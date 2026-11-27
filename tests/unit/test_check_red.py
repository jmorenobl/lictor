import contextlib
import html
import json
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
    SpecSource,
    Vcs,
)
from lictor.application.seal import hash_tree
from lictor.application.spec_history import SpecHistory
from lictor.application.usecases.check_red import (
    DRIVER_COMMIT_MESSAGE,
    CheckRed,
    CheckRedRequest,
)
from lictor.application.verification_env import VerificationEnvironment, VerificationEnvSpec
from lictor.domain.errors import DriverError
from lictor.domain.interface_proof import InterfaceImpostor
from lictor.domain.model import (
    Candidate,
    CommandOutput,
    Event,
    ScenarioId,
    TestCaseResult,
)
from lictor.domain.reports import JUnitReport
from lictor.domain.rules import Impostors, RedOracle, StubConstant


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

    reader = MagicMock(spec=JUnitReader)
    # 2 scenarios: IVA-001 (already green, passes), IVA-002 (pending, fails)
    tc1 = TestCaseResult(name="test_iva_001", status="passed")
    tc2 = TestCaseResult(name="test_iva_002", status="failure", message="assert 1 == 2")
    reader.read.return_value = JUnitReport((tc1, tc2))

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

    measured, sealed = (call.args[0] for call in event_log.append.call_args_list)
    assert isinstance(sealed, Event)
    assert sealed.type == "lictor.driver.sealed.v1"
    assert sealed.data["commit"] == "driver_commit_sha"
    # What the stricter rules would say comes first, and names its step without hashing it.
    assert measured.type == "lictor.driver.measured.v1"
    assert measured.data["step"] == sealed.data["evidence"]["path"]
    assert "evidence" not in measured.data
    assert measured.data["red"] == {
        "IVA-002": {"step": None, "caught": "no_aserta", "status": "failure"}
    }
    assert (measured.data["stubs"], measured.data["interface"]) == (None, None)
    # How long the red check took, and each of its runs by phase (F52): here the red alone.
    assert sealed.data["phases"] == {"red": {"runs": 1, "duration_ms": 500}}
    assert isinstance(sealed.data["duration_ms"], int)


def test_check_red_fresh_driver_retry_when_under_max_attempts(tmp_path: Path) -> None:
    runner = MagicMock(spec=CommandRunner)
    runner.run.return_value = CommandOutput(returncode=0, stdout="", stderr="")

    reader = MagicMock(spec=JUnitReader)
    # Both pass: IVA-002 is new and should have failed, so its test proves nothing yet.
    tc1 = TestCaseResult(name="test_iva_001", status="passed")
    tc2 = TestCaseResult(name="test_iva_002", status="passed")
    reader.read.return_value = JUnitReport((tc1, tc2))

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

    reader = MagicMock(spec=JUnitReader)
    tc1 = TestCaseResult(name="test_iva_001", status="passed")
    tc2 = TestCaseResult(name="test_iva_002", status="passed")
    reader.read.return_value = JUnitReport((tc1, tc2))

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

    reader = MagicMock(spec=JUnitReader)
    tc = TestCaseResult(name="test_iva_001", status="failure")
    reader.read.return_value = JUnitReport((tc,))

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

    reader = MagicMock(spec=JUnitReader)
    tc = TestCaseResult(name="test_iva_001", status="failure")
    reader.read.return_value = JUnitReport((tc,))

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
    assert log.read_text(encoding="utf-8").startswith("Comando: ")


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


def test_check_red_baseline_that_passes_records_its_evidence(tmp_path: Path) -> None:
    """Every step in `runs/` has its hash in the journal, the red that registers nothing too."""
    use_case, request, event_log, _, _ = _real_check(
        tmp_path, _REPORT_RED, 1, driver_fresh=False, driver_attempts=0
    )

    result = use_case.execute(request)

    (checked,) = _events(event_log, "lictor.driver.checked.v1")
    assert checked.data["attempt"] == 0
    assert checked.data["scenario_status"] == result.scenario_status
    evidence = checked.data["evidence"]
    step_dir = tmp_path / evidence["path"]
    assert step_dir.name == "000-red"
    assert evidence["hash"] == hash_tree(step_dir)
    assert _events(event_log, "lictor.driver.sealed.v1") == []


def test_check_red_fresh_driver_records_no_check_beside_its_seal(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(tmp_path, _REPORT_RED, 1)

    assert use_case.execute(request).ok is True

    assert _events(event_log, "lictor.driver.checked.v1") == []
    assert len(_events(event_log, "lictor.driver.sealed.v1")) == 1


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
    """A profile without the interface's impostor can't tell: the agent gets another try."""
    use_case, request, event_log, spec_source, _ = _real_check(tmp_path, _REPORT_OFFENDER, 1)

    result = use_case.execute(request)

    assert result.ok is False
    error = result.last_error or ""
    assert "test_iva_002 del escenario IVA-002 pasa con un driver nuevo, y este perfil no" in error
    assert "sin llegar a la interfaz" not in error
    assert "mientras la interfaz no esté implementada" not in error
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
    fix, register = message.index("Corrige el test"), message.index("Solo si el código ya")
    assert fix < register
    assert "`lictor register IVA-002`" in message
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
    assert "`lictor register IVA-002`" in message
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
    assert call.kwargs["message"] == "test: registrar sin agente, 2 escenarios"
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
_PATTERN = r"^(\s*)raise NotImplementedError\b.*$"
_NONE = StubConstant(r"\1return None", "None")
_TRUE = StubConstant(r"\1return True", "True")
_ONE = StubConstant(r"\1return ONE", "un doble de Lictor", double=True)
_IMPOSTORS = Impostors(_PATTERN, (_NONE, _TRUE))
_WITH_A_DOUBLE = Impostors(_PATTERN, (_NONE, _TRUE, _ONE))
_STUB = "def general():\n    raise NotImplementedError\n"


def _case_xml(name: str, failure: str = "", step: str = "then", message: str = "m") -> str:
    """A case of the acceptance; an assertion is the driver's own, in `step`."""
    if not failure:
        return f'<testcase name="{name}"/>'
    props = [("lictor.failure", failure), ("lictor.step", step)]
    if failure == "assertion":
        props += [("lictor.frame", "tests/acceptance/test_iva.py"), ("lictor.line", "12")]
    else:
        props += [("lictor.origin", "src/iva.py"), ("lictor.frame", "tests/acceptance/test_iva.py")]
    body = "".join(f'<property name="{key}" value="{value}"/>' for key, value in props)
    fails = f'<failure message="{message}"/>'
    return f'<testcase name="{name}"><properties>{body}</properties>{fails}</testcase>'


class _ImpostorRunner(_SuiteRunner):
    """Reports by what the clone holds (the stub or a constant) and, when the impostor of the
    interface runs over the stubs, by its mode (`interfaz-<mode>`)."""

    def __init__(self, reports: dict[str, tuple[str, int]]) -> None:
        super().__init__("", 1)
        self.reports = reports
        self.sources: list[str] = []
        self.told: list[dict[str, Any]] = []

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
        raw = (env or {}).get("LICTOR_IMPOSTOR")
        if raw:
            told = json.loads(raw)
            self.told.append(told)
            key = f"interfaz-{told['mode']}"
        else:
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
    impostor_log = (step / "impostor-1" / "acceptance.log").read_text(encoding="utf-8")
    assert impostor_log.startswith("Comando: ")
    assert (step / "impostor-2" / "acceptance.xml").is_file()


_PASSES = (_suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002")), 1)


def test_check_red_without_an_interface_impostor_a_test_that_passes_is_rejected(
    tmp_path: Path,
) -> None:
    """A profile that can't judge what doesn't depend on the stubs rejects it, asking for nothing
    in a Given (F64)."""
    reports = {"raise NotImplementedError": (_suite(_RED), 1), "return": _PASSES}
    use_case, request, event_log, spec_source, _ = _impostor_check(tmp_path, reports)

    result = use_case.execute(request)

    assert result.ok is False
    rejection = _rejection(event_log)
    assert rejection.data["reason"] == "impostor"
    error = str(rejection.data["error"])
    assert "El test test_iva_002 del escenario IVA-002 pasa aunque los stubs devuelvan" in error
    assert "este perfil no puede comprobar" in error
    assert "Dado" not in error
    spec_source.write_seal.assert_not_called()


def _via_reports(menos: str, mas: str | None = None) -> dict[str, tuple[str, int]]:
    """IVA-002 passes with every constant: the interface judges it over the stubs at None."""
    red = _iface_xml("test_iva_001", "assertion")
    return {
        "raise NotImplementedError": (_suite(_RED), 1),
        "return": _PASSES,
        "interfaz-control": (_suite(red, _iface_xml("test_iva_002", altered=0)), 1),
        "interfaz-mas": (_suite(red, mas or menos), 1),
        "interfaz-menos": (_suite(red, menos), 1),
    }


def test_check_red_a_test_that_doesn_t_depend_on_the_stubs_goes_to_the_interface(
    tmp_path: Path,
) -> None:
    caught = _iface_xml("test_iva_002", "assertion")
    use_case, request, event_log, spec_source, runner = _impostor_check(
        tmp_path, _via_reports(caught), interface_impostor=_INTERFACE
    )

    result = use_case.execute(request)

    assert result.ok is True
    spec_source.write_seal.assert_called_once()
    assert [told["mode"] for told in runner.told] == ["control", "mas", "menos"]
    stub_at_none = _STUB.replace("raise NotImplementedError", "return None")
    assert runner.sources[-3:] == [stub_at_none] * 3
    for told in runner.told:
        [(path, lines)] = told["stubs"].items()
        assert (path.endswith("/src/iva.py"), lines) == (True, [2])
    step = runner.paths[0].parent
    assert (step / "stubs-interfaz-menos" / "acceptance.xml").is_file()
    [sealed] = _events(event_log, "lictor.driver.sealed.v1")
    assert sealed.data["stub_unchecked"] == []
    [measured] = _events(event_log, "lictor.driver.measured.v1")
    assert measured.data["stubs"]["IVA-002"] == {
        "by_constant": {"None": "pasa", "True": "pasa"},
        "outcome": "a_la_interfaz",
        "interface": "probado",
        "verdict": "aceptado",
    }


def test_check_red_seals_unchecked_what_the_interface_finds_nothing_to_alter_in(
    tmp_path: Path,
) -> None:
    unaltered = _iface_xml("test_iva_002", altered=0)
    use_case, request, event_log, spec_source, _ = _impostor_check(
        tmp_path, _via_reports(unaltered), interface_impostor=_INTERFACE
    )

    assert use_case.execute(request).ok is True

    spec_source.write_seal.assert_called_once()
    [sealed] = _events(event_log, "lictor.driver.sealed.v1")
    assert sealed.data["stub_unchecked"] == ["IVA-002"]


def test_check_red_rejects_what_the_interface_finds_unchecked(tmp_path: Path) -> None:
    survives = _iface_xml("test_iva_002")
    use_case, request, event_log, spec_source, _ = _impostor_check(
        tmp_path, _via_reports(survives), interface_impostor=_INTERFACE
    )

    assert use_case.execute(request).ok is False

    error = str(_rejection(event_log).data["error"])
    assert "IVA-002 pasa aunque los stubs no estén implementados y sigue pasando" in error
    spec_source.write_seal.assert_not_called()


def test_check_red_a_test_that_passes_with_none_and_true_goes_to_the_interface(
    tmp_path: Path,
) -> None:
    """A double would catch `r in (None, True)`: that proves nothing of the test."""
    reports = {
        **_via_reports(_iface_xml("test_iva_002")),
        "return ONE": (
            _suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002", "assertion")),
            1,
        ),
    }
    use_case, request, event_log, _, runner = _impostor_check(
        tmp_path, reports, impostors=_WITH_A_DOUBLE, interface_impostor=_INTERFACE
    )

    assert use_case.execute(request).ok is False

    assert [told["mode"] for told in runner.told] == ["control", "mas", "menos"]
    assert "IVA-002 pasa aunque los stubs no estén implementados" in str(
        _rejection(event_log).data["error"]
    )


def test_check_red_a_double_lets_a_test_reach_its_assertion(tmp_path: Path) -> None:
    error = _case_xml("test_iva_002", "error", message="AttributeError: 'NoneType' object")
    failing = (_suite(_case_xml("test_iva_001", "assertion"), error), 1)
    reports = {
        "raise NotImplementedError": (_suite(_RED), 1),
        "return None": failing,
        "return True": failing,
        "return ONE": (
            _suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002", "assertion")),
            1,
        ),
    }
    use_case, request, _, spec_source, runner = _impostor_check(
        tmp_path, reports, impostors=_WITH_A_DOUBLE
    )

    assert use_case.execute(request).ok is True
    spec_source.write_seal.assert_called_once()
    assert runner.told == []


def test_check_red_rejects_a_test_that_never_reaches_an_assertion(tmp_path: Path) -> None:
    error = _case_xml("test_iva_002", "error", message="TypeError: cannot unpack")
    failing = (_suite(_case_xml("test_iva_001", "assertion"), error), 1)
    reports = {"raise NotImplementedError": (_suite(_RED), 1), "return": failing}
    use_case, request, event_log, _, _ = _impostor_check(
        tmp_path, reports, impostors=_WITH_A_DOUBLE
    )

    use_case.execute(request)

    error_text = str(_rejection(event_log).data["error"])
    assert (
        "El test test_iva_002 del escenario IVA-002, cuando los stubs devuelven un valor en lugar "
        "de lanzar NotImplementedError, falla, pero con error de un Entonces, en "
        "tests/acceptance/test_iva.py (devolviendo un doble de Lictor: TypeError: cannot unpack)"
    ) in error_text
    assert "tal cual, sin convertirlo" in error_text
    assert "no los trates aparte" in error_text


def test_check_red_rejects_a_test_that_checks_outside_a_then(tmp_path: Path) -> None:
    given = _case_xml("test_iva_002", "assertion", step="given", message="assert True is None")
    reports = {
        "raise NotImplementedError": (_suite(_RED), 1),
        "return None": (_suite(_case_xml("test_iva_001", "assertion"), given), 1),
        "return True": (_suite(_case_xml("test_iva_001", "assertion"), given), 1),
    }
    use_case, request, event_log, _, _ = _impostor_check(tmp_path, reports)

    use_case.execute(request)

    error = str(_rejection(event_log).data["error"])
    assert "falla por una aserción de un Dado (devolviendo None: assert True is None)" in error
    assert "guárdalo en el Cuando y compáralo en el Entonces" in error


def test_check_red_once_the_attempts_run_out_the_person_reads_the_known_limits(
    tmp_path: Path,
) -> None:
    error = _case_xml("test_iva_002", "error", message="TypeError")
    failing = (_suite(_case_xml("test_iva_001", "assertion"), error), 1)
    reports = {"raise NotImplementedError": (_suite(_RED), 1), "return": failing}
    use_case, request, _, _, _ = _impostor_check(tmp_path, reports, driver_attempts=3)

    with pytest.raises(DriverError) as raised:
        use_case.execute(request)

    assert "reescríbelo a mano" in str(raised.value)


def test_check_red_an_impostor_that_keeps_a_stub_proves_nothing(tmp_path: Path) -> None:
    reports = {"raise NotImplementedError": (_suite(_RED), 1), "return": (_suite(_RED), 1)}
    use_case, request, event_log, _, _ = _impostor_check(tmp_path, reports)

    use_case.execute(request)

    error = str(_rejection(event_log).data["error"])
    assert "No se pudo construir el impostor 'None'" in error
    assert "una sola línea" in error


def test_check_red_a_constant_whose_run_crashes_counts_for_nothing(tmp_path: Path) -> None:
    reports = {
        "raise NotImplementedError": (_suite(_RED), 1),
        "return None": ("", 2),
        "return True": (
            _suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002", "assertion")),
            1,
        ),
    }
    use_case, request, _, spec_source, _ = _impostor_check(tmp_path, reports)

    assert use_case.execute(request).ok is True
    spec_source.write_seal.assert_called_once()


def test_check_red_a_constant_whose_run_doesn_t_map_counts_for_nothing(tmp_path: Path) -> None:
    reports = {
        "raise NotImplementedError": (_suite(_RED), 1),
        "return None": (_suite(_case_xml("test_iva_001", "assertion")), 1),
        "return True": (
            _suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002", "assertion")),
            1,
        ),
    }
    use_case, request, _, spec_source, _ = _impostor_check(tmp_path, reports)

    assert use_case.execute(request).ok is True
    spec_source.write_seal.assert_called_once()


def test_check_red_a_file_that_isn_t_text_holds_no_stub(tmp_path: Path) -> None:
    caught = _iface_xml("test_iva_002", "assertion")
    use_case, request, _, spec_source, runner = _impostor_check(
        tmp_path, _via_reports(caught), interface_impostor=_INTERFACE
    )
    assert isinstance(use_case.vcs, MagicMock)
    export = use_case.vcs.export_tree.side_effect

    def with_a_binary(rev: str, dest: Path) -> None:
        export(rev, dest)
        (dest / "src" / "logo.bin").write_bytes(b"\xff\xfe raise NotImplementedError")

    use_case.vcs.export_tree.side_effect = with_a_binary

    assert use_case.execute(request).ok is True
    spec_source.write_seal.assert_called_once()
    for told in runner.told:
        assert [Path(path).name for path in told["stubs"]] == ["iva.py"]


def test_check_red_rejects_the_driver_when_every_constant_s_run_crashes(tmp_path: Path) -> None:
    reports = {"raise NotImplementedError": (_suite(_RED), 1), "return": ("", 2)}
    use_case, request, event_log, _, _ = _impostor_check(tmp_path, reports)

    use_case.execute(request)

    rejection = _rejection(event_log)
    assert rejection.data["reason"] == "impostor"
    assert "Ninguna ejecución con los stubs sustituidos dio resultado: con None" in str(
        rejection.data["error"]
    )


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
        props += '<property name="lictor.frame" value="tests/acceptance/test_iva.py"/>'
        props += '<property name="lictor.step" value="then"/>'
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


_PATH_HACK = 'sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))'


def test_check_red_rejects_a_fresh_driver_with_a_banned_line_before_running_it(
    tmp_path: Path,
) -> None:
    use_case, request, event_log, spec_source, runner = _real_check(
        tmp_path, _REPORT_RED, 1, tree="driver-tree", banned_patterns=(r"\bsys\.path\b",)
    )
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)
    vcs.added_lines.return_value = [_PATH_HACK, "from romanos.cli import main  # noqa: E402"]

    result = use_case.execute(request)

    assert (result.ok, result.failed_at) == (False, "L0-supresiones")
    event = _rejection(event_log)
    assert event.data["reason"] == "L0-supresiones"
    assert r"\bsys\.path\b" in str(event.data["error"])
    vcs.added_lines.assert_called_once_with("driver-tree")
    assert runner.paths == []
    spec_source.write_seal.assert_not_called()
    vcs.commit_tree.assert_not_called()


def test_check_red_rejects_a_suppression_the_fresh_driver_adds(tmp_path: Path) -> None:
    use_case, request, event_log, _, _ = _real_check(
        tmp_path, _REPORT_RED, 1, tree="driver-tree", suppressions=("# noqa",)
    )
    assert isinstance(use_case.vcs, MagicMock)
    use_case.vcs.added_lines.return_value = ["from romanos.cli import main  # noqa: E402"]

    use_case.execute(request)

    assert "# noqa" in str(_rejection(event_log).data["error"])


def test_check_red_on_the_last_attempt_a_banned_line_stops_the_run(tmp_path: Path) -> None:
    use_case, request, _, _, _ = _real_check(
        tmp_path,
        _REPORT_RED,
        1,
        tree="driver-tree",
        banned_patterns=(r"\bsys\.path\b",),
        driver_attempts=3,
    )
    assert isinstance(use_case.vcs, MagicMock)
    use_case.vcs.added_lines.return_value = [_PATH_HACK]

    with pytest.raises(DriverError, match="L0-supresiones"):
        use_case.execute(request)


def test_check_red_never_judges_the_lines_of_a_sealed_driver(tmp_path: Path) -> None:
    use_case, request, _, _, _ = _real_check(
        tmp_path,
        _REPORT_RED,
        1,
        driver_fresh=False,
        driver_attempts=0,
        banned_patterns=(r"\bsys\.path\b",),
    )
    assert isinstance(use_case.vcs, MagicMock)
    use_case.vcs.added_lines.return_value = [_PATH_HACK]

    use_case.execute(request)

    use_case.vcs.added_lines.assert_not_called()


# The impostor of the interface (ADR-0011): a new scenario that passes with a fresh driver.
_INTERFACE = InterfaceImpostor("LICTOR_IMPOSTOR", "control", ("mas", "menos"))


def _iface_xml(
    name: str, failure: str = "", calls: int = 1, altered: int = 1, frame: str = ""
) -> str:
    """A test that calls the interface, and fails if it does, in its Then."""
    props = [
        ("lictor.interface.calls", str(calls)),
        ("lictor.interface.calls.then", str(calls)),
        ("lictor.interface.altered", str(altered)),
        ("lictor.interface.altered.then", str(altered)),
    ]
    if failure:
        props += [("lictor.failure", failure), ("lictor.frame", frame or "tests/acceptance/t.py")]
        props += [("lictor.step", "then")]
    body = "".join(f'<property name="{key}" value="{value}"/>' for key, value in props)
    fails = '<failure message="m"/>' if failure else ""
    return f'<testcase name="{name}"><properties>{body}</properties>{fails}</testcase>'


_IFACE_RED = _suite(_iface_xml("test_iva_001", "assertion"), _iface_xml("test_iva_002"))
_IFACE_CONTROL = _IFACE_RED
_IFACE_CAUGHT = _suite(
    _iface_xml("test_iva_001", "assertion"), _iface_xml("test_iva_002", "assertion")
)


class _InterfaceRunner(_SuiteRunner):
    """Reports by the mode the impostor's variable gives, or as the red without it."""

    def __init__(self, reports: dict[str, tuple[str, int]]) -> None:
        super().__init__("", 1)
        self.reports = reports
        self.told: list[dict[str, Any] | None] = []

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandOutput:
        raw = (env or {}).get("LICTOR_IMPOSTOR")
        told = json.loads(raw) if raw else None
        self.told.append(told)
        self.report, self.code = self.reports[told["mode"] if told else "red"]
        return super().run(cmd, cwd, timeout_s, env)


def _interface_check(
    tmp_path: Path, reports: dict[str, tuple[str, int]], **overrides: Any
) -> tuple[CheckRed, CheckRedRequest, MagicMock, MagicMock, _InterfaceRunner, MagicMock]:
    fields: dict[str, Any] = {
        "oracle": _ORACLE,
        "interface_impostor": _INTERFACE,
        "tree": "driver-tree",
        **overrides,
    }
    use_case, request, event_log, spec_source, _ = _real_check(tmp_path, "", 1, **fields)
    runner = _InterfaceRunner(reports)
    use_case.environment.runner = runner
    vcs = use_case.vcs
    assert isinstance(vcs, MagicMock)
    vcs.commit_tree.side_effect = [
        Candidate("driver", "driver-tree", "head"),
        Candidate("registro", "driver-tree", "driver"),
    ]
    return use_case, request, event_log, spec_source, runner, vcs


def _proved_reports() -> dict[str, tuple[str, int]]:
    return {
        "red": (_IFACE_RED, 1),
        "control": (_IFACE_CONTROL, 1),
        "mas": (_IFACE_CAUGHT, 1),
        "menos": (_IFACE_CAUGHT, 1),
    }


def test_check_red_a_new_scenario_the_code_already_met_is_a_proved_collateral(
    tmp_path: Path,
) -> None:
    use_case, request, event_log, spec_source, runner, vcs = _interface_check(
        tmp_path, _proved_reports()
    )

    result = use_case.execute(request)

    assert result.ok is True
    assert result.scenario_status == {"IVA-001": "pending", "IVA-002": "green"}
    assert [t["mode"] if t else None for t in runner.told] == [None, "control", "mas", "menos"]
    spec_source.write_seal.assert_called_once()
    registration = vcs.commit_tree.call_args_list[1]
    assert registration.kwargs["message"] == "test(IVA-002): ya se cumplía"
    assert registration.kwargs["trailers"] == {
        "Scenario": ["IVA-002"],
        "Lictor-Collateral": "driver",
        "Lictor-Proof": "interfaz",
        "Lictor-Run": "R",
    }
    assert (registration.kwargs["parent"], registration.kwargs["empty"]) == ("driver", True)
    vcs.advance_head.assert_called_once_with("registro", expected="head")
    [green] = _events(event_log, "lictor.scenario.green.v1")
    assert (green.scenarioid, green.data) == (
        "IVA-002",
        {"commit": "registro", "collateral": True, "proof": "interface"},
    )
    [sealed] = _events(event_log, "lictor.driver.sealed.v1")
    assert sealed.data["already_met"] == ["IVA-002"]
    assert sealed.data["stub_impostors"] is None
    step = runner.paths[0].parent
    for mode in ("control", "mas", "menos"):
        log = (step / f"interfaz-{mode}" / "acceptance.log").read_text(encoding="utf-8")
        assert log.startswith("Comando: ")


def test_check_red_tells_the_impostor_where_the_boundary_lies_in_each_clone(
    tmp_path: Path,
) -> None:
    use_case, request, _, _, runner, _ = _interface_check(tmp_path, _proved_reports())

    use_case.execute(request)

    told = [t for t in runner.told if t is not None]
    for entry in told:
        [writable], [driver] = entry["writable"], entry["driver"]
        assert Path(writable).name == "src"
        assert Path(driver).parts[-2:] == ("tests", "acceptance")
        assert Path(writable).parent == Path(driver).parent.parent
    assert len({entry["writable"][0] for entry in told}) == len(told), "un clon por modo"


def test_check_red_a_new_test_that_survives_an_alteration_is_another_try(tmp_path: Path) -> None:
    reports = {**_proved_reports(), "menos": (_IFACE_RED, 1)}
    use_case, request, event_log, spec_source, _, vcs = _interface_check(tmp_path, reports)

    result = use_case.execute(request)

    assert result.ok is False
    rejection = _rejection(event_log)
    assert (rejection.data["reason"], rejection.data["final"]) == ("sin_prueba", False)
    error = str(rejection.data["error"])
    assert "sigue pasando cuando la interfaz devuelve otro valor (modo menos)" in error
    assert "lictor register" not in error
    spec_source.write_seal.assert_not_called()
    vcs.advance_head.assert_not_called()
    assert _events(event_log, "lictor.scenario.green.v1") == []


def test_check_red_a_surviving_test_on_the_last_attempt_carries_the_hint(tmp_path: Path) -> None:
    reports = {**_proved_reports(), "mas": (_IFACE_RED, 1)}
    use_case, request, event_log, _, _, _ = _interface_check(
        tmp_path, reports, driver_attempts=3, max_driver_attempts=3
    )

    with pytest.raises(DriverError, match="tras 3 intentos") as raised:
        use_case.execute(request)

    assert "`lictor register IVA-002`" in str(raised.value)
    assert _rejection(event_log).data["final"] is True


def test_check_red_what_no_retry_can_change_stops_for_a_person(tmp_path: Path) -> None:
    """The interface returns an object the impostor can't alter: the agent can't change that."""
    untouched = _suite(
        _iface_xml("test_iva_001", "assertion"), _iface_xml("test_iva_002", altered=0)
    )
    reports = {**_proved_reports(), "mas": (untouched, 1), "menos": (untouched, 1)}
    use_case, request, event_log, spec_source, _, _ = _interface_check(tmp_path, reports)

    with pytest.raises(DriverError) as raised:
        use_case.execute(request)

    message = str(raised.value)
    assert raised.value.exit_code == 10
    assert "Este test no lo cambies" in message
    assert "Lictor no pudo comprobar IVA-002: lo que la interfaz les devuelve" in message
    assert "Otro intento del driver no lo cambiaría" in message
    assert "`lictor register IVA-002`" in message
    assert "corrígelo" not in message
    rejection = _rejection(event_log)
    assert (rejection.data["reason"], rejection.data["final"]) == ("sin_prueba", True)
    spec_source.write_seal.assert_not_called()


def test_check_red_a_command_only_a_runner_invokes_stops_for_a_person_at_once(
    tmp_path: Path,
) -> None:
    """Through a library the impostor alters nothing: the agent can't change that either."""
    runner_call = (
        '<testcase name="test_iva_002"><properties>'
        '<property name="lictor.interface.calls" value="0"/>'
        '<property name="lictor.interface.via" value="3"/>'
        '<property name="lictor.interface.via.when" value="3"/>'
        '<property name="lictor.interface.entry.when" value="typer.testing"/>'
        "</properties></testcase>"
    )
    control = _suite(_iface_xml("test_iva_001", "assertion"), runner_call)
    reports = {**_proved_reports(), "control": (control, 1)}
    use_case, request, event_log, spec_source, _, _ = _interface_check(tmp_path, reports)

    with pytest.raises(DriverError) as raised:
        use_case.execute(request)

    message = str(raised.value)
    assert raised.value.exit_code == 10
    assert "Este test no lo cambies" in message
    assert "Lictor no pudo comprobar IVA-002, a través de typer.testing:" in message
    assert "`lictor register IVA-002`" in message
    assert "no llama a la interfaz" not in message
    assert _rejection(event_log).data["final"] is True
    spec_source.write_seal.assert_not_called()


def test_check_red_an_impostor_run_that_crashed_proves_nothing(tmp_path: Path) -> None:
    reports = {**_proved_reports(), "mas": ("", 2)}
    use_case, request, event_log, spec_source, _, _ = _interface_check(tmp_path, reports)

    with pytest.raises(DriverError) as raised:
        use_case.execute(request)

    assert "con el modo mas del impostor no dejó un informe válido" in str(raised.value)
    assert _rejection(event_log).data["reason"] == "sin_prueba"
    spec_source.write_seal.assert_not_called()


def test_check_red_an_impostor_run_that_doesn_t_map_proves_nothing(tmp_path: Path) -> None:
    lost = _suite(_iface_xml("test_iva_001", "assertion"), _iface_xml("test_otro", "assertion"))
    reports = {**_proved_reports(), "control": (lost, 1)}
    use_case, request, _, _, _, _ = _interface_check(tmp_path, reports)

    with pytest.raises(DriverError, match="no se pudo emparejar"):
        use_case.execute(request)


def test_check_red_proves_nothing_unless_every_scenario_is_proved(tmp_path: Path) -> None:
    """Once sealed, the next red would register an unproved one as a collateral: no half seal."""
    red = _suite(_iface_xml("test_iva_001"), _iface_xml("test_iva_002"))
    half = _suite(_iface_xml("test_iva_001", "assertion"), _iface_xml("test_iva_002"))
    reports = {"red": (red, 0), "control": (red, 0), "mas": (half, 1), "menos": (half, 1)}
    use_case, request, event_log, spec_source, _, vcs = _interface_check(tmp_path, reports)

    result = use_case.execute(request)

    assert result.ok is False
    error = str(_rejection(event_log).data["error"])
    assert "IVA-002" in error
    assert "IVA-001" not in error
    spec_source.write_seal.assert_not_called()
    assert vcs.commit_tree.call_count == 1  # the driver's candidate, and no registration


def test_check_red_a_reopened_scenario_never_runs_the_impostor(tmp_path: Path) -> None:
    use_case, request, _, _, runner, _ = _interface_check(tmp_path, _proved_reports())
    assert isinstance(use_case.history, MagicMock)
    use_case.history.classify.side_effect = lambda current: {
        "IVA-001": "new",
        "IVA-002": "reopened",
    }

    with pytest.raises(DriverError, match="La especificación de IVA-002 cambió"):
        use_case.execute(request)

    assert runner.told == [None]


def test_check_red_a_sealed_driver_never_runs_the_impostor(tmp_path: Path) -> None:
    use_case, request, event_log, _, runner, vcs = _interface_check(
        tmp_path, _proved_reports(), driver_fresh=False, driver_attempts=0, tree=None
    )
    vcs.commit_tree.side_effect = None
    vcs.commit_tree.return_value = Candidate("registro", "tree", "head")

    assert use_case.execute(request).ok is True

    assert runner.told == [None]
    [green] = _events(event_log, "lictor.scenario.green.v1")
    assert "proof" not in green.data


def test_check_red_tampering_during_an_impostor_run_is_a_recorded_rejection(
    tmp_path: Path,
) -> None:
    use_case, request, event_log, spec_source, runner, _ = _interface_check(
        tmp_path, _proved_reports()
    )
    state = tmp_path / ".lictor" / "seal.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}", encoding="utf-8")

    def tamper() -> None:
        if len(runner.told) == 2:  # the control run is about to report
            state.write_text('{"forjado": true}', encoding="utf-8")

    runner.on_run = tamper

    result = use_case.execute(request)

    assert result.ok is False
    assert _rejection(event_log).data["reason"] == "L0-estado"
    spec_source.write_seal.assert_not_called()


def test_check_red_a_profile_without_the_impostor_runs_nothing_more(tmp_path: Path) -> None:
    use_case, request, event_log, _, runner, _ = _interface_check(
        tmp_path, _proved_reports(), interface_impostor=None
    )

    result = use_case.execute(request)

    assert result.ok is False
    assert runner.told == [None]
    assert "este perfil no puede comprobar" in str(_rejection(event_log).data["error"])


def _without_stubs(dest: Path) -> None:
    (dest / "src").mkdir(parents=True)
    (dest / "src" / "iva.py").write_text("def general():\n    return 21\n", encoding="utf-8")


def test_check_red_stub_impostors_with_no_stub_run_nothing_and_say_so(tmp_path: Path) -> None:
    """In a project with code there is nothing to replace: the count says so (ADR-0011, 7)."""
    red = _suite(_iface_xml("test_iva_001", "assertion"), _iface_xml("test_iva_002", "assertion"))
    use_case, request, event_log, spec_source, runner, vcs = _interface_check(
        tmp_path, {"red": (red, 1)}, impostors=_IMPOSTORS
    )
    vcs.export_tree.side_effect = lambda _rev, dest: _without_stubs(dest)

    assert use_case.execute(request).ok is True

    assert runner.told == [None]
    spec_source.write_seal.assert_called_once()
    [sealed] = _events(event_log, "lictor.driver.sealed.v1")
    assert sealed.data["stub_impostors"] == 0


def test_check_red_counts_the_stubs_the_impostors_replaced(tmp_path: Path) -> None:
    reports = {
        "raise NotImplementedError": (_suite(_RED), 1),
        "return None": (
            _suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002", "assertion")),
            1,
        ),
        "return True": (
            _suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002", "assertion")),
            1,
        ),
    }
    use_case, request, event_log, _, _ = _impostor_check(tmp_path, reports)

    assert use_case.execute(request).ok is True

    [sealed] = _events(event_log, "lictor.driver.sealed.v1")
    assert sealed.data["stub_impostors"] == 1


def test_check_red_a_stub_failure_with_no_stub_to_replace_is_refused(tmp_path: Path) -> None:
    red = _suite(
        _case_xml("test_iva_001", "not_implemented"), _case_xml("test_iva_002", "assertion")
    )
    use_case, request, event_log, _, runner, vcs = _interface_check(
        tmp_path, {"red": (red, 1)}, impostors=_IMPOSTORS
    )
    vcs.export_tree.side_effect = lambda _rev, dest: _without_stubs(dest)

    result = use_case.execute(request)

    assert result.ok is False
    rejection = _rejection(event_log)
    assert rejection.data["reason"] == "impostor"
    assert "no hay ningún stub que sustituir" in str(rejection.data["error"])
    assert runner.told == [None]


def _stepped_xml(name: str, failure: str = "", step: str = "", **counts: int) -> str:
    """A case with the impostor's counts by step (`calls_then=1`) and the step it failed in."""
    props = [(f"lictor.interface.{key.replace('_', '.')}", str(n)) for key, n in counts.items()]
    for total in ("calls", "altered"):
        props.append(
            (
                f"lictor.interface.{total}",
                str(sum(counts.get(f"{total}_{step}", 0) for step in ("given", "when", "then"))),
            )
        )
    if failure:
        props += [
            ("lictor.failure", failure),
            ("lictor.frame", "tests/acceptance/t.py"),
            ("lictor.step", step),
        ]
    body = "".join(f'<property name="{key}" value="{value}"/>' for key, value in props)
    fails = '<failure message="m"/>' if failure else ""
    return f'<testcase name="{name}"><properties>{body}</properties>{fails}</testcase>'


def _measured_reports(menos: str) -> dict[str, tuple[str, int]]:
    red = _stepped_xml("test_iva_001", "assertion", "then")
    control = _stepped_xml("test_iva_002", calls_given=1, calls_then=1)
    caught = _stepped_xml("test_iva_002", "assertion", "then", calls_then=1, altered_then=1)
    return {
        "red": (_suite(red, _stepped_xml("test_iva_002")), 1),
        "control": (_suite(red, control), 1),
        "mas": (_suite(red, caught), 1),
        "menos": (_suite(red, menos), 1),
    }


def test_check_red_measures_what_the_interface_proved_of_a_scenario(tmp_path: Path) -> None:
    caught = _stepped_xml("test_iva_002", "impostor", "when", calls_when=1, altered_when=1)
    use_case, request, event_log, _, _, _ = _interface_check(tmp_path, _measured_reports(caught))

    assert use_case.execute(request).ok is True

    [measured] = _events(event_log, "lictor.driver.measured.v1")
    types = [call.args[0].type for call in event_log.append.call_args_list]
    assert types.index("lictor.driver.measured.v1") < types.index("lictor.driver.sealed.v1")
    assert measured.data["red"] == {
        "IVA-001": {"step": "then", "caught": "entonces", "status": "failure"}
    }
    found = measured.data["interface"]["IVA-002"]
    assert found["outcome"] == "probado"
    assert found["calls"] == {"given": 1, "when": 0, "then": 1, "outside": 0}
    assert found["failed_step"] == {"mas": "then", "menos": "when"}
    assert found["altered"]["menos"] == {"given": 0, "when": 1, "then": 0, "outside": 0}
    assert measured.data["stubs"] is None


def test_check_red_measures_a_driver_it_rejects_before_rejecting_it(tmp_path: Path) -> None:
    """The test checks the result in its When: caught, but not where a Then would catch it."""
    in_the_when = _stepped_xml("test_iva_002", "assertion", "when", calls_when=1, altered_when=1)
    red = _stepped_xml("test_iva_001", "assertion", "then")
    reports = {**_measured_reports(in_the_when), "mas": (_suite(red, in_the_when), 1)}
    use_case, request, event_log, _, _, _ = _interface_check(tmp_path, reports)

    assert use_case.execute(request).ok is False

    [measured] = _events(event_log, "lictor.driver.measured.v1")
    rejection = _rejection(event_log)
    assert measured.data["step"] == rejection.data["evidence"]["path"]
    found = measured.data["interface"]["IVA-002"]
    assert found["outcome"] == "fuera_del_entonces"
    assert found["failed_step"] == {"mas": "when", "menos": "when"}
    assert (rejection.data["reason"], rejection.data["final"]) == ("sin_prueba", False)
    error = str(rejection.data["error"])
    assert "falla, pero por una aserción de un Cuando (modo mas: m), no de un Entonces" in error
    assert "compáralo en el Entonces" in error


def _mixed_reports() -> dict[str, tuple[str, int]]:
    """IVA-002 gets nothing alterable; IVA-003 never calls the interface, which can be fixed."""
    untouched = _stepped_xml("test_iva_002", calls_then=1)
    red = _stepped_xml("test_iva_001", "assertion", "then")
    rest = (untouched, _stepped_xml("test_iva_003"))
    return {
        "red": (_suite(red, _stepped_xml("test_iva_002"), _stepped_xml("test_iva_003")), 1),
        **{mode: (_suite(red, *rest), 1) for mode in ("control", "mas", "menos")},
    }


def _mixed_check(
    tmp_path: Path, **overrides: Any
) -> tuple[CheckRed, CheckRedRequest, MagicMock, MagicMock]:
    use_case, request, event_log, spec_source, _, _ = _interface_check(
        tmp_path, _mixed_reports(), all_scenario_ids=("IVA-001", "IVA-002", "IVA-003"), **overrides
    )
    assert isinstance(use_case.history, MagicMock)
    use_case.history.classify.side_effect = lambda current: dict.fromkeys(current, "new")
    return use_case, request, event_log, spec_source


def test_check_red_tells_only_the_person_why_a_test_couldn_t_be_judged(tmp_path: Path) -> None:
    """A retry for another scenario: the agent reads not to change the one it can't fix."""
    use_case, request, event_log, spec_source = _mixed_check(tmp_path)

    assert use_case.execute(request).ok is False

    rejection = _rejection(event_log)
    assert (rejection.data["reason"], rejection.data["final"]) == ("sin_prueba", False)
    error = str(rejection.data["error"])
    assert "IVA-002 pasa con un driver nuevo. Este test no lo cambies" in error
    assert "IVA-003 pasa con un driver nuevo, pero no llama a la interfaz" in error
    assert "Lictor no pudo comprobar" not in error
    spec_source.write_seal.assert_not_called()


def test_check_red_on_the_last_attempt_tells_the_person_why_too(tmp_path: Path) -> None:
    use_case, request, _, _ = _mixed_check(tmp_path, driver_attempts=3, max_driver_attempts=3)

    with pytest.raises(DriverError) as raised:
        use_case.execute(request)

    lines = str(raised.value).splitlines()
    note = next(i for i, line in enumerate(lines) if line.startswith("Lictor no pudo comprobar"))
    assert lines[note].startswith("Lictor no pudo comprobar IVA-002: lo que la interfaz")
    assert "`lictor register IVA-002 IVA-003`" in lines[note + 1]


def test_check_red_measures_how_each_new_test_fared_with_each_stub_constant(
    tmp_path: Path,
) -> None:
    reports = {
        "raise NotImplementedError": (_suite(_RED), 1),
        "return None": (
            _suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002", "error")),
            1,
        ),
        "return True": (
            _suite(_case_xml("test_iva_001"), _case_xml("test_iva_002", "assertion")),
            1,
        ),
    }
    use_case, request, event_log, _, _ = _impostor_check(tmp_path, reports)

    assert use_case.execute(request).ok is True

    [measured] = _events(event_log, "lictor.driver.measured.v1")
    assert measured.data["stubs"] == {
        "IVA-001": {
            "by_constant": {"None": "entonces", "True": "pasa"},
            "outcome": "probado",
            "interface": None,
            "verdict": "aceptado",
        },
        "IVA-002": {
            "by_constant": {"None": "no_aserta", "True": "entonces"},
            "outcome": "probado",
            "interface": None,
            "verdict": "aceptado",
        },
    }
    assert measured.data["red"]["IVA-001"] == {
        "step": "then",
        "caught": "sin_implementar",
        "status": "failure",
    }


def test_check_red_measures_the_stub_runs_of_a_driver_they_reject(tmp_path: Path) -> None:
    passing = (_suite(_case_xml("test_iva_001", "assertion"), _case_xml("test_iva_002")), 1)
    reports = {"raise NotImplementedError": (_suite(_RED), 1), "return": passing}
    use_case, request, event_log, _, _ = _impostor_check(tmp_path, reports)

    assert use_case.execute(request).ok is False

    [measured] = _events(event_log, "lictor.driver.measured.v1")
    assert measured.data["stubs"]["IVA-002"]["outcome"] == "a_la_interfaz"
    assert measured.data["stubs"]["IVA-002"]["verdict"] == "rechazado"
    assert _rejection(event_log).data["reason"] == "impostor"


def test_check_red_measures_nothing_of_a_sealed_driver(tmp_path: Path) -> None:
    use_case, request, event_log, _, _, _ = _interface_check(
        tmp_path, _proved_reports(), driver_fresh=False
    )

    use_case.execute(request)

    assert _events(event_log, "lictor.driver.measured.v1") == []


# The impostor of the Gherkin (ADR-0016): what the interface can't prove is weighed with the
# scenario's values.
_WITH_GHERKIN = InterfaceImpostor("LICTOR_IMPOSTOR", "control", ("mas", "menos"), "LICTOR_GHERKIN")
_UNREAD = json.dumps([["el IVA es 21", "tipo", "21"]])


def _gherkin_xml(name: str, failure: str = "", altered: str = "0", unread: str = "[]") -> str:
    """A test whose Then receives one value of the Gherkin, as both impostors report it."""
    case = _iface_xml(name, failure)
    props = {
        "lictor.interface.trace": "0:aa:x1",
        "lictor.gherkin.values": "1",
        "lictor.gherkin.alterable": "0",
        "lictor.gherkin.unread": unread,
        "lictor.gherkin.altered": altered,
    }
    body = "".join(
        f'<property name="{key}" value="{html.escape(value)}"/>' for key, value in props.items()
    )
    return case.replace("</properties>", body + "</properties>")


class _GherkinRunner(_InterfaceRunner):
    """Reports by the interface's mode and what the Gherkin was told: `gherkin-<mode>-<n>`."""

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandOutput:
        raw = (env or {}).get("LICTOR_GHERKIN")
        gherkin = json.loads(raw) if raw else None
        self.gherkins.append(gherkin)
        if gherkin is not None and "targets" in gherkin:
            name = (
                "bis"
                if gherkin["mode"] == "control"
                else (f"gherkin-{gherkin['mode']}-{gherkin['target']}")
            )
            self.reports = {**self.reports, "control": self.reports[name]}
        elif gherkin is not None:
            self.reports = {**self.reports, "control": self.reports["first"]}
        return super().run(cmd, cwd, timeout_s, env)

    gherkins: list[dict[str, Any] | None]


def _gherkin_check(
    tmp_path: Path, reports: dict[str, tuple[str, int]]
) -> tuple[CheckRed, CheckRedRequest, MagicMock, _GherkinRunner]:
    use_case, request, event_log, _, _, _ = _interface_check(
        tmp_path, reports, interface_impostor=_WITH_GHERKIN
    )
    runner = _GherkinRunner(reports)
    runner.gherkins = []
    use_case.environment.runner = runner
    return use_case, request, event_log, runner


def test_check_red_a_test_that_reads_only_its_scenario_s_value_goes_to_the_person(
    tmp_path: Path,
) -> None:
    """It survives what the interface returns, and fails with another value of the scenario."""
    control = _suite(_iface_xml("test_iva_001", "assertion"), _gherkin_xml("test_iva_002"))
    caught = _suite(_gherkin_xml("test_iva_002", "assertion", altered="1"))
    reports = {
        "red": (_IFACE_RED, 1),
        "first": (control, 1),
        "mas": (_IFACE_RED, 1),
        "menos": (_IFACE_RED, 1),
        "bis": (_suite(_gherkin_xml("test_iva_002")), 0),
        "gherkin-mas-0": (caught, 1),
        "gherkin-menos-0": (caught, 1),
    }
    use_case, request, event_log, runner = _gherkin_check(tmp_path, reports)

    with pytest.raises(DriverError) as raised:
        use_case.execute(request)

    told = [(t["mode"], t["trace"]) if t else None for t in runner.told]
    assert (
        told
        == [None, ("control", True), ("mas", False), ("menos", False)] + [("control", True)] * 3
    )
    assert runner.gherkins[1:] == [
        {"mode": "control"},
        None,
        None,
        {"mode": "control", "targets": ["IVA-002"]},
        {"mode": "mas", "target": 0, "targets": ["IVA-002"]},
        {"mode": "menos", "target": 0, "targets": ["IVA-002"]},
    ]
    step = runner.paths[0].parent
    assert {p.parent.name for p in runner.paths[4:]} == {
        "gherkin-control-bis",
        "gherkin-mas-0",
        "gherkin-menos-0",
    }
    assert step.name.endswith("-red")
    reason = str(raised.value)
    assert "Este test no lo cambies" in reason
    assert "con otro valor del escenario en su lugar, falla" in reason
    assert "en tests/acceptance/t.py" in reason
    assert "Lee antes esos tests" in reason
    assert "lictor register" not in reason
    [measured] = _events(event_log, "lictor.driver.measured.v1")
    found = measured.data["interface"]["IVA-002"]
    assert (found["outcome"], found["interface"]) == ("solo_el_valor", "sobrevive")
    assert found["gherkin"]["read"] == [0]


def test_check_red_a_value_the_then_never_reads_is_told_to_the_agent_without_more_runs(
    tmp_path: Path,
) -> None:
    control = _suite(
        _iface_xml("test_iva_001", "assertion"), _gherkin_xml("test_iva_002", unread=_UNREAD)
    )
    reports = {
        "red": (_IFACE_RED, 1),
        "first": (control, 1),
        "mas": (_IFACE_CAUGHT, 1),
        "menos": (_IFACE_CAUGHT, 1),
    }
    use_case, request, _, runner = _gherkin_check(tmp_path, reports)

    result = use_case.execute(request)

    assert result.ok is False
    assert len(runner.told) == 4  # the red and the interface's three: the code says it
    assert result.last_error is not None
    assert "el Entonces «el IVA es 21» recibe «21» del escenario y no lo usa" in result.last_error
