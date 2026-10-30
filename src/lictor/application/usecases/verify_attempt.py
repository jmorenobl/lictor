"""VerifyAttempt use case with deterministic verification pipeline.

Includes ADR-0001 integrity snapshot for state protection.
"""

import contextlib
import hashlib
import json
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from lictor.application.ports import CommandRunner, ResultReader, Vcs
from lictor.domain.errors import DriverError
from lictor.domain.model import ControlResult, ScenarioId, VerifyResult
from lictor.domain.rules import condense_error, map_test_cases_to_scenarios

RoleKind = Literal["implement", "driver"]


class VerificationControlProtocol(Protocol):
    """Protocol matching control configurations without binding to Pydantic."""

    @property
    def id(self) -> str: ...

    @property
    def level(self) -> str: ...

    @property
    def run(self) -> str: ...

    @property
    def report(self) -> str: ...

    @property
    def mode(self) -> str: ...

    @property
    def allow_empty(self) -> bool: ...

    @property
    def timeout_s(self) -> int: ...


@dataclass(frozen=True)
class VerificationControl:
    """Concrete value object for verification controls."""

    id: str
    level: str
    run: str
    report: str
    mode: str = "block"
    allow_empty: bool = False
    timeout_s: int = 600


@dataclass(frozen=True)
class VerifyAttemptRequest:
    """Request payload for VerifyAttempt use case."""

    scenario_id: str | ScenarioId
    run_id: str
    role: RoleKind = "implement"
    paths_writable: Sequence[str] = ("src/", "tests/unit/")
    paths_driver: Sequence[str] = ("tests/acceptance/",)
    suppressions: Sequence[str] = ("# noqa", "# type: ignore", "# pragma: no cover")
    controls: Sequence[VerificationControlProtocol] = field(default_factory=tuple)
    acceptance_run: str = ""
    acceptance_report: str = "junit"
    acceptance_timeout_s: int = 600
    green_scenario_ids: Sequence[str] = field(default_factory=tuple)
    all_scenario_ids: Sequence[str | ScenarioId] = field(default_factory=tuple)
    error_max_chars: int = 4000


class IntegritySnapshot:
    """ADR-0001: In-memory integrity snapshot of .lictor/ metadata files."""

    MONITORED_FILES = ("seal.json", "lictor.json", "checkpoints.sqlite", "journal.jsonl")

    def __init__(self, lictor_dir: Path) -> None:
        self.lictor_dir = lictor_dir
        self.snapshots: dict[str, bytes | None] = {}
        self.take()

    def take(self) -> None:
        for name in self.MONITORED_FILES:
            target = self.lictor_dir / name
            if target.is_file():
                self.snapshots[name] = target.read_bytes()
            else:
                self.snapshots[name] = None

    def verify_and_restore(self) -> bool:
        """Check if monitored files changed. If so, restore them and return False."""
        changed = False
        for name, initial_bytes in self.snapshots.items():
            target = self.lictor_dir / name
            if initial_bytes is None:
                if target.exists():
                    changed = True
                    target.unlink(missing_ok=True)
            elif not target.is_file() or target.read_bytes() != initial_bytes:
                changed = True
                target.write_bytes(initial_bytes)
        return not changed


def _collect_added_lines(vcs: Vcs, project_root: Path) -> list[str]:
    added_lines: list[str] = []
    diff_text = vcs.diff()
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])

    for rel_path in vcs.status():
        target = project_root / rel_path
        if target.is_file():
            with contextlib.suppress(OSError):
                added_lines.extend(target.read_text(encoding="utf-8", errors="ignore").splitlines())
    return added_lines


def _is_path_allowed(rel_path: str, allowed_roots: Sequence[str]) -> bool:
    target = PurePosixPath(rel_path)
    for root_str in allowed_roots:
        root_norm = PurePosixPath(root_str.strip("/"))
        if target == root_norm or target.is_relative_to(root_norm):
            return True
    return False


class VerifyAttempt:
    """Deterministic multi-gate verification pipeline for an agent attempt."""

    def __init__(
        self,
        vcs: Vcs,
        runner: CommandRunner,
        readers: Mapping[str, ResultReader],
        project_root: Path,
    ) -> None:
        self.vcs = vcs
        self.runner = runner
        self.readers = readers
        self.project_root = project_root

    def execute(self, req: VerifyAttemptRequest) -> VerifyResult:
        """Run verification steps L0 to Acceptance sequentially."""
        # Step 1: L0-rutas
        routes_res = self._check_routes(req)
        if not routes_res.ok:
            return routes_res

        # Step 2: L0-sello (only in implement role)
        if req.role == "implement":
            seal_res = self._check_seal(req)
            if not seal_res.ok:
                return seal_res

        # Step 3: L0-supresiones
        suppressions_res = self._check_suppressions(req)
        if not suppressions_res.ok:
            return suppressions_res

        # Step 4: Controles
        control_results: dict[str, ControlResult] = {}
        for ctrl in req.controls:
            ctrl_ok, ctrl_res, failed_step = self._run_single_control(ctrl, req)
            if failed_step == "L0-estado":
                err = "El estado en .lictor/ fue modificado indebidamente"
                condensed = condense_error("L0-estado", err, max_chars=req.error_max_chars)
                return VerifyResult(ok=False, failed_at="L0-estado", condensed_error=condensed)

            control_results[ctrl.id] = ctrl_res
            if not ctrl_ok and ctrl.mode == "block":
                condensed = condense_error(
                    ctrl.id,
                    ctrl_res.error or "",
                    ctrl_res.test_cases,
                    max_chars=req.error_max_chars,
                )
                return VerifyResult(
                    ok=False,
                    failed_at=ctrl.id,
                    condensed_error=condensed,
                    control_results=control_results,
                )

        # Step 5: Aceptación
        return self._run_acceptance(req, control_results)

    def _check_routes(self, req: VerifyAttemptRequest) -> VerifyResult:
        allowed = list(req.paths_writable)
        if req.role == "driver":
            allowed.extend(req.paths_driver)

        changed_files = self.vcs.status()
        forbidden: list[str] = [f for f in changed_files if not _is_path_allowed(f, allowed)]

        if forbidden:
            self.vcs.revert_paths(forbidden)
            err = f"Archivos modificados fuera de las rutas permitidas: {', '.join(forbidden)}"
            condensed = condense_error("L0-rutas", err, max_chars=req.error_max_chars)
            return VerifyResult(
                ok=False,
                failed_at="L0-rutas",
                condensed_error=condensed,
                reverted_files=tuple(forbidden),
            )
        return VerifyResult(ok=True)

    def _check_seal(self, req: VerifyAttemptRequest) -> VerifyResult:
        seal_path = self.project_root / ".lictor" / "seal.json"
        if not seal_path.is_file():
            err = "No se encontró el archivo de sello .lictor/seal.json"
            condensed = condense_error("L0-sello", err, max_chars=req.error_max_chars)
            return VerifyResult(ok=False, failed_at="L0-sello", condensed_error=condensed)

        try:
            seal_data = json.loads(seal_path.read_text(encoding="utf-8"))
            sealed_files: dict[str, str] = seal_data.get("files", {})
        except (json.JSONDecodeError, OSError):
            err = "El archivo de sello .lictor/seal.json es inválido o ilegible"
            condensed = condense_error("L0-sello", err, max_chars=req.error_max_chars)
            return VerifyResult(ok=False, failed_at="L0-sello", condensed_error=condensed)

        for rel_path, expected_hash in sealed_files.items():
            target = self.project_root / rel_path
            if not target.is_file():
                err = f"Falta archivo sellado: {rel_path}"
                condensed = condense_error("L0-sello", err, max_chars=req.error_max_chars)
                return VerifyResult(ok=False, failed_at="L0-sello", condensed_error=condensed)

            actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            expected_sha = expected_hash.removeprefix("sha256:")
            if actual_sha != expected_sha:
                err = f"El sello no coincide para {rel_path}"
                condensed = condense_error("L0-sello", err, max_chars=req.error_max_chars)
                return VerifyResult(ok=False, failed_at="L0-sello", condensed_error=condensed)

        return VerifyResult(ok=True)

    def _check_suppressions(self, req: VerifyAttemptRequest) -> VerifyResult:
        if not req.suppressions:
            return VerifyResult(ok=True)

        added_lines = _collect_added_lines(self.vcs, self.project_root)
        detected: list[str] = []
        for line in added_lines:
            for pattern in req.suppressions:
                if pattern in line:
                    detected.append(pattern)

        if detected:
            err = f"Se detectaron supresiones no permitidas: {', '.join(sorted(set(detected)))}"
            condensed = condense_error("L0-supresiones", err, max_chars=req.error_max_chars)
            return VerifyResult(ok=False, failed_at="L0-supresiones", condensed_error=condensed)

        return VerifyResult(ok=True)

    def _run_single_control(
        self,
        ctrl: VerificationControlProtocol,
        req: VerifyAttemptRequest,
    ) -> tuple[bool, ControlResult, str | None]:
        runs_dir = self.project_root / ".lictor" / "runs" / req.run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        out_file = runs_dir / f"control_{ctrl.id}.xml"
        cmd_str = ctrl.run.replace("{out}", str(out_file))

        snapshot = IntegritySnapshot(self.project_root / ".lictor")
        cmd_out = self.runner.run(
            shlex.split(cmd_str),
            cwd=self.project_root,
            timeout_s=ctrl.timeout_s,
        )
        if not snapshot.verify_and_restore():
            return False, ControlResult(ok=False), "L0-estado"

        reader = self.readers.get(ctrl.report)
        if reader is None:
            err_msg = f"No reader available for format: {ctrl.report}"
            return False, ControlResult(ok=False, error=err_msg), ctrl.id

        if ctrl.report == "junit":
            output_content = str(out_file) if "{out}" in ctrl.run else cmd_out.stdout
            c_res = reader.read(
                output=output_content,
                exit_code=cmd_out.returncode,
                duration_ms=cmd_out.duration_ms,
            )
        else:
            combined = f"{cmd_out.stdout}\n{cmd_out.stderr}".strip()
            c_res = reader.read(
                output=combined,
                exit_code=cmd_out.returncode,
                duration_ms=cmd_out.duration_ms,
            )

        return c_res.ok, c_res, None

    def _run_acceptance(
        self,
        req: VerifyAttemptRequest,
        control_results: dict[str, ControlResult],
    ) -> VerifyResult:
        runs_dir = self.project_root / ".lictor" / "runs" / req.run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        out_file = runs_dir / "acceptance.xml"
        cmd_str = req.acceptance_run.replace("{out}", str(out_file))

        snapshot = IntegritySnapshot(self.project_root / ".lictor")
        cmd_out = self.runner.run(
            shlex.split(cmd_str),
            cwd=self.project_root,
            timeout_s=req.acceptance_timeout_s,
        )
        if not snapshot.verify_and_restore():
            err = "El estado en .lictor/ fue modificado indebidamente"
            condensed = condense_error("L0-estado", err, max_chars=req.error_max_chars)
            return VerifyResult(ok=False, failed_at="L0-estado", condensed_error=condensed)

        reader = self.readers.get(req.acceptance_report)
        if reader is None:
            err = f"No reader available for format: {req.acceptance_report}"
            condensed = condense_error("acceptance", err, max_chars=req.error_max_chars)
            return VerifyResult(
                ok=False,
                failed_at="acceptance",
                condensed_error=condensed,
                control_results=control_results,
            )

        output_content = str(out_file) if "{out}" in req.acceptance_run else cmd_out.stdout
        acc_res = reader.read(
            output=output_content,
            exit_code=cmd_out.returncode,
            duration_ms=cmd_out.duration_ms,
        )
        control_results["acceptance"] = acc_res

        # Map test cases to scenario IDs
        try:
            mapped = map_test_cases_to_scenarios(acc_res.test_cases, req.all_scenario_ids)
        except DriverError as exc:
            condensed = condense_error(
                "acceptance",
                str(exc),
                acc_res.test_cases,
                max_chars=req.error_max_chars,
            )
            return VerifyResult(
                ok=False,
                failed_at="acceptance",
                condensed_error=condensed,
                control_results=control_results,
            )

        scenario_results: dict[str, str] = {sid: str(tc.status) for sid, tc in mapped.items()}

        # Verify current scenario passed
        cur_id_str = str(
            req.scenario_id.value if isinstance(req.scenario_id, ScenarioId) else req.scenario_id
        ).strip()
        if scenario_results.get(cur_id_str) != "passed":
            condensed = condense_error(
                "acceptance",
                acc_res.error or f"Escenario {cur_id_str} no pasó",
                acc_res.test_cases,
                max_chars=req.error_max_chars,
            )
            return VerifyResult(
                ok=False,
                failed_at="acceptance",
                condensed_error=condensed,
                control_results=control_results,
                scenario_results=scenario_results,
            )

        # Verify regression on previously green scenarios
        for green_id in req.green_scenario_ids:
            if scenario_results.get(green_id) != "passed":
                err = f"Regresión detectada en escenario verde previo: {green_id}"
                condensed = condense_error(
                    "acceptance",
                    err,
                    acc_res.test_cases,
                    max_chars=req.error_max_chars,
                )
                return VerifyResult(
                    ok=False,
                    failed_at="acceptance",
                    condensed_error=condensed,
                    control_results=control_results,
                    scenario_results=scenario_results,
                )

        return VerifyResult(
            ok=True,
            control_results=control_results,
            scenario_results=scenario_results,
        )
