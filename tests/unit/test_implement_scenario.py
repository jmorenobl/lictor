from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.seal import hash_file
from lictor.application.usecases.implement_scenario import (
    ImplementScenario,
    ImplementScenarioRequest,
    ImplementScenarioResult,
)
from lictor.domain.api import IMPLEMENTER_API
from lictor.domain.controls import CommandControl, ControlMode, ReaderControl
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

SCENARIO_TEXT = "@IVA-001\nEscenario: IVA general\n  Entonces el total es 121"


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
    spec_source.scenario_text.return_value = SCENARIO_TEXT
    spec_source.verify_seal.return_value = True
    event_log = overrides.pop("event_log", MagicMock(spec=EventLog))
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

    event = event_log.append.call_args_list[1].args[0]
    assert event.data["stderr"] == ".lictor/runs/x.stderr.log"


def test_journal_has_no_stderr_when_the_call_left_none(tmp_path: Path) -> None:
    _, _, event_log = _execute(tmp_path, AgentResult(status="ok"))

    assert event_log.append.call_args_list[1].args[0].data["stderr"] is None


def test_denials_and_exit_code_reach_the_journal(tmp_path: Path) -> None:
    denial = PermissionDenial(tool="Bash", target="curl -V")
    agent = AgentResult(status="ok", exit_code=0, permission_denials=(denial,))

    _, _, event_log = _execute(tmp_path, agent)

    _, finished, denied = _events(event_log)
    expected = [{"tool": "Bash", "target": "curl -V"}]
    assert finished.data["permission_denials"] == expected
    assert finished.data["exit_code"] == 0
    assert finished.data["signal"] == "ok"
    assert denied.type == "lictor.agent.denied.v1"
    assert denied.scenarioid == "IVA-001"
    assert denied.data == {"role": "implement", "attempt": 2, "denials": expected}


def test_no_denial_event_without_denials(tmp_path: Path) -> None:
    _, _, event_log = _execute(tmp_path, AgentResult(status="ok"))

    assert [e.type for e in _events(event_log)] == [
        "lictor.agent.started.v1",
        "lictor.agent.finished.v1",
    ]


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
    spec_source.scenario_text.return_value = SCENARIO_TEXT
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
    spec_source.scenario_text.return_value = SCENARIO_TEXT
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

    assert event_log.append.call_count == 2
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
    spec_source.scenario_text.return_value = SCENARIO_TEXT
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

    vcs.capture.return_value = "c" * 40

    result = use_case.execute(req)
    assert result.attempt == 2
    assert result.agent_signal == "spec_conflict"
    # F54: the tree of a declared conflict is kept, so verify judges it and not the text.
    assert result.tree == "c" * 40
    vcs.capture.assert_called_once()


def test_implement_scenario_rate_limited_does_not_increment_attempt(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text("Feature: IVA\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="rate_limited")

    spec_source = MagicMock(spec=SpecSource)
    spec_source.scenario_text.return_value = SCENARIO_TEXT
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


def test_implement_scenario_handles_curly_braces_in_hints_and_errors(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text("Feature: IVA\n  @IVA-001\n  Scenario: Foo\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="ok")

    spec_source = MagicMock(spec=SpecSource)
    spec_source.scenario_text.return_value = SCENARIO_TEXT
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


def test_no_result_is_read_from_a_call_that_produced_none(tmp_path: Path) -> None:
    vcs = MagicMock(spec=Vcs)
    result, _, _ = _execute(tmp_path, AgentResult(status="rate_limited"), vcs=vcs)

    vcs.capture.assert_not_called()
    assert result.tree is None


def test_a_declared_conflict_whose_copy_cant_be_read_stays_a_conflict(tmp_path: Path) -> None:
    # Nothing to verify: the conflict says more than an unreadable copy, and routing quarantines.
    vcs = MagicMock(spec=Vcs)
    vcs.capture.side_effect = CandidateError("repositorio anidado")
    agent_result = AgentResult(status="ok", spec_conflict="contradice")
    result, _, _ = _execute(tmp_path, agent_result, vcs=vcs)

    assert result.agent_signal == "spec_conflict"
    assert result.tree is None


def test_cli_version_comes_from_the_call_not_from_a_health_check(tmp_path: Path) -> None:
    _, executor, event_log = _execute(tmp_path, AgentResult(status="ok", cli_version="2.1.316"))

    finished = [e for e in _events(event_log) if e.type == "lictor.agent.finished.v1"]
    assert [e.data["cli_version"] for e in finished] == ["2.1.316"]
    executor.health.assert_not_called()


def test_the_prompt_carries_the_scenario_text_from_the_spec_source(tmp_path: Path) -> None:
    _, executor, _ = _execute(tmp_path, AgentResult(status="ok"))

    task = executor.run.call_args.args[0]
    assert SCENARIO_TEXT in task.prompt


def test_the_prompt_is_saved_beside_the_raw_output_before_the_call(tmp_path: Path) -> None:
    """The CLI gets the prompt by argv: without this file nothing tells what it was asked."""
    seen: list[str] = []

    def run(task: AgentTask) -> AgentResult:
        seen.append(task.raw_path.with_suffix(".prompt.md").read_text(encoding="utf-8"))
        return AgentResult(status="ok")

    _, executor, event_log = _execute(tmp_path, MagicMock(side_effect=run))

    task = executor.run.call_args.args[0]
    assert seen == [task.prompt]
    (finished,) = [e for e in _events(event_log) if e.type == "lictor.agent.finished.v1"]
    prompt_file = task.raw_path.with_suffix(".prompt.md")
    assert finished.data["prompt"] == {
        "path": prompt_file.relative_to(tmp_path).as_posix(),
        "hash": hash_file(prompt_file),
    }
    assert prompt_file.name == "002-implement.prompt.md"


def test_the_start_of_the_call_is_in_the_journal_before_the_agent_runs(tmp_path: Path) -> None:
    """Whoever watches the run learns what it waits for, and the snapshot covers that line."""
    event_log = MagicMock(spec=EventLog)
    journal: list[list[str]] = []

    def run(task: AgentTask) -> AgentResult:
        journal.append([e.type for e in _events(event_log)])
        return AgentResult(status="ok")

    _execute(tmp_path, MagicMock(side_effect=run), max_attempts=3, event_log=event_log)

    assert journal == [["lictor.agent.started.v1"]]
    started, finished = _events(event_log)[:2]
    assert (started.scenarioid, started.data["attempt"], started.data["max_attempts"]) == (
        "IVA-001",
        2,
        3,
    )
    assert started.data["prompt"] == finished.data["prompt"]


def test_prompt_names_the_only_commands_bash_admits(tmp_path: Path) -> None:
    """F37: the implementer tried an interpreter through Bash to rewrite a file, and was denied."""
    _, executor, _ = _execute(tmp_path, AgentResult(status="ok"), check_commands=["make test"])

    prompt = executor.run.call_args.args[0].prompt
    assert "Bash solo admite: make test." in prompt
    assert "un intérprete o un script" in prompt
    assert "Lee con Read, Grep y Glob" in prompt


_COVERAGE = ReaderControl(
    "coverage",
    "L2",
    reads="unit",
    adds="--cov {out}",
    min_score=80,
    when_plugin="cov.plugin",
    agent_run="tests --cov --table",
)


def test_the_prompt_tells_each_minimum_and_lets_the_agent_measure_it(tmp_path: Path) -> None:
    # F55: four of nine verifications failed on a coverage the agent couldn't measure.
    _, executor, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        check_commands=["tests"],
        controls=[_COVERAGE],
        installed_plugins=["cov.plugin"],
    )

    prompt = executor.run.call_args.args[0].prompt
    assert "  - coverage: al menos 80 %, con `tests --cov --table`." in prompt
    assert "Bash solo admite: tests, tests --cov --table." in prompt


def test_a_minimum_measured_with_a_profile_module_gets_the_profiles_files(
    tmp_path: Path,
) -> None:
    """F71: the coverage command names the profile's module, which the call needs next to it."""
    tool = tmp_path / "cobertura.py"
    tool.write_text("", encoding="utf-8")
    module = replace(_COVERAGE, agent_run="uv run python {lictor_files}/cobertura.py tests")

    _, executor, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        check_commands=["tests"],
        controls=[module],
        installed_plugins=["cov.plugin"],
        agent_files=[tool],
        allowed_tools=["Bash(uv run python {lictor_files}/cobertura.py:*)"],
    )

    task = executor.run.call_args.args[0]
    [rule] = task.allowed_tools
    files = rule.removeprefix("Bash(uv run python ").removesuffix("/cobertura.py:*)")
    assert f"  - coverage: al menos 80 %, con `uv run python {files}/cobertura.py tests`." in (
        task.prompt
    )
    assert "{lictor_files}" not in task.prompt


def test_a_minimum_the_run_skips_is_not_told(tmp_path: Path) -> None:
    _, executor, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        check_commands=["tests"],
        controls=[_COVERAGE],
        installed_plugins=[],
    )

    prompt = executor.run.call_args.args[0].prompt
    assert "puntuación mínima" not in prompt
    assert "tests --cov" not in prompt
    assert "{medidas}" not in prompt


def _complexity(mode: ControlMode) -> CommandControl:
    return CommandControl(
        id="complexity",
        level="L1",
        run="medir {out}",
        report="complexity",
        mode=mode,
        limit=10,
        agent_run="uv run python {lictor_files}/medir.py src",
    )


def test_a_complexity_control_that_measures_is_not_the_implementers(tmp_path: Path) -> None:
    """ADR-0013, point 4: blocking the implementer would repeat F55 for the refactor's work."""
    tool = tmp_path / "medir.py"
    tool.write_text("", encoding="utf-8")

    _, executor, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        controls=[_complexity("measure")],
        agent_files=[tool],
        allowed_tools=["Bash(uv run python {lictor_files}/medir.py:*)"],
    )

    task = executor.run.call_args.args[0]
    assert "complejidad" not in task.prompt
    assert list(task.allowed_tools) == []


def test_one_that_blocks_tells_the_implementer_its_limit_and_how_to_measure(
    tmp_path: Path,
) -> None:
    tool = tmp_path / "medir.py"
    tool.write_text("", encoding="utf-8")

    _, executor, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        check_commands=["tests"],
        controls=[_complexity("block")],
        agent_files=[tool],
        allowed_tools=["Bash(uv run python {lictor_files}/medir.py:*)"],
    )

    task = executor.run.call_args.args[0]
    assert "ninguna función supere una complejidad ciclomática de 10. Mídela con `" in task.prompt
    [rule] = task.allowed_tools
    assert "{lictor_files}" not in rule
    assert "Bash solo admite: tests, uv run python " in task.prompt


def _duplication(mode: ControlMode) -> CommandControl:
    return CommandControl(
        id="duplication",
        level="L1",
        run="repetido {out}",
        report="duplication",
        mode=mode,
        agent_run="uv run python {lictor_files}/repetido.py src",
    )


@pytest.mark.parametrize(("mode", "told"), [("measure", False), ("block", True)])
def test_the_implementer_hears_of_duplication_only_when_it_blocks(
    tmp_path: Path, mode: ControlMode, told: bool
) -> None:
    """ADR-0014, point 4, as ADR-0013 for complexity."""
    tool = tmp_path / "repetido.py"
    tool.write_text("", encoding="utf-8")

    _, executor, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        controls=[_duplication(mode)],
        agent_files=[tool],
        allowed_tools=["Bash(uv run python {lictor_files}/repetido.py:*)"],
    )

    prompt = executor.run.call_args.args[0].prompt
    assert ("no haya código repetido" in prompt) is told
    assert ("/repetido.py src`, que dice en su primera línea" in prompt) is told


_API_CONTROL = CommandControl(
    id="api", level="L1", run="api src --out {out}", report="api", mode="measure"
)


@pytest.mark.parametrize("mode", ["measure", "block"])
def test_the_implementer_always_hears_not_to_break_the_public_api(
    tmp_path: Path, mode: ControlMode
) -> None:
    """ADR-0017: in either mode, since what breaks goes to the report."""
    _, executor, _ = _execute(
        tmp_path, AgentResult(status="ok"), controls=[replace(_API_CONTROL, mode=mode)]
    )

    assert IMPLEMENTER_API in executor.run.call_args.args[0].prompt


def test_without_an_api_control_the_implementer_hears_nothing_of_it(tmp_path: Path) -> None:
    _, executor, _ = _execute(tmp_path, AgentResult(status="ok"), controls=[])

    assert "API pública" not in executor.run.call_args.args[0].prompt
