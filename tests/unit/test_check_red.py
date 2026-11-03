import contextlib
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
from lictor.application.spec_history import SpecHistory
from lictor.application.usecases.check_red import (
    DRIVER_COMMIT_MESSAGE,
    CheckRed,
    CheckRedRequest,
)
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
from lictor.domain.rules import Impostors, RedOracle


def _environment(
    runner: CommandRunner, vcs: MagicMock, tmp_path: Path, spec: VerificationEnvSpec | None = None
) -> VerificationEnvironment:
    audit = AuditSink(event_log=MagicMock(spec=EventLog), clock=MagicMock(spec=Clock))
    return VerificationEnvironment(vcs, runner, audit, tmp_path, spec or VerificationEnvSpec())


def _history(spec_source: MagicMock, greens: Sequence[str] = ()) -> MagicMock:
    """Git's say on each scenario: green if a green commit's spec matches, new otherwise."""
    history = MagicMock(spec=SpecHistory)
    history.spec_source = spec_source
    history.classify.side_effect = lambda current: {
        sid: "green" if sid in greens else "new" for sid in current
    }
    return history


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
    vcs.commit_tree.return_value = Candidate("driver_commit_sha", "tree", "parent")

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
        history=_history(spec_source, ("IVA-001",)),
        audit=AuditSink(event_log=event_log, clock=clock),
    )

    req = CheckRedRequest(
        tree="driver-tree",
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
    vcs.commit_tree.assert_called_once_with(
        "driver-tree",
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
    # Both pass: IVA-002 is new and should have failed, so its test proves nothing yet.
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
        history=_history(spec_source, ("IVA-001",)),
        audit=AuditSink(event_log=event_log, clock=clock),
    )

    req = CheckRedRequest(
        tree="driver-tree",
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
    assert "test_iva_002 del escenario IVA-002 pasa con un driver nuevo" in (
        result.last_error or ""
    )
    assert event_log.append.call_args.args[0].data["reason"] == "sin_prueba"
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
        history=_history(spec_source, ("IVA-001",)),
        audit=AuditSink(event_log=event_log, clock=clock),
    )

    req = CheckRedRequest(
        tree="driver-tree",
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        acceptance_run="acceptance-runner --junitxml {out}",
        driver_fresh=True,
        driver_attempts=2,
        max_driver_attempts=2,
        driver_paths=["tests/acceptance/"],
        all_scenario_ids=[ScenarioId("IVA-001"), ScenarioId("IVA-002")],
    )

    with pytest.raises(DriverError, match="tras 2 intentos") as exc_info:
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
    vcs.get_tree.return_value = "driver-tree"
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
        history=_history(spec_source, ()),
        audit=AuditSink(event_log=event_log, clock=clock),
    )

    req = CheckRedRequest(
        tree="driver-tree",
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
    vcs.commit_tree.assert_not_called()
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
        history=_history(spec_source, ()),
        audit=AuditSink(event_log=event_log, clock=clock),
    )

    req = CheckRedRequest(
        tree="driver-tree",
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
# IVA-002 is green and its test fails now: a violation the driver's agent can fix.
_BROKEN_GREEN = {"report": _REPORT_RED, "greens": ("IVA-002",)}


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
    tmp_path: Path, report: str, code: int, greens: Sequence[str] = (), **overrides: Any
) -> tuple[CheckRed, CheckRedRequest, MagicMock, MagicMock, _SuiteRunner]:
    runner = _SuiteRunner(report, code)
    vcs = MagicMock(spec=Vcs)
    vcs.get_trailers.return_value = []
    vcs.commit_tree.return_value = Candidate("sha", "tree", "parent")
    spec_source = MagicMock(spec=SpecSource)
    spec_source.write_seal.return_value = {"files": {}}
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-31T10:00:00Z"
    use_case = CheckRed(
        environment=_environment(runner, vcs, tmp_path),
        reader=JUnitReader(),
        vcs=vcs,
        history=_history(spec_source, greens),
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


@pytest.mark.parametrize(
    ("setup", "reason"),
    [({"report": _REPORT_OFFENDER}, "sin_prueba"), (_BROKEN_GREEN, "regla_del_rojo")],
)
def test_check_red_violation_names_only_the_offender(
    tmp_path: Path, setup: dict[str, Any], reason: str
) -> None:
    use_case, request, event_log, _, _ = _real_check(tmp_path, code=1, **setup)

    result = use_case.execute(request)

    error = result.last_error or ""
    assert "test_iva_002" in error
    assert "test_iva_001" not in error
    assert "salida" not in error
    assert _rejection(event_log).data["reason"] == reason


@pytest.mark.parametrize("setup", [{"report": _REPORT_OFFENDER}, _BROKEN_GREEN])
def test_check_red_full_error_keeps_the_legitimate_reds(
    tmp_path: Path, setup: dict[str, Any]
) -> None:
    use_case, request, event_log, _, _ = _real_check(tmp_path, code=1, **setup)

    use_case.execute(request)

    path = tmp_path / str(_rejection(event_log).data["error_path"])
    full = path.read_text(encoding="utf-8")
    assert "test_iva_001" in full
    assert "stderr crudo" in full


@pytest.mark.parametrize("setup", [{"report": _REPORT_OFFENDER}, _BROKEN_GREEN])
def test_check_red_exhausted_attempts_carry_the_reason(
    tmp_path: Path, setup: dict[str, Any]
) -> None:
    use_case, request, event_log, _, _ = _real_check(
        tmp_path, code=1, driver_attempts=2, max_driver_attempts=2, **setup
    )

    with pytest.raises(DriverError, match="test_iva_002"):
        use_case.execute(request)
    assert _rejection(event_log).data["final"] is True


def test_check_red_sealed_driver_failure_carries_the_reason(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(
        tmp_path, code=1, driver_fresh=False, driver_attempts=0, **_BROKEN_GREEN
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


def test_check_red_repository_moved_during_the_suite_is_a_rejection(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(tmp_path, _REPORT_RED, 1)
    assert isinstance(use_case.vcs, MagicMock)
    use_case.vcs.restore.return_value = ["HEAD"]

    result = use_case.execute(request)

    assert result.ok is False
    assert _rejection(event_log).data["reason"] == "L0-estado"


def test_check_red_same_run_with_another_tree_is_not_the_committed_driver(tmp_path: Path) -> None:
    """After a crash the trailer alone doesn't prove HEAD holds the driver being checked."""
    use_case, request, _, _, _ = _real_check(tmp_path, _REPORT_RED, 1, tree="driver-tree")
    assert isinstance(use_case.vcs, MagicMock)
    use_case.vcs.get_trailers.side_effect = lambda key, commit_ref=None: (
        ["R"] if key == "Lictor-Run" else []
    )
    use_case.vcs.get_tree.return_value = "otro-arbol"

    result = use_case.execute(request)

    assert result.ok is True
    use_case.vcs.commit_tree.assert_called_once()
    use_case.vcs.advance_head.assert_called_once_with("sha", expected="parent")


def test_check_red_seal_leaves_the_hash_of_its_evidence(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(tmp_path, _REPORT_RED, 1, tree="driver-tree")

    use_case.execute(request)

    sealed = [
        call_.args[0]
        for call_ in event_log.append.call_args_list
        if call_.args[0].type == "lictor.driver.sealed.v1"
    ]
    assert sealed[0].data["evidence"]["hash"]


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
    vcs.commit_tree.assert_not_called()
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
    vcs.commit_tree.assert_not_called()
    vcs.advance_head.assert_not_called()
    spec_source.write_seal.assert_called_once()


def test_check_red_driver_that_changes_nothing_seals_head_without_a_commit(
    tmp_path: Path,
) -> None:
    """A rebuilt driver may need no change, as when a step's wording changed and the test didn't."""
    use_case, request, _, spec_source, _ = _real_check(tmp_path, _REPORT_RED, 1, tree="same")
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)
    vcs.get_tree.return_value = "same"
    vcs.get_head_sha.return_value = "head"

    result = use_case.execute(request)

    assert result.commit_sha == "head"
    vcs.commit_tree.assert_not_called()
    vcs.advance_head.assert_not_called()
    assert vcs.export_tree.call_args.args[0] == "HEAD"
    spec_source.write_seal.assert_called_once()


def _events(event_log: MagicMock, kind: str) -> list[Event]:
    return [c.args[0] for c in event_log.append.call_args_list if c.args[0].type == kind]


def test_check_red_new_scenario_passing_with_a_fresh_driver_is_another_try(
    tmp_path: Path,
) -> None:
    """With stubs in place, a new test that passes most likely checks nothing: the agent fixes."""
    use_case, request, event_log, spec_source, _ = _real_check(tmp_path, _REPORT_OFFENDER, 1)

    result = use_case.execute(request)

    assert result.ok is False
    error = result.last_error or ""
    assert "test_iva_002 del escenario IVA-002 pasa con un driver nuevo, sin llegar a" in error
    assert "comprobar el resultado" in error
    assert "--allow-empty" not in error
    rejection = _rejection(event_log)
    assert (rejection.data["reason"], rejection.data["final"]) == ("sin_prueba", False)
    spec_source.write_seal.assert_not_called()
    assert isinstance(use_case.vcs, MagicMock)
    use_case.vcs.advance_head.assert_not_called()


def test_check_red_new_scenario_passing_on_the_last_attempt_stops_with_the_hint(
    tmp_path: Path,
) -> None:
    use_case, request, event_log, _, _ = _real_check(
        tmp_path, _REPORT_OFFENDER, 1, driver_attempts=3, max_driver_attempts=3
    )

    with pytest.raises(DriverError, match="tras 3 intentos") as raised:
        use_case.execute(request)

    message = str(raised.value)
    assert raised.value.exit_code == 10
    fix, register = message.index("Corrige el test"), message.index("Solo si el escenario ya")
    assert fix < register
    assert '--trailer "Scenario: <id>"' in message
    assert _rejection(event_log).data["final"] is True


def test_check_red_reopened_scenario_passing_with_a_fresh_driver_stops_for_a_person(
    tmp_path: Path,
) -> None:
    """The step may still hold the old value: only a person knows the change was only wording."""
    use_case, request, event_log, spec_source, _ = _real_check(
        tmp_path, _REPORT_OFFENDER, 1, tree="driver-tree", driver_attempts=1
    )
    use_case.history.classify.side_effect = lambda current: {
        "IVA-001": "new",
        "IVA-002": "reopened",
    }
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)

    with pytest.raises(DriverError) as raised:
        use_case.execute(request)

    message = str(raised.value)
    assert message.startswith("La especificación de IVA-002 cambió y su test pasa")
    assert '--trailer "Scenario: <id>"' in message
    assert "Si no, su test no detecta el cambio" in message
    rejection = _rejection(event_log)
    assert (rejection.data["reason"], rejection.data["final"]) == ("especificacion_cambiada", True)
    vcs.advance_head.assert_not_called()
    assert _events(event_log, "lictor.scenario.reregistered.v1") == []
    spec_source.write_seal.assert_not_called()


def test_check_red_reopened_scenario_registers_on_head_with_a_sealed_driver(
    tmp_path: Path,
) -> None:
    use_case, request, _, spec_source, _ = _real_check(
        tmp_path, _REPORT_OFFENDER, 1, driver_fresh=False, driver_attempts=0
    )
    use_case.history.classify.side_effect = lambda current: {
        "IVA-001": "new",
        "IVA-002": "reopened",
    }
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)
    vcs.get_head_sha.return_value = "head"
    vcs.get_tree.return_value = "head-tree"
    vcs.commit_tree.return_value = Candidate("registro", "head-tree", "head")

    assert use_case.execute(request).ok is True

    assert vcs.commit_tree.call_args.kwargs["parent"] == "head"
    vcs.advance_head.assert_called_once_with("registro", expected="head")
    spec_source.write_seal.assert_not_called()


def test_check_red_reopened_scenario_that_fails_stays_pending(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(tmp_path, _REPORT_RED, 1)
    use_case.history.classify.side_effect = lambda current: {
        "IVA-001": "reopened",
        "IVA-002": "new",
    }

    result = use_case.execute(request)

    assert result.scenario_status == {"IVA-001": "pending", "IVA-002": "pending"}
    assert _events(event_log, "lictor.scenario.reregistered.v1") == []


def test_check_red_classifies_with_the_hashes_of_the_request(tmp_path: Path) -> None:
    use_case, request, _, _, _ = _real_check(
        tmp_path, _REPORT_RED, 1, scenario_hashes={"IVA-001": "h1"}
    )

    use_case.execute(request)

    use_case.history.classify.assert_called_once_with({"IVA-001": "h1", "IVA-002": ""})


def _sealed_with_registration(
    tmp_path: Path, classes: dict[str, str]
) -> tuple[CheckRed, CheckRedRequest, MagicMock, MagicMock]:
    use_case, request, event_log, _, _ = _real_check(
        tmp_path, _REPORT_OFFENDER, 1, driver_fresh=False, driver_attempts=0
    )
    assert isinstance(use_case.history, MagicMock)
    use_case.history.classify.side_effect = lambda current: classes
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)
    vcs.get_head_sha.return_value = "head"
    vcs.get_tree.return_value = "head-tree"
    vcs.commit_tree.return_value = Candidate("registro", "head-tree", "head")
    return use_case, request, event_log, vcs


def test_check_red_sealed_driver_registers_a_collateral_green_in_the_baseline(
    tmp_path: Path,
) -> None:
    use_case, request, event_log, vcs = _sealed_with_registration(
        tmp_path, {"IVA-001": "new", "IVA-002": "new"}
    )

    result = use_case.execute(request)

    assert result.scenario_status == {"IVA-001": "pending", "IVA-002": "green"}
    call = vcs.commit_tree.call_args
    assert call.kwargs["message"] == "test(IVA-002): verde colateral"
    assert call.kwargs["trailers"] == {
        "Scenario": ["IVA-002"],
        "Lictor-Collateral": "head",
        "Lictor-Run": "R",
    }
    assert (call.kwargs["parent"], call.kwargs["empty"]) == ("head", True)
    vcs.advance_head.assert_called_once_with("registro", expected="head")
    [green] = _events(event_log, "lictor.scenario.green.v1")
    assert (green.scenarioid, green.data) == ("IVA-002", {"commit": "registro", "collateral": True})
    assert _events(event_log, "lictor.driver.rejected.v1") == []


def test_check_red_registers_reopened_and_collateral_in_one_commit(tmp_path: Path) -> None:
    report = (
        '<testsuite><testcase name="test_iva_001"/><testcase name="test_iva_002"/>'
        '<testcase name="test_iva_003"><failure message="x"/></testcase></testsuite>'
    )
    use_case, request, event_log, _, _ = _real_check(
        tmp_path,
        report,
        1,
        driver_fresh=False,
        driver_attempts=0,
        all_scenario_ids=[ScenarioId("IVA-001"), ScenarioId("IVA-002"), ScenarioId("IVA-003")],
    )
    assert isinstance(use_case.history, MagicMock)
    use_case.history.classify.side_effect = lambda current: {
        "IVA-001": "reopened",
        "IVA-002": "new",
        "IVA-003": "new",
    }
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)
    vcs.get_head_sha.return_value = "head"
    vcs.commit_tree.return_value = Candidate("registro", "t", "head")

    use_case.execute(request)

    vcs.commit_tree.assert_called_once()
    call = vcs.commit_tree.call_args
    assert call.kwargs["message"] == "test(IVA-001, IVA-002): registrar sin agente"
    assert call.kwargs["trailers"]["Scenario"] == ["IVA-001", "IVA-002"]
    assert [e.scenarioid for e in _events(event_log, "lictor.scenario.reregistered.v1")] == [
        "IVA-001"
    ]
    assert [e.scenarioid for e in _events(event_log, "lictor.scenario.green.v1")] == ["IVA-002"]


def test_check_red_reregistration_alone_carries_no_collateral_trailer(tmp_path: Path) -> None:
    use_case, request, _, vcs = _sealed_with_registration(
        tmp_path, {"IVA-001": "new", "IVA-002": "reopened"}
    )

    use_case.execute(request)

    assert "Lictor-Collateral" not in vcs.commit_tree.call_args.kwargs["trailers"]


_ORACLE = RedOracle(
    red_kinds=("assertion", "not_implemented"), writable=("src/",), driver=("tests/acceptance/",)
)
_IMPOSTORS = Impostors(
    r"^(\s*)raise NotImplementedError\b.*$", (r"\1return None", r"\1return True")
)
_STUB = "def general():\n    raise NotImplementedError\n"


def _case_xml(name: str, failure: str = "") -> str:
    if not failure:
        return f'<testcase name="{name}"/>'
    props = f'<properties><property name="lictor.failure" value="{failure}"/>'
    props += '<property name="lictor.origin" value="src/iva.py"/></properties>'
    return f'<testcase name="{name}">{props}<failure message="m"/></testcase>'


class _ImpostorRunner(_SuiteRunner):
    """Reports by what the clone holds: the stub, `return None` or `return True`."""

    def __init__(self, reports: dict[str, tuple[str, int]]) -> None:
        super().__init__("", 1)
        self.reports = reports
        self.sources: list[str] = []

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandOutput:
        source = (cwd / "src" / "iva.py").read_text(encoding="utf-8")
        self.sources.append(source)
        driver = (cwd / "tests" / "acceptance" / "test_iva.py").read_text(encoding="utf-8")
        assert "raise NotImplementedError" in driver, "el driver no se toca"
        key = next(k for k in self.reports if k in source)
        self.report, self.code = self.reports[key]
        return super().run(cmd, cwd, timeout_s, env)


def _impostor_check(
    tmp_path: Path, reports: dict[str, tuple[str, int]], **overrides: Any
) -> tuple[CheckRed, CheckRedRequest, MagicMock, MagicMock, _ImpostorRunner]:
    use_case, request, event_log, spec_source, _ = _real_check(
        tmp_path, "", 1, **{"oracle": _ORACLE, "impostors": _IMPOSTORS, **overrides}
    )
    runner = _ImpostorRunner(reports)
    use_case.environment.runner = runner

    def export(_rev: str, dest: Path) -> None:
        (dest / "src").mkdir(parents=True)
        (dest / "src" / "iva.py").write_text(_STUB, encoding="utf-8")
        (dest / "tests" / "acceptance").mkdir(parents=True)
        (dest / "tests" / "acceptance" / "test_iva.py").write_text(
            "def stub():\n    raise NotImplementedError\n", encoding="utf-8"
        )

    assert isinstance(use_case.vcs, MagicMock)
    use_case.vcs.export_tree.side_effect = export
    return use_case, request, event_log, spec_source, runner


_RED = "".join(
    [_case_xml("test_iva_001", "not_implemented"), _case_xml("test_iva_002", "not_implemented")]
)


def _suite(*cases: str) -> str:
    return f"<testsuite>{''.join(cases)}</testsuite>"


def test_check_red_seals_a_driver_whose_new_tests_fail_with_some_impostor(tmp_path: Path) -> None:
    reports = {
        "raise NotImplementedError": (_suite(_RED), 1),
        "return None": (
            _suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002")),
            1,
        ),
        "return True": (
            _suite(_case_xml("test_iva_001"), _case_xml("test_iva_002", "assertion")),
            1,
        ),
    }
    use_case, request, _, spec_source, runner = _impostor_check(tmp_path, reports)

    result = use_case.execute(request)

    assert result.ok is True
    assert runner.sources == [
        _STUB,
        _STUB.replace("raise NotImplementedError", "return None"),
        _STUB.replace("raise NotImplementedError", "return True"),
    ]
    spec_source.write_seal.assert_called_once()
    step = runner.paths[0].parent
    assert (step / "impostor-1" / "acceptance.log").is_file()
    assert (step / "impostor-2" / "acceptance.xml").is_file()


def test_check_red_rejects_a_new_test_that_passes_with_every_impostor(tmp_path: Path) -> None:
    passing = (_suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002")), 1)
    reports = {"raise NotImplementedError": (_suite(_RED), 1), "return": passing}
    use_case, request, event_log, spec_source, _ = _impostor_check(tmp_path, reports)

    result = use_case.execute(request)

    assert result.ok is False
    rejection = _rejection(event_log)
    assert rejection.data["reason"] == "impostor"
    assert "Los tests de IVA-002 pasan aunque la interfaz devuelva siempre" in str(
        rejection.data["error"]
    )
    spec_source.write_seal.assert_not_called()


def test_check_red_an_impostor_that_keeps_a_stub_proves_nothing(tmp_path: Path) -> None:
    reports = {"raise NotImplementedError": (_suite(_RED), 1), "return": (_suite(_RED), 1)}
    use_case, request, event_log, _, _ = _impostor_check(tmp_path, reports)

    use_case.execute(request)

    error = str(_rejection(event_log).data["error"])
    assert "No se pudo construir el impostor" in error
    assert "una sola línea" in error


def test_check_red_an_impostor_that_crashes_proves_nothing(tmp_path: Path) -> None:
    reports = {"raise NotImplementedError": (_suite(_RED), 1), "return": ("", 2)}
    use_case, request, event_log, _, _ = _impostor_check(tmp_path, reports)

    use_case.execute(request)

    assert "No se pudo construir el impostor" in str(_rejection(event_log).data["error"])


def test_check_red_runs_no_impostor_for_a_sealed_driver(tmp_path: Path) -> None:
    reports = {"raise NotImplementedError": (_suite(_RED), 1)}
    use_case, request, _, _, runner = _impostor_check(
        tmp_path, reports, driver_fresh=False, driver_attempts=0
    )

    assert use_case.execute(request).ok is True
    assert len(runner.sources) == 1


def test_check_red_rejects_a_red_that_fails_with_an_error(tmp_path: Path) -> None:
    error = _suite(_case_xml("test_iva_001", "error"), _case_xml("test_iva_002", "not_implemented"))
    use_case, request, event_log, _, runner = _impostor_check(
        tmp_path, {"raise NotImplementedError": (error, 1)}
    )

    use_case.execute(request)

    assert "no falla por un rojo válido" in str(_rejection(event_log).data["error"])
    assert len(runner.sources) == 1


def test_check_red_rejects_a_fresh_driver_with_tests_of_no_scenario(tmp_path: Path) -> None:
    report = _suite(_RED, _case_xml("test_ayuda", "assertion"))
    use_case, request, event_log, spec_source, _ = _real_check(tmp_path, report, 1)

    use_case.execute(request)

    event = _rejection(event_log)
    assert event.data["reason"] == "sin_escenario"
    assert "no son de ningún escenario: test_ayuda" in str(event.data["error"])
    spec_source.write_seal.assert_not_called()


def test_check_red_a_sealed_driver_may_keep_tests_of_no_scenario(tmp_path: Path) -> None:
    report = _suite(_RED, _case_xml("test_ayuda", "assertion"))
    use_case, request, _, _, _ = _real_check(
        tmp_path, report, 1, driver_fresh=False, driver_attempts=0
    )
    assert use_case.execute(request).ok is True


def test_check_red_impostors_never_touch_the_driver_even_inside_writable(tmp_path: Path) -> None:
    """The configuration keeps them apart; the impostor doesn't rely on it."""
    reports = {
        "raise NotImplementedError": (_suite(_RED), 1),
        "return": (_suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002")), 1),
    }
    nested = RedOracle(
        _ORACLE.red_kinds, writable=("src/", "tests/"), driver=("tests/acceptance/",)
    )
    use_case, request, _, _, runner = _impostor_check(tmp_path, reports, oracle=nested)

    use_case.execute(request)

    assert len(runner.sources) == 3, "el driver intacto en cada impostor"


_FILES = dict.fromkeys(("IVA-001", "IVA-002"), "features/iva.feature")


def _linked_xml(name: str, sid: str, feature: str, failure: str = "") -> str:
    props = f'<property name="lictor.scenario" value="{sid}"/>'
    props += f'<property name="lictor.feature" value="{feature}"/>'
    if failure:
        props += f'<property name="lictor.failure" value="{failure}"/>'
        props += '<property name="lictor.origin" value="src/iva.py"/>'
    verdict = '<failure message="m"/>' if failure else ""
    return f'<testcase name="{name}"><properties>{props}</properties>{verdict}</testcase>'


def test_check_red_rejects_a_scenario_executed_from_another_file(tmp_path: Path) -> None:
    """Before the seal the driver may write a copy of the feature under another name."""
    report = _suite(
        _linked_xml("test_a", "IVA-001", "features/copia.txt", "not_implemented"),
        _linked_xml("test_b", "IVA-002", "features/iva.feature", "not_implemented"),
    )
    use_case, request, event_log, spec_source, _ = _real_check(
        tmp_path, report, 1, link="scenario", scenario_files=_FILES
    )

    result = use_case.execute(request)

    assert result.ok is False
    assert "desde features/copia.txt, no desde features/iva.feature" in (result.last_error or "")
    assert _rejection(event_log).data["reason"] == "mapeo"
    spec_source.write_seal.assert_not_called()


def test_check_red_impostors_map_by_the_scenario_s_own_file(tmp_path: Path) -> None:
    def suite(first: str, second: str) -> tuple[str, int]:
        return (
            _suite(
                _linked_xml("test_a", "IVA-001", "features/iva.feature", first),
                _linked_xml("test_b", "IVA-002", "features/iva.feature", second),
            ),
            1,
        )

    reports = {
        "raise NotImplementedError": suite("not_implemented", "not_implemented"),
        "return None": suite("assertion", ""),
        "return True": suite("", "assertion"),
    }
    use_case, request, _, spec_source, _ = _impostor_check(
        tmp_path, reports, link="scenario", scenario_files=_FILES
    )

    assert use_case.execute(request).ok is True
    spec_source.write_seal.assert_called_once()


_REPORT_GREEN = (
    '<testsuite><testcase name="test_iva_001"/><testcase name="test_iva_002"/></testsuite>'
)


@pytest.mark.parametrize("report", [_REPORT_RED, _REPORT_OFFENDER, _REPORT_GREEN])
@pytest.mark.parametrize("cls", ["green", "new", "reopened"])
def test_check_red_with_a_fresh_driver_never_commits_a_registration(
    tmp_path: Path, report: str, cls: str
) -> None:
    """Nothing turns green inside the red check before the seal: the branch moves to the driver."""
    use_case, request, event_log, _, _ = _real_check(tmp_path, report, 1, tree="driver-tree")
    assert isinstance(use_case.history, MagicMock)
    use_case.history.classify.side_effect = lambda current: dict.fromkeys(current, cls)
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)
    vcs.commit_tree.return_value = Candidate("driver", "driver-tree", "head")

    with contextlib.suppress(DriverError):
        use_case.execute(request)

    for call in vcs.commit_tree.call_args_list:
        assert call.kwargs["message"] == DRIVER_COMMIT_MESSAGE
        assert "empty" not in call.kwargs
    for call in vcs.advance_head.call_args_list:
        assert call.args == ("driver",)
    assert _events(event_log, "lictor.scenario.reregistered.v1") == []
    assert _events(event_log, "lictor.scenario.green.v1") == []
