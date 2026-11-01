from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.application.ports import (
    AuditSink,
    Clock,
    CommandRunner,
    EventLog,
    ResultReader,
    SpecSource,
    Vcs,
)
from lictor.application.usecases.check_red import CheckRed, CheckRedRequest
from lictor.application.verification_env import VerificationEnvironment, VerificationEnvSpec
from lictor.domain.errors import DriverError
from lictor.domain.model import (
    Candidate,
    CommandOutput,
    ControlResult,
    Event,
    ScenarioId,
    TestCaseResult,
)


def _environment(
    runner: CommandRunner, vcs: MagicMock, tmp_path: Path, spec: VerificationEnvSpec | None = None
) -> VerificationEnvironment:
    audit = AuditSink(event_log=MagicMock(spec=EventLog), clock=MagicMock(spec=Clock))
    return VerificationEnvironment(vcs, runner, audit, tmp_path, spec or VerificationEnvSpec())


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
    vcs.get_trailers.return_value = ["IVA-001"]
    vcs.create_candidate.return_value = Candidate("driver_commit_sha", "tree", "parent")

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
        environment=_environment(runner, vcs, tmp_path),
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        audit=AuditSink(event_log=event_log, clock=clock),
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
    vcs.create_candidate.assert_called_once_with(
        message="chore(lictor): driver de aceptación",
        trailers={"Lictor-Run": "20261030-140000-abcd"},
    )
    vcs.advance_head.assert_called_once_with("driver_commit_sha", expected="parent")
    vcs.export_tree.assert_called_once()
    assert vcs.export_tree.call_args.args[0] == "driver_commit_sha"
    seal_source = spec_source.write_seal.call_args.kwargs["source_root"]
    assert seal_source == vcs.export_tree.call_args.args[1]

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
        environment=_environment(runner, vcs, tmp_path),
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        audit=AuditSink(event_log=event_log, clock=clock),
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
        environment=_environment(runner, vcs, tmp_path),
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        audit=AuditSink(event_log=event_log, clock=clock),
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

    def mock_get_trailers(key: str, commit_ref: str | None = None) -> list[str]:
        if key == "Lictor-Run" and commit_ref == "HEAD":
            return ["run123"]
        return []

    vcs.get_trailers.side_effect = mock_get_trailers
    vcs.status.return_value = []
    vcs.get_head_message.return_value = "chore(lictor): driver de aceptación"
    vcs.get_head_sha.return_value = "sha_already_committed"

    spec_source = MagicMock(spec=SpecSource)
    spec_source.write_seal.return_value = {"files": {}}
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:00:00Z"

    use_case = CheckRed(
        environment=_environment(runner, vcs, tmp_path),
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        audit=AuditSink(event_log=event_log, clock=clock),
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
    vcs.create_candidate.assert_not_called()
    vcs.advance_head.assert_not_called()


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
        environment=_environment(runner, vcs, tmp_path),
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        audit=AuditSink(event_log=event_log, clock=clock),
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


_REPORT_RED = (
    '<testsuite><testcase name="test_iva_001"><failure message="NotImplementedError"/></testcase>'
    '<testcase name="test_iva_002"><failure message="NotImplementedError"/></testcase></testsuite>'
)
_REPORT_OFFENDER = (
    '<testsuite><testcase name="test_iva_001"><failure message="NotImplementedError"/></testcase>'
    '<testcase name="test_iva_002"/></testsuite>'
)


class _SuiteRunner(FakeRunner):
    """Runs the suite once with a given report, as the real command would write it."""

    def __init__(self, report: str, code: int) -> None:
        super().__init__()
        self.report = report
        self.code = code
        self.paths: list[Path] = []
        self.on_run: Callable[[], object] | None = None
        self.stdout = "salida"

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandOutput:
        if self.on_run is not None:
            self.on_run()
        out = Path(cmd[-1])
        self.paths.append(out)
        if self.report:
            out.write_text(self.report, encoding="utf-8")
        return CommandOutput(returncode=self.code, stdout=self.stdout, stderr="stderr crudo")


def _real_check(
    tmp_path: Path, report: str, code: int, **overrides: Any
) -> tuple[CheckRed, CheckRedRequest, MagicMock, MagicMock, _SuiteRunner]:
    runner = _SuiteRunner(report, code)
    vcs = MagicMock(spec=Vcs)
    vcs.get_trailers.return_value = []
    vcs.create_candidate.return_value = Candidate("sha", "tree", "parent")
    spec_source = MagicMock(spec=SpecSource)
    spec_source.write_seal.return_value = {"files": {}}
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-31T10:00:00Z"
    use_case = CheckRed(
        environment=_environment(runner, vcs, tmp_path),
        reader=JUnitReader(),
        vcs=vcs,
        spec_source=spec_source,
        audit=AuditSink(event_log=event_log, clock=clock),
    )
    fields: dict[str, Any] = {
        "project_root": tmp_path,
        "run_id": "R",
        "acceptance_run": "suite --junitxml {out}",
        "driver_fresh": True,
        "driver_attempts": 1,
        "max_driver_attempts": 3,
        "driver_paths": ["tests/acceptance/"],
        "all_scenario_ids": [ScenarioId("IVA-001"), ScenarioId("IVA-002")],
    }
    request = CheckRedRequest(**{**fields, **overrides})
    return use_case, request, event_log, spec_source, runner


def _rejection(event_log: MagicMock) -> Event:
    events = [call.args[0] for call in event_log.append.call_args_list]
    rejected = [e for e in events if e.type == "lictor.driver.rejected.v1"]
    assert len(rejected) == 1
    event: Event = rejected[0]
    return event


def test_check_red_crashed_suite_cannot_seal_on_red_looking_report(tmp_path: Path) -> None:
    use_case, request, event_log, spec_source, _ = _real_check(tmp_path, _REPORT_RED, 3)

    result = use_case.execute(request)

    assert result.ok is False
    assert "código 3" in (result.last_error or "")
    spec_source.write_seal.assert_not_called()
    assert _rejection(event_log).data["reason"] == "suite_caida"


def test_check_red_crash_without_report_explains_itself(tmp_path: Path) -> None:
    use_case, request, _, _, _ = _real_check(tmp_path, "", 3)

    result = use_case.execute(request)

    error = result.last_error or ""
    assert "código 3" in error
    assert "stderr crudo" in error
    assert "No se encontró" not in error


def test_check_red_failure_codes_come_from_the_request(tmp_path: Path) -> None:
    use_case, request, _, spec_source, _ = _real_check(
        tmp_path, _REPORT_RED, 2, acceptance_failure_exit_codes=(2,)
    )

    assert use_case.execute(request).ok is True
    spec_source.write_seal.assert_called_once()


def test_check_red_old_report_is_not_reused(tmp_path: Path) -> None:
    use_case, request, _, _, runner = _real_check(tmp_path, "", 3)
    stale = tmp_path / ".lictor" / "runs" / "R" / "driver" / "001-red"
    stale.mkdir(parents=True)
    (stale / "acceptance.xml").write_text(_REPORT_RED, encoding="utf-8")

    result = use_case.execute(request)

    assert result.ok is False
    assert runner.paths[0].parent.name == "001-red-r1"


def test_check_red_mapping_failure_is_recorded(tmp_path: Path) -> None:
    report = '<testsuite><testcase name="test_iva_001"><failure/></testcase></testsuite>'
    use_case, request, event_log, _, _ = _real_check(tmp_path, report, 1)

    result = use_case.execute(request)

    assert "No se encontró" in (result.last_error or "")
    event = _rejection(event_log)
    assert event.data["reason"] == "mapeo"
    assert event.data["attempt"] == 1
    assert event.data["final"] is False
    assert "No se encontró" in str(event.data["error"])
    error_path = tmp_path / str(event.data["error_path"])
    assert error_path.parent.name == "001-red"
    assert "No se encontró" in error_path.read_text(encoding="utf-8")


def test_check_red_violation_names_only_the_offender(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(tmp_path, _REPORT_OFFENDER, 1)

    result = use_case.execute(request)

    error = result.last_error or ""
    assert "test_iva_002" in error
    assert "test_iva_001" not in error
    assert "salida" not in error
    assert _rejection(event_log).data["reason"] == "regla_del_rojo"


def test_check_red_full_error_keeps_the_legitimate_reds(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(tmp_path, _REPORT_OFFENDER, 1)

    use_case.execute(request)

    path = tmp_path / str(_rejection(event_log).data["error_path"])
    full = path.read_text(encoding="utf-8")
    assert "test_iva_001" in full
    assert "stderr crudo" in full


def test_check_red_exhausted_attempts_carry_the_reason(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(
        tmp_path, _REPORT_OFFENDER, 1, driver_attempts=2, max_driver_attempts=2
    )

    with pytest.raises(DriverError, match="test_iva_002"):
        use_case.execute(request)
    assert _rejection(event_log).data["final"] is True


def test_check_red_sealed_driver_failure_carries_the_reason(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(
        tmp_path, _REPORT_OFFENDER, 1, driver_fresh=False, driver_attempts=0
    )

    with pytest.raises(DriverError, match="test_iva_002"):
        use_case.execute(request)
    assert _rejection(event_log).data["final"] is True


def test_check_red_tampering_is_a_recorded_rejection(tmp_path: Path) -> None:
    use_case, request, event_log, _, runner = _real_check(tmp_path, _REPORT_RED, 1)
    state = tmp_path / ".lictor"
    state.mkdir()
    (state / "seal.json").write_text("{}", encoding="utf-8")
    runner.on_run = lambda: (state / "seal.json").write_text("forjado", encoding="utf-8")

    result = use_case.execute(request)

    assert result.ok is False
    assert _rejection(event_log).data["reason"] == "L0-estado"


def test_check_red_passing_keeps_its_output(tmp_path: Path) -> None:
    use_case, request, _, _, runner = _real_check(tmp_path, _REPORT_RED, 1)

    assert use_case.execute(request).ok is True
    log = runner.paths[0].parent / "acceptance.log"
    assert "stderr crudo" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "overrides",
    [
        {"driver_fresh": False, "driver_attempts": 0},
        {"driver_attempts": 3, "max_driver_attempts": 3},
    ],
)
def test_check_red_stop_message_stays_bounded(tmp_path: Path, overrides: dict[str, Any]) -> None:
    use_case, request, _, _, runner = _real_check(tmp_path, "", 3, error_max_chars=500, **overrides)
    runner.stdout = "x" * 100_000

    with pytest.raises(DriverError) as raised:
        use_case.execute(request)

    assert "código 3" in str(raised.value)
    assert len(str(raised.value)) < 600


def _tampered_environment(check: CheckRed, problems: list[str | None]) -> None:
    environment = MagicMock(spec=VerificationEnvironment)
    environment.ensure.side_effect = problems
    environment.run.return_value = CommandOutput(returncode=1, stdout="", stderr="")
    check.environment = environment


def test_check_red_tampered_environment_rejects_without_running(tmp_path: Path) -> None:
    use_case, request, event_log, spec_source, runner = _real_check(tmp_path, _REPORT_RED, 1)
    _tampered_environment(use_case, ["manipulado"])

    result = use_case.execute(request)

    assert result.ok is False
    assert _rejection(event_log).data["reason"] == "L0-entorno"
    assert runner.paths == []
    spec_source.write_seal.assert_not_called()


def test_check_red_environment_tampered_during_the_suite_blocks_the_seal(tmp_path: Path) -> None:
    use_case, request, event_log, spec_source, _ = _real_check(tmp_path, _REPORT_RED, 1)
    environment = MagicMock(spec=VerificationEnvironment)
    environment.ensure.side_effect = [None, "manipulado"]
    original_runner = use_case.environment.runner
    environment.run.side_effect = original_runner.run
    use_case.environment = environment

    result = use_case.execute(request)

    assert result.ok is False
    assert _rejection(event_log).data["reason"] == "L0-entorno"
    spec_source.write_seal.assert_not_called()


def test_check_red_baseline_verifies_head_and_commits_nothing(tmp_path: Path) -> None:
    use_case, request, _, spec_source, _ = _real_check(
        tmp_path, _REPORT_RED, 1, driver_fresh=False, driver_attempts=0
    )
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)

    assert use_case.execute(request).ok is True

    assert vcs.export_tree.call_args.args[0] == "HEAD"
    vcs.create_candidate.assert_not_called()
    vcs.advance_head.assert_not_called()
    spec_source.write_seal.assert_not_called()


def test_check_red_rerun_after_a_crash_verifies_the_driver_already_committed(
    tmp_path: Path,
) -> None:
    use_case, request, _, spec_source, _ = _real_check(tmp_path, _REPORT_RED, 1)
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)
    vcs.status.return_value = []
    vcs.get_trailers.side_effect = lambda key, commit_ref=None: ["R"] if commit_ref else []
    vcs.get_head_sha.return_value = "head"

    result = use_case.execute(request)

    assert result.commit_sha == "head"
    vcs.create_candidate.assert_not_called()
    vcs.advance_head.assert_not_called()
    spec_source.write_seal.assert_called_once()
