from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lictor.application.ports import Clock, EventLog, Executor, Vcs
from lictor.application.usecases.prepare_driver import (
    PrepareDriver,
    PrepareDriverRequest,
    PrepareDriverResult,
)
from lictor.domain.model import (
    AgentResult,
    AgentStatus,
    Event,
    Health,
    PermissionDenial,
    Tokens,
)


def test_prepare_driver_executes_task_and_emits_event(tmp_path: Path) -> None:
    # Setup files
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "test.feature").write_text("Feature: Test\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    vcs.status.return_value = ["tests/acceptance/test_foo.py", "src/foo.py"]

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

    vcs.reset_to_head.assert_called_once()
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
    vcs.status.return_value = [
        "tests/acceptance/test_foo.py",
        "pyproject.toml",  # unauthorized outside paths.driver + paths.writable
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

    vcs.revert_paths.assert_called_once_with(["pyproject.toml"])
    assert result.reverted_files == ("pyproject.toml",)
    assert event_log.append.call_args_list[0].args[0].data["reverted_files"] == ["pyproject.toml"]


def test_prepare_driver_does_not_increment_attempts_on_rate_limit(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "test.feature").write_text("Feature: Test\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    vcs.status.return_value = []

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
    vcs.status.return_value = []

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
    tmp_path: Path, agent: AgentResult, agent_failures: int = 0
) -> tuple[PrepareDriverResult, MagicMock, MagicMock]:
    vcs = MagicMock(spec=Vcs)
    vcs.status.return_value = []
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
    return use_case.execute(request), executor, event_log


@pytest.mark.parametrize(
    ("status", "failures", "expected"),
    [
        ("ok", 1, ("ok", 2, 0)),
        ("error", 0, ("error", 2, 1)),
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
