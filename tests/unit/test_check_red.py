from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lictor.application.ports import Clock, CommandRunner, EventLog, ResultReader, SpecSource, Vcs
from lictor.application.usecases.check_red import CheckRed, CheckRedRequest
from lictor.domain.errors import DriverError
from lictor.domain.model import (
    CommandOutput,
    ControlResult,
    Event,
    ScenarioId,
    TestCaseResult,
)


def test_check_red_fresh_driver_success(tmp_path: Path) -> None:
    runner = MagicMock(spec=CommandRunner)
    runner.run.return_value = CommandOutput(
        returncode=1, stdout="tests output", stderr="", duration_ms=500
    )

    reader = MagicMock(spec=ResultReader)
    # 2 scenarios: IVA-001 (already green, passes), IVA-002 (pending, fails)
    tc1 = TestCaseResult(name="test_iva_001", status="passed")
    tc2 = TestCaseResult(name="test_iva_002", status="failure", message="assert 1 == 2")
    reader.read.return_value = ControlResult(ok=False, test_cases=(tc1, tc2), duration_ms=500)

    vcs = MagicMock(spec=Vcs)
    vcs.get_trailers.return_value = ["IVA-001"]
    vcs.commit.return_value = "driver_commit_sha"

    spec_source = MagicMock(spec=SpecSource)
    spec_source.write_seal.return_value = {
        "created": "2026-10-30T14:00:00Z",
        "run_id": "20261030-140000-abcd",
        "files": {"features/iva.feature": "sha256:123"},
    }

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:00:00Z"

    use_case = CheckRed(
        runner=runner,
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = CheckRedRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        acceptance_run="acceptance-runner --junitxml {out}",
        acceptance_report="junit",
        acceptance_timeout_s=600,
        driver_fresh=True,
        driver_attempts=1,
        max_driver_attempts=2,
        driver_paths=["tests/acceptance/"],
        all_scenario_ids=[ScenarioId("IVA-001"), ScenarioId("IVA-002")],
    )

    result = use_case.execute(req)

    assert result.ok is True
    assert result.scenario_status == {"IVA-001": "green", "IVA-002": "pending"}
    spec_source.write_seal.assert_called_once()
    vcs.commit.assert_called_once_with(message="chore(lictor): driver de aceptación")

    event_log.append.assert_called_once()
    event = event_log.append.call_args[0][0]
    assert isinstance(event, Event)
    assert event.type == "lictor.driver.sealed.v1"
    assert event.data["commit"] == "driver_commit_sha"


def test_check_red_fresh_driver_retry_when_under_max_attempts(tmp_path: Path) -> None:
    runner = MagicMock(spec=CommandRunner)
    runner.run.return_value = CommandOutput(returncode=0, stdout="", stderr="")

    reader = MagicMock(spec=ResultReader)
    # Both pass (IVA-002 should have failed, so red rule fails!)
    tc1 = TestCaseResult(name="test_iva_001", status="passed")
    tc2 = TestCaseResult(name="test_iva_002", status="passed")
    reader.read.return_value = ControlResult(ok=True, test_cases=(tc1, tc2))

    vcs = MagicMock(spec=Vcs)
    vcs.get_trailers.return_value = ["IVA-001"]
    spec_source = MagicMock(spec=SpecSource)
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)

    use_case = CheckRed(
        runner=runner,
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = CheckRedRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        acceptance_run="acceptance-runner --junitxml {out}",
        driver_fresh=True,
        driver_attempts=1,
        max_driver_attempts=2,
        driver_paths=["tests/acceptance/"],
        all_scenario_ids=[ScenarioId("IVA-001"), ScenarioId("IVA-002")],
    )

    result = use_case.execute(req)
    assert result.ok is False
    assert result.last_error is not None
    spec_source.write_seal.assert_not_called()


def test_check_red_fresh_driver_raises_when_attempts_exhausted(tmp_path: Path) -> None:
    runner = MagicMock(spec=CommandRunner)
    runner.run.return_value = CommandOutput(returncode=0, stdout="", stderr="")

    reader = MagicMock(spec=ResultReader)
    tc1 = TestCaseResult(name="test_iva_001", status="passed")
    tc2 = TestCaseResult(name="test_iva_002", status="passed")
    reader.read.return_value = ControlResult(ok=True, test_cases=(tc1, tc2))

    vcs = MagicMock(spec=Vcs)
    vcs.get_trailers.return_value = ["IVA-001"]
    spec_source = MagicMock(spec=SpecSource)
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)

    use_case = CheckRed(
        runner=runner,
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = CheckRedRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        acceptance_run="acceptance-runner --junitxml {out}",
        driver_fresh=True,
        driver_attempts=2,
        max_driver_attempts=2,
        driver_paths=["tests/acceptance/"],
        all_scenario_ids=[ScenarioId("IVA-001"), ScenarioId("IVA-002")],
    )

    with pytest.raises(DriverError) as exc_info:
        use_case.execute(req)
    assert exc_info.value.exit_code == 10


def test_check_red_idempotency_after_crash_with_clean_tree(tmp_path: Path) -> None:
    runner = MagicMock(spec=CommandRunner)
    runner.run.return_value = CommandOutput(returncode=1, stdout="out", stderr="")

    reader = MagicMock(spec=ResultReader)
    tc = TestCaseResult(name="test_iva_001", status="failure")
    reader.read.return_value = ControlResult(ok=False, test_cases=(tc,))

    vcs = MagicMock(spec=Vcs)
    vcs.get_trailers.return_value = []
    vcs.status.return_value = []
    vcs.get_head_message.return_value = "chore(lictor): driver de aceptación"
    vcs.get_head_sha.return_value = "sha_already_committed"

    spec_source = MagicMock(spec=SpecSource)
    spec_source.write_seal.return_value = {"files": {}}
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:00:00Z"

    use_case = CheckRed(
        runner=runner,
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = CheckRedRequest(
        project_root=tmp_path,
        run_id="run123",
        acceptance_run="acceptance-runner",
        driver_fresh=True,
        driver_attempts=1,
        max_driver_attempts=2,
        driver_paths=["tests/acceptance/"],
        all_scenario_ids=[ScenarioId("IVA-001")],
    )

    res = use_case.execute(req)
    assert res.ok is True
    assert res.commit_sha == "sha_already_committed"
    vcs.commit.assert_not_called()


def test_check_red_detects_state_tampering_via_integrity_snapshot(tmp_path: Path) -> None:
    lictor_dir = tmp_path / ".lictor"
    lictor_dir.mkdir(parents=True)
    chk = lictor_dir / "checkpoints.sqlite"
    chk.write_text("initial state", encoding="utf-8")

    runner = MagicMock(spec=CommandRunner)

    def sabotage(*args: object, **kwargs: object) -> CommandOutput:
        chk.write_text("tampered state", encoding="utf-8")
        return CommandOutput(returncode=1, stdout="", stderr="")

    runner.run.side_effect = sabotage

    reader = MagicMock(spec=ResultReader)
    tc = TestCaseResult(name="test_iva_001", status="failure")
    reader.read.return_value = ControlResult(ok=False, test_cases=(tc,))

    vcs = MagicMock(spec=Vcs)
    vcs.get_trailers.return_value = []
    spec_source = MagicMock(spec=SpecSource)
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)

    use_case = CheckRed(
        runner=runner,
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = CheckRedRequest(
        project_root=tmp_path,
        run_id="run123",
        acceptance_run="acceptance-runner",
        driver_fresh=True,
        driver_attempts=1,
        max_driver_attempts=2,
        driver_paths=["tests/acceptance/"],
        all_scenario_ids=[ScenarioId("IVA-001")],
    )

    res = use_case.execute(req)
    assert res.ok is False
    assert "L0-estado" in (res.last_error or "")
    assert chk.read_text(encoding="utf-8") == "initial state"
