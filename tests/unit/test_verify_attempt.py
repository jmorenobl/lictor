"""Unit tests for VerifyAttempt use case and ADR-0001 integrity snapshot."""

import hashlib
import json
from pathlib import Path

from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.application.ports import Vcs
from lictor.application.usecases.verify_attempt import (
    VerificationControl,
    VerifyAttempt,
    VerifyAttemptRequest,
)
from lictor.domain.model import CommandOutput


class FakeVcs(Vcs):
    """In-memory VCS fake for testing VerifyAttempt."""

    def __init__(self, changed_files: list[str] | None = None, diff_output: str = "") -> None:
        self._changed_files = list(changed_files or [])
        self._diff_output = diff_output
        self.reverted: list[str] = []
        self.reset_count = 0

    def status(self) -> list[str]:
        return list(self._changed_files)

    def reset_to_head(self) -> None:
        self.reset_count += 1
        self._changed_files.clear()

    def commit(self, message: str, trailers: dict[str, str] | None = None) -> str:
        return "fake-sha-12345"

    def get_trailers(self, key: str) -> list[str]:
        return []

    def revert_paths(self, paths: list[str] | tuple[str, ...]) -> None:
        for p in paths:
            self.reverted.append(p)
            if p in self._changed_files:
                self._changed_files.remove(p)

    def diff(self) -> str:
        return self._diff_output


def _setup_project(
    tmp_path: Path,
    sealed: bool = True,
    seal_tampered: bool = False,
) -> tuple[Path, str]:
    features_dir = tmp_path / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    feature_file = features_dir / "iva.feature"
    feature_file.write_text("Feature: IVA\n@IVA-001\nScenario: General\n", encoding="utf-8")

    driver_dir = tmp_path / "tests" / "acceptance"
    driver_dir.mkdir(parents=True, exist_ok=True)
    driver_file = driver_dir / "test_iva.py"
    driver_file.write_text("def test_iva_001(): pass\n", encoding="utf-8")

    lictor_dir = tmp_path / ".lictor"
    lictor_dir.mkdir(parents=True, exist_ok=True)
    (lictor_dir / "lictor.json").write_text("{}", encoding="utf-8")

    if sealed:
        feature_hash = hashlib.sha256(feature_file.read_bytes()).hexdigest()
        driver_hash = (
            "wrong_hash" if seal_tampered else hashlib.sha256(driver_file.read_bytes()).hexdigest()
        )
        seal_content = {
            "created": "2026-10-30T12:00:00Z",
            "run_id": "run-001",
            "files": {
                "features/iva.feature": f"sha256:{feature_hash}",
                "tests/acceptance/test_iva.py": f"sha256:{driver_hash}",
            },
        }
        (lictor_dir / "seal.json").write_text(json.dumps(seal_content), encoding="utf-8")

    return tmp_path, "run-001"


def test_verify_attempt_l0_routes_reverts_and_fails(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path)
    vcs = FakeVcs(changed_files=["src/code.py", "forbidden/exploit.py"])
    runner = FakeRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is False
    assert result.failed_at == "L0-rutas"
    assert "forbidden/exploit.py" in result.reverted_files
    assert "forbidden/exploit.py" in vcs.reverted


def test_verify_attempt_l0_seal_tampered(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path, sealed=True, seal_tampered=True)
    vcs = FakeVcs(changed_files=["src/code.py"])
    runner = FakeRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is False
    assert result.failed_at == "L0-sello"


def test_verify_attempt_l0_suppressions_detected(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path)
    diff = "+ x = 1  # noqa: F401\n"
    vcs = FakeVcs(changed_files=["src/code.py"], diff_output=diff)
    runner = FakeRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        suppressions=("# noqa",),
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is False
    assert result.failed_at == "L0-supresiones"


def test_verify_attempt_control_block_stops_pipeline(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path)
    vcs = FakeVcs(changed_files=["src/code.py"])
    runner = FakeRunner()
    # Control 'lint' fails
    runner.set_response(
        "ruff check",
        CommandOutput(returncode=1, stdout="lint error", stderr="", duration_ms=20),
    )
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        controls=(
            VerificationControl(
                id="lint", level="L1", run="ruff check", report="exit-code", mode="block"
            ),
            VerificationControl(
                id="types", level="L1", run="mypy", report="exit-code", mode="block"
            ),
        ),
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is False
    assert result.failed_at == "lint"
    assert "lint" in result.control_results
    assert "types" not in result.control_results  # Blocked before types


def test_verify_attempt_control_measure_does_not_block(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path)
    vcs = FakeVcs(changed_files=["src/code.py"])
    runner = FakeRunner()
    # 'mutmut' in mode measure fails
    runner.set_response(
        "mutmut",
        CommandOutput(returncode=1, stdout="mutation score 50%", stderr="", duration_ms=50),
    )
    # acceptance passes
    xml = """<testsuite><testcase name="test_iva_001" status="passed" /></testsuite>"""
    runner.set_response(
        "pytest",
        CommandOutput(returncode=0, stdout=xml, stderr="", duration_ms=100),
    )
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        controls=(
            VerificationControl(
                id="mutation",
                level="L5",
                run="mutmut",
                report="exit-code",
                mode="measure",
            ),
        ),
        acceptance_run="pytest",
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is True
    assert result.control_results["mutation"].ok is False


def test_verify_attempt_acceptance_regression_fails(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path)
    vcs = FakeVcs(changed_files=["src/code.py"])
    runner = FakeRunner()
    # Acceptance has IVA-001 passed, but green IVA-002 failed!
    xml = """<testsuite>
      <testcase name="test_iva_001" status="passed" />
      <testcase name="test_iva_002" status="failure"><failure>broke</failure></testcase>
    </testsuite>"""
    runner.set_response(
        "pytest",
        CommandOutput(returncode=1, stdout=xml, stderr="", duration_ms=100),
    )
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        acceptance_run="pytest",
        green_scenario_ids=("IVA-002",),
        all_scenario_ids=("IVA-001", "IVA-002"),
    )

    result = verifier.execute(req)
    assert result.ok is False
    assert result.failed_at == "acceptance"
    assert result.scenario_results["IVA-002"] == "failure"


def test_verify_attempt_adr0001_integrity_snapshot_restores_and_fails(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path)
    vcs = FakeVcs(changed_files=["src/code.py"])

    # Simulate runner that maliciously tampers with .lictor/seal.json
    class MaliciousRunner(FakeRunner):
        def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            (project_root / ".lictor" / "seal.json").write_text("hacked!", encoding="utf-8")
            return CommandOutput(returncode=0, stdout="", stderr="", duration_ms=10)

    runner = MaliciousRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        controls=(
            VerificationControl(id="check", level="L1", run="echo test", report="exit-code"),
        ),
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is False
    assert result.failed_at == "L0-estado"
    # State was restored
    assert (project_root / ".lictor" / "seal.json").read_text(encoding="utf-8") != "hacked!"
