"""Unit tests for VerifyAttempt use case and ADR-0001 integrity snapshot."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.application.ports import Vcs
from lictor.application.usecases.verify_attempt import (
    IntegritySnapshot,
    VerificationControl,
    VerifyAttempt,
    VerifyAttemptRequest,
    control_summaries,
)
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.errors import InternalLictorError
from lictor.domain.model import Candidate, CommandOutput, TreeChange


class FakeVcs(Vcs):
    """In-memory VCS fake for testing VerifyAttempt."""

    def __init__(
        self, changed_files: list[str] | None = None, added: list[str] | None = None
    ) -> None:
        self._changed_files = list(changed_files or [])
        self._added = list(added or [])
        self.links: list[str] = []

    def status(self) -> list[str]:
        return []

    def capture(self, workdir: Path, skip: Sequence[str] = ()) -> str:
        return "fake-tree"

    def changes(self, tree: str) -> list[TreeChange]:
        return [
            TreeChange(path, "120000" if path in self.links else "100644")
            for path in self._changed_files
        ]

    def added_lines(self, tree: str) -> list[str]:
        return list(self._added)

    def commit_tree(
        self,
        tree: str,
        message: str,
        trailers: dict[str, str] | None = None,
    ) -> Candidate:
        self.candidate_tree = tree
        self.candidate_trailers = trailers
        return Candidate(sha="fake-candidate", tree="fake-tree", parent="fake-sha-head")

    def export_tree(self, rev: str, dest: Path) -> None:
        dest.mkdir(parents=True)

    def export_copy(self, rev: str, dest: Path) -> None:
        dest.mkdir(parents=True)

    def advance_head(self, sha: str, expected: str) -> None:
        raise NotImplementedError

    def get_tree(self, rev: str) -> str:
        return "fake-tree"

    def get_trailers(self, key: str, commit_ref: str | None = None) -> list[str]:
        return []

    def get_head_sha(self) -> str:
        return "fake-sha-head"

    def get_head_message(self) -> str:
        return "fake head commit message"


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
        tree="agent-tree",
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


def test_verify_attempt_l0_seal_tampered(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path, sealed=True, seal_tampered=True)
    vcs = FakeVcs(changed_files=["src/code.py"])
    runner = FakeRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        tree="agent-tree",
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is False
    assert result.failed_at == "L0-sello"


def test_verify_attempt_l0_seal_detects_unsealed_file_in_driver_paths(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path, sealed=True)
    extra_driver = project_root / "tests" / "acceptance" / "extra_driver.py"
    extra_driver.write_text("def test_extra(): pass\n", encoding="utf-8")

    vcs = FakeVcs(changed_files=["src/code.py"])
    runner = FakeRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        tree="agent-tree",
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        paths_driver=("tests/acceptance/",),
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is False
    assert result.failed_at == "L0-sello"


def test_verify_attempt_l0_seal_detects_symlink_outside_repo(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path, sealed=True)
    outside_file = tmp_path.parent / f"outside_{tmp_path.name}.txt"
    outside_file.write_text("evil", encoding="utf-8")
    symlink_feat = project_root / "features" / "external.feature"
    symlink_feat.symlink_to(outside_file)

    vcs = FakeVcs(changed_files=["src/code.py"])
    runner = FakeRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        tree="agent-tree",
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        paths_driver=("tests/acceptance/",),
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is False
    assert result.failed_at == "L0-sello"
    assert "Enlace simbólico fuera del repositorio no permitido" in (result.condensed_error or "")


def test_verify_attempt_l0_suppressions_detected(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path)
    vcs = FakeVcs(changed_files=["src/code.py"], added=[" x = 1  # noqa: F401"])
    runner = FakeRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        tree="agent-tree",
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
        tree="agent-tree",
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
        tree="agent-tree",
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
        tree="agent-tree",
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
        def run(self, *args: Any, **kwargs: Any) -> CommandOutput:
            (project_root / ".lictor" / "seal.json").write_text("hacked!", encoding="utf-8")
            return CommandOutput(returncode=0, stdout="", stderr="", duration_ms=10)

    runner = MaliciousRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        tree="agent-tree",
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


def test_verify_attempt_adr0001_integrity_snapshot_monitors_root_lictor_json(
    tmp_path: Path,
) -> None:
    project_root, run_id = _setup_project(tmp_path)
    (project_root / "lictor.json").write_text('{"profile": "python-uv"}', encoding="utf-8")
    vcs = FakeVcs(changed_files=["src/code.py"])

    class MaliciousRunner(FakeRunner):
        def run(self, *args: Any, **kwargs: Any) -> CommandOutput:
            (project_root / "lictor.json").write_text('{"hacked": true}', encoding="utf-8")
            return CommandOutput(returncode=0, stdout="", stderr="", duration_ms=10)

    runner = MaliciousRunner()
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        tree="agent-tree",
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
    assert "hacked" not in (project_root / "lictor.json").read_text(encoding="utf-8")


def test_verify_attempt_control_allow_empty_junit(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path)
    vcs = FakeVcs(changed_files=["src/code.py"])
    runner = FakeRunner()
    # Pytest returns 5 when no tests are collected
    runner.set_response(
        "pytest",
        CommandOutput(returncode=5, stdout="", stderr="no tests ran", duration_ms=15),
    )
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader(allow_empty=False)}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        tree="agent-tree",
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        controls=(
            VerificationControl(
                id="unit",
                level="L2",
                run="pytest tests/unit --junitxml {out}",
                report="junit",
                mode="block",
                allow_empty=True,
            ),
        ),
        acceptance_run="echo passed",
        all_scenario_ids=("IVA-001",),
    )

    # Acceptance xml passed
    xml = """<testsuite><testcase name="test_iva_001" status="passed" /></testsuite>"""
    runner.set_response(
        "echo passed",
        CommandOutput(returncode=0, stdout=xml, stderr="", duration_ms=10),
    )

    result = verifier.execute(req)
    assert result.ok is True
    assert result.control_results["unit"].ok is True


def test_verify_attempt_preexisting_suppressions_in_tracked_file_ignored(
    tmp_path: Path,
) -> None:
    project_root, run_id = _setup_project(tmp_path)
    # File has existing suppression on line 1, but agent only added line 2
    code_file = project_root / "src" / "code.py"
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_text("x = 1  # noqa\ny = 2\n", encoding="utf-8")

    vcs = FakeVcs(changed_files=["src/code.py"], added=["y = 2"])
    runner = FakeRunner()
    xml = """<testsuite><testcase name="test_iva_001" status="passed" /></testsuite>"""
    runner.default_response = CommandOutput(returncode=0, stdout=xml, stderr="", duration_ms=10)
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}

    verifier = VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root)
    req = VerifyAttemptRequest(
        tree="agent-tree",
        scenario_id="IVA-001",
        run_id=run_id,
        role="implement",
        paths_writable=("src/",),
        suppressions=("# noqa",),
        acceptance_run="pytest",
        all_scenario_ids=("IVA-001",),
    )

    result = verifier.execute(req)
    assert result.ok is True


class _PlantingRunner(FakeRunner):
    """Writes to the report path a command receives, as the command itself would."""

    def __init__(self, reports: dict[str, tuple[str, int]]) -> None:
        super().__init__()
        self.reports = reports
        self.paths: list[Path] = []

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandOutput:
        report, code = self.reports.get(cmd[0], ("", 0))
        out = Path(cmd[-1]) if cmd[-1].endswith(".xml") else None
        if out is not None:
            self.paths.append(out)
            if report:
                out.write_text(report, encoding="utf-8")
        super().run(cmd, cwd, timeout_s, env)
        return CommandOutput(returncode=code, stdout=f"salida de {cmd[0]}", stderr="stderr crudo")


_GREEN_REPORT = (
    '<testsuite><testcase name="test_iva_001"/><testcase name="test_iva_002"/></testsuite>'
)
_PENDING_REPORT = (
    '<testsuite><testcase name="test_iva_001"/>'
    '<testcase name="test_iva_002"><failure message="pendiente">traza</failure></testcase>'
    "</testsuite>"
)


def _verify(
    tmp_path: Path, runner: FakeRunner, **overrides: Any
) -> tuple[VerifyAttempt, VerifyAttemptRequest]:
    project_root, run_id = _setup_project(tmp_path)
    vcs = FakeVcs(changed_files=["src/code.py"])
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}
    fields: dict[str, Any] = {
        "scenario_id": "IVA-001",
        "run_id": run_id,
        "paths_writable": ("src/",),
        "acceptance_run": "suite --junitxml {out}",
        "all_scenario_ids": ("IVA-001", "IVA-002"),
        "attempt": 2,
        "tree": "agent-tree",
    }
    return (
        VerifyAttempt(vcs=vcs, runner=runner, readers=readers, project_root=project_root),
        VerifyAttemptRequest(**{**fields, **overrides}),
    )


def test_verify_report_left_by_a_previous_step_does_not_hide_a_crash(tmp_path: Path) -> None:
    """A control runs project code: it could leave a report at the acceptance path."""

    class Planter(FakeRunner):
        def run(
            self,
            cmd: Sequence[str],
            cwd: Path,
            timeout_s: int | None = None,
            env: dict[str, str] | None = None,
        ) -> CommandOutput:
            if cmd[0] == "suite":
                return CommandOutput(returncode=3, stdout="", stderr="")
            step = next(tmp_path.glob(".lictor/runs/*/IVA-001/*-verify"))
            (step / "acceptance.xml").write_text(_GREEN_REPORT, encoding="utf-8")
            return CommandOutput(returncode=0, stdout="", stderr="")

    control = VerificationControl(id="lint", level="L1", run="lint", report="exit-code")
    verifier, req = _verify(tmp_path, Planter(), controls=(control,))

    result = verifier.execute(req)

    assert result.ok is False
    assert result.failed_at == "acceptance"
    assert "código 3" in (result.condensed_error or "")


def test_verify_suite_crash_without_report_says_so_and_keeps_its_output(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, _PlantingRunner({"suite": ("", 3)}))

    result = verifier.execute(req)

    assert result.ok is False
    assert result.failed_at == "acceptance"
    condensed = result.condensed_error or ""
    assert "código 3" in condensed
    assert "salida de suite" in condensed
    assert "stderr crudo" in condensed
    assert "No se encontró" not in condensed


def test_verify_crashed_suite_cannot_hide_behind_a_passing_report(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, _PlantingRunner({"suite": (_GREEN_REPORT, 3)}))

    result = verifier.execute(req)

    assert result.ok is False
    assert "código 3" in (result.condensed_error or "")


def test_verify_unreadable_report_reason_survives_a_long_output(tmp_path: Path) -> None:
    class Noisy(_PlantingRunner):
        def run(
            self,
            cmd: Sequence[str],
            cwd: Path,
            timeout_s: int | None = None,
            env: dict[str, str] | None = None,
        ) -> CommandOutput:
            base = super().run(cmd, cwd, timeout_s, env)
            return replace(base, stdout="\n".join(f"linea {i}" for i in range(500)))

    verifier, req = _verify(tmp_path, Noisy({"suite": ("<roto", 1)}))

    assert "Failed to parse JUnit XML" in (verifier.execute(req).condensed_error or "")


def test_verify_gates_need_the_step_dir_from_execute(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, FakeRunner(), paths_writable=("otro/",))

    with pytest.raises(InternalLictorError):
        verifier._check_routes(req)


def test_verify_failed_exit_without_failed_tests_is_not_green(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, _PlantingRunner({"suite": (_GREEN_REPORT, 1)}))

    result = verifier.execute(req)

    assert result.ok is False
    assert "ningún test" in (result.condensed_error or "")


def test_verify_pending_red_scenarios_are_not_a_crash(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, _PlantingRunner({"suite": (_PENDING_REPORT, 1)}))

    assert verifier.execute(req).ok is True


def test_verify_failure_exit_codes_come_from_the_request(tmp_path: Path) -> None:
    runner = _PlantingRunner({"suite": (_PENDING_REPORT, 2)})
    verifier, req = _verify(tmp_path, runner, acceptance_failure_exit_codes=(2,))

    assert verifier.execute(req).ok is True


def test_verify_unparsable_report_explains_why(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, _PlantingRunner({"suite": ("<roto", 1)}))

    result = verifier.execute(req)

    assert result.ok is False
    assert "Failed to parse JUnit XML" in (result.condensed_error or "")


def test_verify_condensed_error_omits_pending_but_the_full_error_keeps_them(
    tmp_path: Path,
) -> None:
    cases = "".join(
        f'<testcase name="test_iva_{i:03d}"><failure message="pendiente-{i}">'
        f"{'traza ' * 200}</failure></testcase>"
        for i in range(1, 31)
    )
    report = f"<testsuite>{cases}</testsuite>"
    ids = tuple(f"IVA-{i:03d}" for i in range(1, 31))
    runner = _PlantingRunner({"suite": (report, 1)})
    verifier, req = _verify(tmp_path, runner, scenario_id="IVA-030", all_scenario_ids=ids)

    result = verifier.execute(req)

    condensed = result.condensed_error or ""
    assert result.failed_at == "acceptance"
    assert "Escenario IVA-030 no pasó" in condensed
    assert "pendiente-30" in condensed
    assert "pendiente-1\n" not in condensed
    assert result.error_path is not None
    full = (tmp_path / result.error_path).read_text(encoding="utf-8")
    assert "pendiente-1\n" in full
    assert "salida de suite" in full


def test_verify_regression_names_the_scenario_and_omits_the_rest(tmp_path: Path) -> None:
    report = (
        '<testsuite><testcase name="test_iva_001"><failure message="rota"/></testcase>'
        '<testcase name="test_iva_002"/>'
        '<testcase name="test_iva_003"><failure message="pendiente"/></testcase></testsuite>'
    )
    verifier, req = _verify(
        tmp_path,
        _PlantingRunner({"suite": (report, 1)}),
        scenario_id="IVA-002",
        green_scenario_ids=("IVA-001",),
        all_scenario_ids=("IVA-001", "IVA-002", "IVA-003"),
    )

    condensed = verifier.execute(req).condensed_error or ""

    assert "Regresión detectada en escenario verde previo: IVA-001" in condensed
    assert "rota" in condensed
    assert "pendiente" not in condensed


def test_verify_reports_of_the_same_attempt_do_not_overwrite_each_other(tmp_path: Path) -> None:
    runner = _PlantingRunner({"suite": (_GREEN_REPORT, 0)})
    verifier, req = _verify(tmp_path, runner)

    verifier.execute(req)
    verifier.execute(req)

    first, second = runner.paths
    assert first.parent.name == "002-verify"
    assert first.parent.parent.name == "IVA-001"
    assert second.parent.name == "002-verify-r1"
    assert first.is_file()


def test_verify_full_error_of_each_gate_lands_in_runs(tmp_path: Path) -> None:
    project_root, run_id = _setup_project(tmp_path)
    verifier = VerifyAttempt(
        vcs=FakeVcs(changed_files=["pyproject.toml"]),
        runner=FakeRunner(),
        readers={},
        project_root=project_root,
    )
    req = VerifyAttemptRequest(
        scenario_id="IVA-001", run_id=run_id, paths_writable=("src/",), tree="agent-tree"
    )

    result = verifier.execute(req)

    assert result.failed_at == "L0-rutas"
    assert result.error_path == f".lictor/runs/{run_id}/IVA-001/001-verify/error.txt"
    assert "pyproject.toml" in (tmp_path / result.error_path).read_text(encoding="utf-8")


def test_verify_green_attempt_writes_no_error(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, _PlantingRunner({"suite": (_GREEN_REPORT, 0)}))

    result = verifier.execute(req)

    assert result.error_path is None
    assert not list(tmp_path.rglob("error.txt"))


def test_verify_records_result_and_output_of_every_control(tmp_path: Path) -> None:
    controls = (
        VerificationControl(id="lint", level="L1", run="lint", report="exit-code"),
        VerificationControl(id="mut", level="L5", run="mut", report="exit-code", mode="measure"),
    )
    runner = _PlantingRunner({"suite": (_GREEN_REPORT, 0), "mut": ("", 7)})
    verifier, req = _verify(tmp_path, runner, controls=controls)

    result = verifier.execute(req)

    assert result.ok is True
    assert result.condensed_error is None
    measured = result.control_results["mut"]
    assert (measured.ok, measured.exit_code, measured.mode) == (False, 7, "measure")
    assert measured.output_path is not None
    log = (tmp_path / measured.output_path).read_text(encoding="utf-8")
    assert "salida de mut" in log
    assert "stderr crudo" in log
    assert "7" in log
    passed = result.control_results["lint"]
    assert (passed.ok, passed.exit_code, passed.mode) == (True, 0, "block")
    assert passed.output_path is not None
    assert "salida de lint" in (tmp_path / passed.output_path).read_text(encoding="utf-8")
    accepted = result.control_results["acceptance"]
    assert accepted.exit_code == 0
    assert accepted.output_path is not None
    assert "salida de suite" in (tmp_path / accepted.output_path).read_text(encoding="utf-8")


def test_verify_blocking_junit_control_without_report_shows_its_output(tmp_path: Path) -> None:
    control = VerificationControl(
        id="unit", level="L2", run="unit --junitxml {out}", report="junit"
    )
    runner = _PlantingRunner({"unit": ("", 4)})
    verifier, req = _verify(tmp_path, runner, controls=(control,))

    result = verifier.execute(req)

    assert result.failed_at == "unit"
    assert "salida de unit" in (result.condensed_error or "")
    assert result.control_results["unit"].ok is False


def test_verify_tampering_during_a_control_keeps_what_was_recorded(tmp_path: Path) -> None:
    class Tamperer(FakeRunner):
        def run(
            self,
            cmd: Sequence[str],
            cwd: Path,
            timeout_s: int | None = None,
            env: dict[str, str] | None = None,
        ) -> CommandOutput:
            if cmd[0] == "second":
                (tmp_path / ".lictor" / "seal.json").write_text("{}", encoding="utf-8")
            return CommandOutput(returncode=0, stdout="", stderr="")

    controls = (
        VerificationControl(id="first", level="L1", run="first", report="exit-code"),
        VerificationControl(id="second", level="L1", run="second", report="exit-code"),
    )
    verifier, req = _verify(tmp_path, Tamperer(), controls=controls)

    result = verifier.execute(req)

    assert result.failed_at == "L0-estado"
    assert result.control_results["first"].ok is True
    assert result.error_path is not None


def test_verify_tampering_during_acceptance_keeps_the_controls(tmp_path: Path) -> None:
    class Tamperer(FakeRunner):
        def run(
            self,
            cmd: Sequence[str],
            cwd: Path,
            timeout_s: int | None = None,
            env: dict[str, str] | None = None,
        ) -> CommandOutput:
            if cmd[0] == "suite":
                (tmp_path / ".lictor" / "seal.json").write_text("{}", encoding="utf-8")
            return CommandOutput(returncode=0, stdout="", stderr="")

    control = VerificationControl(id="lint", level="L1", run="lint", report="exit-code")
    verifier, req = _verify(tmp_path, Tamperer(), controls=(control,))

    result = verifier.execute(req)

    assert result.failed_at == "L0-estado"
    assert "lint" in result.control_results


def test_control_summaries_serialize_for_the_journal(tmp_path: Path) -> None:
    control = VerificationControl(
        id="mut", level="L5", run="mut", report="exit-code", mode="measure"
    )
    runner = _PlantingRunner({"suite": (_GREEN_REPORT, 0), "mut": ("", 7)})
    verifier, req = _verify(tmp_path, runner, controls=(control,))

    summaries = control_summaries(verifier.execute(req))

    assert summaries["mut"]["ok"] is False
    assert summaries["mut"]["exit_code"] == 7
    assert summaries["mut"]["mode"] == "measure"
    assert summaries["mut"]["output"] == (
        f".lictor/runs/{req.run_id}/IVA-001/002-verify/control_mut.log"
    )
    json.dumps(summaries)


def test_verify_runs_every_command_in_the_tree_of_the_candidate(tmp_path: Path) -> None:
    control = VerificationControl(id="lint", level="L1", run="lint", report="exit-code")
    runner = _PlantingRunner({"suite": (_GREEN_REPORT, 0)})
    verifier, req = _verify(tmp_path, runner, controls=(control,))

    result = verifier.execute(req)

    assert result.candidate == Candidate("fake-candidate", "fake-tree", "fake-sha-head")
    assert len(runner.calls) == 2
    assert {call["cwd"] for call in runner.calls} != {tmp_path}
    assert all(call["cwd"] == runner.calls[0]["cwd"] for call in runner.calls)
    assert not Path(str(runner.calls[0]["cwd"])).exists(), "el clon debía borrarse"


def test_verify_candidate_is_the_tree_the_agent_left_with_trailers(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, _PlantingRunner({"suite": (_GREEN_REPORT, 0)}))
    assert isinstance(verifier.vcs, FakeVcs)

    verifier.execute(req)

    assert verifier.vcs.candidate_tree == "agent-tree"
    assert verifier.vcs.candidate_trailers == {
        "Scenario": "IVA-001",
        "Lictor-Run": req.run_id,
        "Lictor-Attempts": "2",
    }


def test_verify_failed_attempt_has_no_candidate(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, _PlantingRunner({"suite": ("", 3)}))

    assert verifier.execute(req).candidate is None


def test_verify_gate_failure_runs_nothing(tmp_path: Path) -> None:
    runner = _PlantingRunner({})
    verifier, req = _verify(tmp_path, runner, forbidden_paths=("code.py",))

    result = verifier.execute(req)

    assert result.failed_at == "L0-rutas"
    assert result.reverted_files == ("src/code.py",)
    assert runner.calls == []


def test_verify_rejects_a_symlink_in_a_writable_path(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, _PlantingRunner({}))
    assert isinstance(verifier.vcs, FakeVcs)
    verifier.vcs.links = ["src/code.py"]

    result = verifier.execute(req)

    assert result.failed_at == "L0-rutas"


def test_verify_banned_pattern_reports_itself(tmp_path: Path) -> None:
    verifier, req = _verify(
        tmp_path, _PlantingRunner({}), suppressions=(), banned_patterns=(r"#\s*noqa",)
    )
    assert isinstance(verifier.vcs, FakeVcs)
    verifier.vcs._added = ["x = 1  # NOQA"]

    result = verifier.execute(req)

    assert result.failed_at == "L0-supresiones"
    assert r"#\s*noqa" in (result.condensed_error or "")


def _spied_environment(problems: list[str | None], runner: FakeRunner | None = None) -> MagicMock:
    environment = MagicMock(spec=VerificationEnvironment)
    environment.ensure.side_effect = problems
    if runner is not None:
        environment.run.side_effect = lambda cmd, cwd, timeout_s: runner.run(
            cmd, cwd, timeout_s, env={"VAR": "1"}
        )
    return environment


def test_verify_tampered_environment_fails_before_running_anything(tmp_path: Path) -> None:
    runner = _PlantingRunner({"suite": (_GREEN_REPORT, 0)})
    verifier, req = _verify(tmp_path, runner)
    verifier.environment = _spied_environment(["manipulado"])

    result = verifier.execute(req)

    assert result.failed_at == "L0-entorno"
    assert "manipulado" in (result.condensed_error or "")
    assert runner.calls == []


def test_verify_environment_tampered_during_the_commands_fails(tmp_path: Path) -> None:
    runner = _PlantingRunner({"suite": (_GREEN_REPORT, 0)})
    verifier, req = _verify(tmp_path, runner)
    verifier.environment = _spied_environment([None, "manipulado"], runner)

    result = verifier.execute(req)

    assert result.ok is False
    assert result.failed_at == "L0-entorno"
    assert result.candidate is None
    assert "acceptance" in result.control_results


def test_verify_commands_get_the_environment_variables(tmp_path: Path) -> None:
    runner = _PlantingRunner({"suite": (_GREEN_REPORT, 0)})
    verifier, req = _verify(tmp_path, runner)
    verifier.environment = _spied_environment([None, None], runner)

    control = VerificationControl(id="lint", level="L1", run="lint", report="exit-code")
    req = replace(req, controls=(control,))

    assert verifier.execute(req).ok is True

    assert verifier.environment.ensure.call_count == 2
    assert [call["env"] for call in runner.calls] == [{"VAR": "1"}, {"VAR": "1"}]


def test_verify_tree_dir_comes_from_execute(tmp_path: Path) -> None:
    verifier, req = _verify(tmp_path, FakeRunner())

    with pytest.raises(InternalLictorError):
        verifier._tree_dir(req)


def test_environment_manifest_is_protected_during_untrusted_windows(tmp_path: Path) -> None:
    lictor_dir = tmp_path / ".lictor"
    lictor_dir.mkdir()
    manifest = lictor_dir / "verify_env.json"
    manifest.write_text("original", encoding="utf-8")
    snapshot = IntegritySnapshot(lictor_dir, tmp_path)

    manifest.write_text("forjado", encoding="utf-8")

    assert snapshot.verify_and_restore() is False
    assert manifest.read_text(encoding="utf-8") == "original"
