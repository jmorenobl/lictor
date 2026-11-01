from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.usecases.implement_scenario import (
    ImplementScenario,
    ImplementScenarioRequest,
    ImplementScenarioResult,
    _extract_scenario_gherkin,
)
from lictor.domain.errors import CandidateError, SealTamperedError
from lictor.domain.model import (
    AgentResult,
    AgentStatus,
    AgentTask,
    Event,
    PermissionDenial,
    Scenario,
    ScenarioId,
    Tokens,
)


def _execute(
    tmp_path: Path,
    result: AgentResult | MagicMock,
    **overrides: Any,
) -> tuple[ImplementScenarioResult, MagicMock, MagicMock]:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True, exist_ok=True)
    feature_file.write_text("Feature: IVA\n", encoding="utf-8")
    executor = MagicMock(spec=Executor)
    if isinstance(result, AgentResult):
        executor.run.return_value = result
    else:
        executor.run.side_effect = result
    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = True
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:35:00Z"
    use_case = ImplementScenario(
        vcs=overrides.pop("vcs", MagicMock(spec=Vcs)),
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )
    fields: dict[str, Any] = {
        "project_root": tmp_path,
        "run_id": "run",
        "scenario": Scenario(id=ScenarioId("IVA-001"), name="N", file=str(feature_file), line=1),
        "attempt": 1,
        "model": "m",
        "allowed_tools": [],
        "max_turns": 1,
        "timeout_s": 1,
        "paths_writable": ["src/"],
        "paths_protected": [],
        "paths_driver": [],
        "suppressions": [],
        "green_ids": [],
        "check_commands": [],
        "hints": "",
    }
    return (
        use_case.execute(ImplementScenarioRequest(**{**fields, **overrides})),
        executor,
        event_log,
    )


def _events(event_log: MagicMock) -> list[Event]:
    return [call.args[0] for call in event_log.append.call_args_list]


def test_agent_error_keeps_the_attempt_and_counts_the_failure(tmp_path: Path) -> None:
    result, _, _ = _execute(tmp_path, AgentResult(status="error"))

    assert result.agent_signal == "error"
    assert result.attempt == 1
    assert result.agent_failures == 1


def test_second_agent_error_in_a_row_reports_unavailable(tmp_path: Path) -> None:
    result, _, _ = _execute(tmp_path, AgentResult(status="error"), agent_failures=1)

    assert result.agent_signal == "unavailable"
    assert result.attempt == 1


def test_auth_failure_reports_unavailable_at_once(tmp_path: Path) -> None:
    result, _, _ = _execute(tmp_path, AgentResult(status="auth"))

    assert result.agent_signal == "unavailable"
    assert result.attempt == 1
    assert result.agent_failures == 0


@pytest.mark.parametrize("status", ["max_turns", "timeout"])
def test_agent_that_ran_out_uses_up_the_attempt(tmp_path: Path, status: AgentStatus) -> None:
    result, _, _ = _execute(tmp_path, AgentResult(status=status), agent_failures=1)

    assert result.agent_signal == "error"
    assert result.attempt == 2
    assert result.agent_failures == 0


def test_successful_call_clears_the_failures(tmp_path: Path) -> None:
    result, _, _ = _execute(tmp_path, AgentResult(status="ok"), agent_failures=1)

    assert result.agent_signal == "ok"
    assert result.agent_failures == 0


def test_tampering_is_not_a_backend_failure(tmp_path: Path) -> None:
    lictor_dir = tmp_path / ".lictor"
    lictor_dir.mkdir()
    journal = lictor_dir / "journal.jsonl"
    journal.write_text("", encoding="utf-8")

    def tamper(_task: AgentTask) -> AgentResult:
        journal.write_text("forjado\n", encoding="utf-8")
        return AgentResult(status="ok", text="hecho", num_turns=7)

    result, _, _ = _execute(tmp_path, MagicMock(side_effect=tamper), agent_failures=1)

    assert result.agent_signal == "rejected"
    assert result.failed_at == "L0-estado"
    assert ".lictor/journal.jsonl" in (result.error or "")
    assert result.tree is None
    assert result.attempt == 2
    assert result.agent_failures == 0
    assert result.agent_result.status == "ok"
    assert result.agent_result.num_turns == 7
    assert journal.read_text(encoding="utf-8") == "", "se restaura"


def test_failed_call_does_not_overwrite_the_previous_raw_output(tmp_path: Path) -> None:
    _, first, _ = _execute(tmp_path, AgentResult(status="error"))
    _, second, _ = _execute(tmp_path, AgentResult(status="error"), agent_failures=1)

    first_path = first.run.call_args.args[0].raw_path
    second_path = second.run.call_args.args[0].raw_path
    assert first_path.name == "002-implement.json"
    assert second_path.name == "002-implement-r1.json"
    assert first_path.parent.name == "IVA-001"


def test_a_paused_call_does_not_overwrite_the_previous_raw_output(tmp_path: Path) -> None:
    """A rate limit keeps the attempt and resets the failure count: only the disk can tell."""

    def write_raw(task: AgentTask) -> AgentResult:
        task.raw_path.write_text("crudo", encoding="utf-8")
        return AgentResult(status="rate_limited")

    _, first, _ = _execute(tmp_path, MagicMock(side_effect=write_raw))
    _, second, _ = _execute(tmp_path, MagicMock(side_effect=write_raw))

    assert first.run.call_args.args[0].raw_path != second.run.call_args.args[0].raw_path


def test_stderr_of_the_call_reaches_the_journal(tmp_path: Path) -> None:
    path = tmp_path / ".lictor" / "runs" / "x.stderr.log"
    agent = AgentResult(status="error", stderr_path=path)

    _, _, event_log = _execute(tmp_path, agent)

    event = event_log.append.call_args_list[0].args[0]
    assert event.data["stderr"] == ".lictor/runs/x.stderr.log"


def test_journal_has_no_stderr_when_the_call_left_none(tmp_path: Path) -> None:
    _, _, event_log = _execute(tmp_path, AgentResult(status="ok"))

    assert event_log.append.call_args_list[0].args[0].data["stderr"] is None


def test_denials_and_exit_code_reach_the_journal(tmp_path: Path) -> None:
    denial = PermissionDenial(tool="Bash", target="curl -V")
    agent = AgentResult(status="ok", exit_code=0, permission_denials=(denial,))

    _, _, event_log = _execute(tmp_path, agent)

    finished, denied = _events(event_log)
    expected = [{"tool": "Bash", "target": "curl -V"}]
    assert finished.data["permission_denials"] == expected
    assert finished.data["exit_code"] == 0
    assert finished.data["signal"] == "ok"
    assert denied.type == "lictor.agent.denied.v1"
    assert denied.scenarioid == "IVA-001"
    assert denied.data == {"role": "implement", "attempt": 2, "denials": expected}


def test_no_denial_event_without_denials(tmp_path: Path) -> None:
    _, _, event_log = _execute(tmp_path, AgentResult(status="ok"))

    assert [e.type for e in _events(event_log)] == ["lictor.agent.finished.v1"]


@pytest.mark.parametrize(
    ("status", "note"), [("max_turns", "sin turnos"), ("timeout", "sin tiempo")]
)
def test_retry_prompt_says_the_agent_ran_out(
    tmp_path: Path, status: AgentStatus, note: str
) -> None:
    _, executor, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        last_agent_status=status,
        last_error="fallo de L2",
        failed_at="L2",
    )

    prompt = executor.run.call_args.args[0].prompt
    assert note in prompt
    assert "fallo de L2" in prompt


def test_error_block_is_shown_even_if_a_failed_call_kept_the_attempt_at_zero(
    tmp_path: Path,
) -> None:
    _, executor, _ = _execute(
        tmp_path, AgentResult(status="ok"), attempt=0, last_error="fallo de L2"
    )

    assert "fallo de L2" in executor.run.call_args.args[0].prompt


def test_prompt_falls_back_when_the_profile_has_no_commands(tmp_path: Path) -> None:
    _, executor, _ = _execute(tmp_path, AgentResult(status="ok"))

    assert "ejecutando: los tests del proyecto" in executor.run.call_args.args[0].prompt


def test_implement_scenario_seal_tampered_raises_before_the_agent(tmp_path: Path) -> None:
    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = False  # Human edited files!

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)

    use_case = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = ImplementScenarioRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        scenario=Scenario(
            id=ScenarioId("IVA-001"), name="IVA general", file="features/iva.feature", line=5
        ),
        attempt=0,
        model="claude-3-7-sonnet",
        allowed_tools=["Bash(*)"],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_protected=["features/", "tests/acceptance/"],
        paths_driver=["tests/acceptance/"],
        suppressions=["# noqa"],
        green_ids=[],
        check_commands=["check-command"],
        hints="Python 3.12",
    )

    with pytest.raises(SealTamperedError) as exc_info:
        use_case.execute(req)
    assert exc_info.value.exit_code == 10
    vcs.export_copy.assert_not_called()
    executor.run.assert_not_called()


def test_implement_scenario_success(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text(
        """Feature: IVA
  @IVA-001
  Scenario: IVA general
    Given total 100
""",
        encoding="utf-8",
    )

    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    agent_result = AgentResult(
        status="ok",
        num_turns=2,
        duration_ms=800,
        tokens=Tokens(input=50, output=20),
        cost_usd=0.005,
    )
    executor.run.return_value = agent_result

    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = True

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:35:00Z"

    use_case = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = ImplementScenarioRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        scenario=Scenario(
            id=ScenarioId("IVA-001"), name="IVA general", file=str(feature_file), line=3
        ),
        attempt=0,
        model="claude-3-7-sonnet",
        allowed_tools=["Bash(*)"],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_protected=["features/", "tests/acceptance/"],
        paths_driver=["tests/acceptance/"],
        suppressions=["# noqa"],
        green_ids=[],
        check_commands=["check-command"],
        hints="Python 3.12",
    )

    result = use_case.execute(req)

    vcs.export_copy.assert_called_once()
    assert vcs.export_copy.call_args.args[0] == "HEAD"
    workspace = executor.run.call_args.args[0].cwd
    assert workspace == vcs.export_copy.call_args.args[1]
    assert not workspace.is_relative_to(tmp_path)
    assert workspace.name == tmp_path.name
    vcs.capture.assert_called_once_with(workspace)
    assert result.tree == vcs.capture.return_value
    assert result.attempt == 1
    assert result.agent_signal == "ok"
    assert result.agent_result.status == "ok"

    event_log.append.assert_called_once()
    event = event_log.append.call_args[0][0]
    assert isinstance(event, Event)
    assert event.type == "lictor.agent.finished.v1"
    assert event.scenarioid == "IVA-001"
    assert event.data["role"] == "implement"
    assert event.data["attempt"] == 1


def test_implement_scenario_spec_conflict(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text("Feature: IVA\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(
        status="ok",
        spec_conflict="Contradicción con IVA-002",
    )

    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = True

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:35:00Z"

    use_case = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = ImplementScenarioRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        scenario=Scenario(
            id=ScenarioId("IVA-001"), name="IVA general", file=str(feature_file), line=1
        ),
        attempt=1,
        model="claude-3-7-sonnet",
        allowed_tools=[],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_protected=["features/"],
        paths_driver=["tests/acceptance/"],
        suppressions=[],
        green_ids=[],
        check_commands=[],
        hints="",
        last_error="Assertion failed",
        failed_at="L2 (unit)",
    )

    result = use_case.execute(req)
    assert result.attempt == 2
    assert result.agent_signal == "spec_conflict"


def test_implement_scenario_rate_limited_does_not_increment_attempt(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text("Feature: IVA\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="rate_limited")

    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = True

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:35:00Z"

    use_case = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = ImplementScenarioRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        scenario=Scenario(
            id=ScenarioId("IVA-001"), name="IVA general", file=str(feature_file), line=1
        ),
        attempt=1,
        model="claude-3-7-sonnet",
        allowed_tools=[],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_protected=["features/"],
        paths_driver=["tests/acceptance/"],
        suppressions=[],
        green_ids=[],
        check_commands=[],
        hints="",
    )

    result = use_case.execute(req)
    assert result.attempt == 1  # Not incremented on rate_limited!
    assert result.agent_signal == "rate_limited"


def test_extract_scenario_gherkin_includes_background_and_handles_comments(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text(
        """# language: es
Característica: Facturación
  Antecedentes:
    Dado una tasa de IVA del 21%
    Y un emisor registrado

  # Comentario importante 1
  # Comentario importante 2
  @IVA-001
  # Comentario entre tag y escenario
  Escenario: IVA general
    Dado una factura con importe 100
    Cuando calculo el total
    Entonces el total es 121

  @IVA-002
  Escenario: IVA reducido
    Dado una factura con importe 100
""",
        encoding="utf-8",
    )

    scenario = Scenario(
        id=ScenarioId("IVA-001"),
        name="IVA general",
        file=str(feature_file),
        line=11,  # Line of Escenario: IVA general
    )

    extracted = _extract_scenario_gherkin(tmp_path, scenario)
    assert "Antecedentes:" in extracted
    assert "Dado una tasa de IVA del 21%" in extracted
    assert "@IVA-001" in extracted
    assert "Escenario: IVA general" in extracted
    assert "Entonces el total es 121" in extracted
    assert "@IVA-002" not in extracted
    assert "Escenario: IVA reducido" not in extracted


def test_implement_scenario_handles_curly_braces_in_hints_and_errors(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text("Feature: IVA\n  @IVA-001\n  Scenario: Foo\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="ok")

    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = True

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:35:00Z"

    use_case = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    template = (
        "{repo} {id} {gherkin_del_escenario} {writable} {protected} "
        "{suppressions} {green_ids} {check_commands} {hints} "
        "{bloque_de_error} literal {'literal_token': 1}"
    )

    req = ImplementScenarioRequest(
        project_root=tmp_path,
        run_id="run1",
        scenario=Scenario(id=ScenarioId("IVA-001"), name="Foo", file=str(feature_file), line=3),
        attempt=1,
        model="fake",
        allowed_tools=[],
        max_turns=1,
        timeout_s=1,
        paths_writable=["src/"],
        paths_protected=[],
        paths_driver=[],
        suppressions=[],
        green_ids=[],
        check_commands=[],
        hints="Check this dict: {'a': 1, 'b': 2}",
        last_error="Crash in format {bad_key}: 404",
        prompt_template=template,
    )

    result = use_case.execute(req)
    assert result.agent_signal == "ok"


def test_a_result_that_cant_be_read_fails_the_attempt(tmp_path: Path) -> None:
    vcs = MagicMock(spec=Vcs)
    vcs.capture.side_effect = CandidateError("repositorio anidado")
    result, _, _ = _execute(tmp_path, AgentResult(status="ok"), vcs=vcs)

    assert result.agent_signal == "rejected"
    assert result.failed_at == "L0-rutas"
    assert result.error == "repositorio anidado"
    assert result.tree is None
    assert result.attempt == 2


@pytest.mark.parametrize(
    "agent_result",
    [AgentResult(status="rate_limited"), AgentResult(status="ok", spec_conflict="contradice")],
    ids=["cuota", "conflicto"],
)
def test_no_result_is_read_from_a_call_that_produced_none(
    tmp_path: Path, agent_result: AgentResult
) -> None:
    vcs = MagicMock(spec=Vcs)
    result, _, _ = _execute(tmp_path, agent_result, vcs=vcs)

    vcs.capture.assert_not_called()
    assert result.tree is None
