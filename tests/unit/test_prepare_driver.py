from pathlib import Path
from unittest.mock import MagicMock

from lictor.application.ports import Clock, EventLog, Executor, Vcs
from lictor.application.usecases.prepare_driver import (
    PrepareDriver,
    PrepareDriverRequest,
)
from lictor.domain.model import AgentResult, Event, Health, Tokens


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

