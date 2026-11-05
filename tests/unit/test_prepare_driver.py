from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from lictor.application.ports import Clock, EventLog, Executor, Vcs
from lictor.application.usecases.prepare_driver import (
    PrepareDriver,
    PrepareDriverRequest,
    PrepareDriverResult,
)
from lictor.domain.errors import CandidateError
from lictor.domain.model import (
    AgentResult,
    AgentStatus,
    AgentTask,
    Event,
    Health,
    PermissionDenial,
    Tokens,
    TreeChange,
)


def _changed(path: str) -> TreeChange:
    return TreeChange(path, "100644")


def _driver_request(tmp_path: Path) -> PrepareDriverRequest:
    return PrepareDriverRequest(
        project_root=tmp_path,
        run_id="R",
        model="m",
        allowed_tools=[],
        max_turns=1,
        timeout_s=1,
        paths_writable=["src/"],
        paths_driver=["tests/acceptance/"],
        paths_protected=[],
        driver_hints="",
        hints="",
        green_ids=[],
        driver_attempts=0,
    )


def test_prepare_driver_executes_task_and_emits_event(tmp_path: Path) -> None:
    # Setup files
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "test.feature").write_text("Feature: Test\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    vcs.changes.return_value = [_changed("tests/acceptance/test_foo.py"), _changed("src/foo.py")]

    executor = MagicMock(spec=Executor)
    agent_result = AgentResult(
        status="ok",
        num_turns=3,
        duration_ms=1200,
        tokens=Tokens(input=100, output=50),
        cost_usd=0.01,
    )
    executor.run.return_value = agent_result
    executor.health.return_value = Health(ok=True, cli_version="1.0", auth_status="ok")

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:30:00Z"

    use_case = PrepareDriver(
        vcs=vcs,
        executor=executor,
        event_log=event_log,
        clock=clock,
    )

    req = PrepareDriverRequest(
        project_root=tmp_path,
        run_id="20261030-143000-abcd",
        model="claude-3-7-sonnet",
        allowed_tools=["Bash(*)"],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_driver=["tests/acceptance/"],
        paths_protected=["features/", "tests/acceptance/"],
        driver_hints="Usa el perfil asignado.",
        hints="Python 3.12",
        green_ids=[],
        driver_attempts=0,
    )

    result = use_case.execute(req)

    executor.run.assert_called_once()
    task = executor.run.call_args[0][0]
    assert task.role == "driver"
    assert "Feature: Test" in task.prompt
    assert task.prompt_sha.startswith("sha256:")

    assert result.driver_attempts == 1
    assert result.agent_result.status == "ok"

    event_log.append.assert_called_once()
    event = event_log.append.call_args[0][0]
    assert isinstance(event, Event)
    assert event.type == "lictor.agent.finished.v1"
    assert event.data["role"] == "driver"
    assert event.data["status"] == "ok"


def test_prepare_driver_reverts_unauthorized_paths(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "test.feature").write_text("Feature: Test\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    # Status includes unauthorized file modified by agent
    vcs.changes.return_value = [
        _changed("tests/acceptance/test_foo.py"),
        _changed("pyproject.toml"),
    ]

    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="ok")

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:30:00Z"

    use_case = PrepareDriver(
        vcs=vcs,
        executor=executor,
        event_log=event_log,
        clock=clock,
    )

    req = PrepareDriverRequest(
        project_root=tmp_path,
        run_id="20261030-143000-abcd",
        model="claude-3-7-sonnet",
        allowed_tools=[],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_driver=["tests/acceptance/"],
        paths_protected=["features/", "tests/acceptance/"],
        driver_hints="",
        hints="",
        green_ids=[],
        driver_attempts=1,
        last_error="Error anterior",
    )

    result = use_case.execute(req)

    workspace = executor.run.call_args.args[0].cwd
    assert vcs.capture.call_args_list == [call(workspace), call(workspace, skip=["pyproject.toml"])]
    assert result.reverted_files == ("pyproject.toml",)
    assert event_log.append.call_args_list[0].args[0].data["reverted_files"] == ["pyproject.toml"]


def test_prepare_driver_does_not_increment_attempts_on_rate_limit(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "test.feature").write_text("Feature: Test\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    vcs.changes.return_value = []

    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="rate_limited")

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:30:00Z"

    use_case = PrepareDriver(
        vcs=vcs,
        executor=executor,
        event_log=event_log,
        clock=clock,
    )

    req = PrepareDriverRequest(
        project_root=tmp_path,
        run_id="run1",
        model="fake",
        allowed_tools=[],
        max_turns=1,
        timeout_s=1,
        paths_writable=["src/"],
        paths_driver=["tests/acceptance/"],
        paths_protected=[],
        driver_hints="",
        hints="",
        green_ids=[],
        driver_attempts=0,
    )

    result = use_case.execute(req)
    # Rate limit should NOT burn an attempt!
    assert result.driver_attempts == 0
    assert result.agent_result.status == "rate_limited"


def test_prepare_driver_handles_curly_braces_in_hints_and_templates(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "test.feature").write_text("Feature: Test\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    vcs.changes.return_value = []

    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="ok")

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:30:00Z"

    use_case = PrepareDriver(
        vcs=vcs,
        executor=executor,
        event_log=event_log,
        clock=clock,
    )

    req = PrepareDriverRequest(
        project_root=tmp_path,
        run_id="run1",
        model="fake",
        allowed_tools=[],
        max_turns=1,
        timeout_s=1,
        paths_writable=["src/"],
        paths_driver=["tests/acceptance/"],
        paths_protected=[],
        driver_hints="JSON snippet: {'key': 'val'}",
        hints="Regex pattern: {foo}",
        green_ids=[],
        driver_attempts=0,
        last_error="Crash in dict {'a': 1}",
        prompt_template=(
            "{repo} {features} {driver_paths} {writable} {protected_sin_driver} "
            "{green_ids} {driver_hints} {hints} {bloque_de_error} literal {'x': 1}"
        ),
    )

    result = use_case.execute(req)
    assert result.agent_result.status == "ok"


def _execute_driver(
    tmp_path: Path,
    agent: AgentResult,
    agent_failures: int = 0,
    moved: tuple[str, ...] = (),
    **overrides: Any,
) -> tuple[PrepareDriverResult, MagicMock, MagicMock]:
    vcs = MagicMock(spec=Vcs)
    vcs.changes.return_value = []
    vcs.restore.return_value = list(moved)
    executor = MagicMock(spec=Executor)
    executor.run.return_value = agent
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:30:00Z"
    use_case = PrepareDriver(vcs=vcs, executor=executor, event_log=event_log, clock=clock)
    request = PrepareDriverRequest(
        project_root=tmp_path,
        run_id="run1",
        model="fake",
        allowed_tools=[],
        max_turns=1,
        timeout_s=1,
        paths_writable=["src/"],
        paths_driver=["tests/acceptance/"],
        paths_protected=[],
        driver_hints="",
        hints="",
        green_ids=[],
        driver_attempts=1,
        agent_failures=agent_failures,
    )
    return use_case.execute(replace(request, **overrides)), executor, event_log


@pytest.mark.parametrize(
    ("status", "failures", "expected"),
    [
        ("ok", 1, ("ok", 2, 0)),
        ("error", 0, ("error", 1, 1)),
        ("max_turns", 0, ("error", 2, 0)),
        ("timeout", 1, ("error", 2, 0)),
        ("error", 1, ("unavailable", 1, 2)),
        ("auth", 0, ("unavailable", 1, 0)),
        ("rate_limited", 0, ("rate_limited", 1, 0)),
    ],
)
def test_prepare_driver_signal(
    tmp_path: Path, status: AgentStatus, failures: int, expected: tuple[str, int, int]
) -> None:
    result, _, _ = _execute_driver(tmp_path, AgentResult(status=status), failures)

    assert (result.agent_signal, result.driver_attempts, result.agent_failures) == expected


def test_prepare_driver_tampering_with_the_repository_is_rejected(tmp_path: Path) -> None:
    result, _, event_log = _execute_driver(
        tmp_path, AgentResult(status="ok"), moved=(".git/config",)
    )

    assert (result.agent_signal, result.driver_attempts) == ("rejected", 2)
    assert result.tree is None
    assert ".git/config" in (result.error or "")
    types = [call_.args[0].type for call_ in event_log.append.call_args_list]
    assert "lictor.state.tampered.v1" in types


def test_prepare_driver_retry_keeps_the_previous_raw_output(tmp_path: Path) -> None:
    _, executor, _ = _execute_driver(tmp_path, AgentResult(status="error"), 1)

    raw_path = executor.run.call_args.args[0].raw_path
    assert raw_path.name == "002-driver-r1.json"
    assert raw_path.parent.name == "driver"


def test_prepare_driver_records_denials(tmp_path: Path) -> None:
    denial = PermissionDenial(tool="Bash", target="curl -V")
    agent = AgentResult(status="ok", exit_code=0, permission_denials=(denial,))

    _, _, event_log = _execute_driver(tmp_path, agent)

    finished, denied = [call.args[0] for call in event_log.append.call_args_list]
    assert finished.data["exit_code"] == 0
    assert finished.data["signal"] == "ok"
    assert denied.type == "lictor.agent.denied.v1"
    assert denied.scenarioid is None
    assert denied.data["role"] == "driver"


def test_prepare_driver_reverts_forbidden_paths_in_project_code(tmp_path: Path) -> None:
    (tmp_path / "features").mkdir()
    vcs = MagicMock(spec=Vcs)
    vcs.changes.return_value = [
        _changed("src/evil.pth"),
        _changed("tests/acceptance/conftest.py"),
        _changed("tests/acceptance/CLAUDE.md"),
        _changed("tests/acceptance/.claude/conftest.py"),
        _changed("src/iva.py"),
    ]
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="ok")
    use_case = PrepareDriver(
        vcs=vcs, executor=executor, event_log=MagicMock(spec=EventLog), clock=MagicMock(spec=Clock)
    )

    result = use_case.execute(
        PrepareDriverRequest(
            project_root=tmp_path,
            run_id="R",
            model="m",
            allowed_tools=[],
            max_turns=1,
            timeout_s=1,
            paths_writable=["src/"],
            paths_driver=["tests/acceptance/"],
            paths_protected=[],
            driver_hints="",
            hints="",
            green_ids=[],
            driver_attempts=0,
            forbidden_paths=["*.pth", "conftest.py", "CLAUDE.md", ".claude"],
            driver_exempt_paths=["conftest.py"],
        )
    )

    # In its own paths it keeps only what the profile exempts, and only where nothing else matches.
    assert result.reverted_files == (
        "src/evil.pth",
        "tests/acceptance/CLAUDE.md",
        "tests/acceptance/.claude/conftest.py",
    )


def test_prepare_driver_discards_links_even_in_its_own_paths(tmp_path: Path) -> None:
    (tmp_path / "features").mkdir()
    vcs = MagicMock(spec=Vcs)
    vcs.changes.return_value = [TreeChange("tests/acceptance/enlace.py", "120000")]
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="ok")
    use_case = PrepareDriver(
        vcs=vcs, executor=executor, event_log=MagicMock(spec=EventLog), clock=MagicMock(spec=Clock)
    )

    result = use_case.execute(_driver_request(tmp_path))

    assert result.reverted_files == ("tests/acceptance/enlace.py",)


def test_prepare_driver_with_an_unreadable_result_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "features").mkdir()
    vcs = MagicMock(spec=Vcs)
    vcs.capture.side_effect = CandidateError("repositorio anidado")
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="ok")
    use_case = PrepareDriver(
        vcs=vcs, executor=executor, event_log=MagicMock(spec=EventLog), clock=MagicMock(spec=Clock)
    )

    result = use_case.execute(_driver_request(tmp_path))

    assert result.agent_signal == "rejected"
    assert result.error == "repositorio anidado"
    assert result.driver_attempts == 1


def test_prepare_driver_reads_nothing_after_a_rate_limit(tmp_path: Path) -> None:
    (tmp_path / "features").mkdir()
    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="rate_limited")
    use_case = PrepareDriver(
        vcs=vcs, executor=executor, event_log=MagicMock(spec=EventLog), clock=MagicMock(spec=Clock)
    )

    result = use_case.execute(_driver_request(tmp_path))

    vcs.capture.assert_not_called()
    assert result.tree is None


def _prompt(executor: MagicMock) -> str:
    task: AgentTask = executor.run.call_args.args[0]
    return task.prompt


def test_driver_prompt_names_greens_and_reopened_scenarios(tmp_path: Path) -> None:
    _, executor, _ = _execute_driver(
        tmp_path, AgentResult(status="ok"), green_ids=["IVA-002"], reopened_ids=["IVA-001"]
    )

    lines = _prompt(executor).splitlines()
    assert any("IVA-002 ya funcionan" in line for line in lines)
    reopened = [line for line in lines if "cambiaron desde que se implementaron" in line]
    assert reopened == [
        "- Los escenarios IVA-001 cambiaron desde que se implementaron: "
        "actualiza sus tests para que comprueben lo que piden ahora."
    ]


def test_driver_prompt_without_reopened_scenarios_says_nothing_of_them(tmp_path: Path) -> None:
    _, executor, _ = _execute_driver(tmp_path, AgentResult(status="ok"))
    prompt = _prompt(executor)
    assert "cambiaron desde" not in prompt
    assert "{reabiertos}" not in prompt


def test_driver_cli_version_comes_from_the_call_not_from_a_health_check(tmp_path: Path) -> None:
    _, executor, event_log = _execute_driver(
        tmp_path, AgentResult(status="ok", cli_version="2.1.316")
    )

    finished = [
        call_.args[0]
        for call_ in event_log.append.call_args_list
        if call_.args[0].type == "lictor.agent.finished.v1"
    ]
    assert [e.data["cli_version"] for e in finished] == ["2.1.316"]
    executor.health.assert_not_called()


def test_driver_prompt_is_saved_beside_the_raw_output(tmp_path: Path) -> None:
    _, executor, event_log = _execute_driver(tmp_path, AgentResult(status="ok"))

    task = executor.run.call_args.args[0]
    prompt_file = task.raw_path.with_suffix(".prompt.md")
    assert prompt_file.read_text(encoding="utf-8") == task.prompt
    (finished,) = [
        call_.args[0]
        for call_ in event_log.append.call_args_list
        if call_.args[0].type == "lictor.agent.finished.v1"
    ]
    assert finished.data["prompt"]["path"] == prompt_file.relative_to(tmp_path).as_posix()
    assert prompt_file.name == task.raw_path.name.replace(".json", ".prompt.md")
