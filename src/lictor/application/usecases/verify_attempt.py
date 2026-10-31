"""VerifyAttempt use case with deterministic verification pipeline.

Includes ADR-0001 integrity snapshot for state protection.
"""

import contextlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from lictor.application.ports import CommandRunner, ResultReader, Vcs
from lictor.application.run_files import (
    attempt_prefix,
    describe_crash,
    fill_report_path,
    format_output,
    new_step_dir,
    relative_to_project,
    save_output,
    verification_tree,
    write_text,
)
from lictor.application.seal import collect_monitored_files
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.errors import DriverError, InternalLictorError, SpecError
from lictor.domain.model import (
    Candidate,
    CommandOutput,
    ControlResult,
    ScenarioId,
    TestCaseResult,
    VerifyResult,
)
from lictor.domain.rules import (
    acceptance_failure,
    banned_matches,
    condense_error,
    forbidden_paths,
    map_test_cases_to_scenarios,
    render_full_error,
    suite_crash_reason,
)

RoleKind = Literal["implement", "driver"]

_STATE_TAMPERED = "El estado en .lictor/ fue modificado indebidamente"


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
    attempt: int = 1
    acceptance_failure_exit_codes: Sequence[int] = (1,)
    forbidden_paths: Sequence[str] = ()
    banned_patterns: Sequence[str] = ()
    step_dir: Path | None = None  # filled in by VerifyAttempt.execute
    tree_dir: Path | None = None  # filled in by VerifyAttempt.execute


@dataclass(frozen=True)
class _Failure:
    """A failed gate: what the agent reads next is condensed, the rest goes to `runs/`."""

    failed_at: str
    reason: str
    label: str | None = None
    cases: Sequence[TestCaseResult] = ()
    all_cases: Sequence[TestCaseResult] | None = None
    output: str = ""
    control_results: dict[str, ControlResult] = field(default_factory=dict)
    scenario_results: dict[str, str] = field(default_factory=dict)
    reverted_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ControlRun:
    """A control after running, with the output its failure may need."""

    result: ControlResult
    tampered: bool = False
    output: CommandOutput | None = None


def control_summaries(result: VerifyResult) -> dict[str, dict[str, object]]:
    """Serialize result and output of each control for the journal."""
    return {
        control_id: {
            "ok": control.ok,
            "mode": control.mode,
            "exit_code": control.exit_code,
            "duration_ms": control.duration_ms,
            "output": control.output_path,
        }
        for control_id, control in result.control_results.items()
    }


class IntegritySnapshot:
    """ADR-0001: In-memory integrity snapshot of .lictor/ and configuration metadata files."""

    MONITORED_LICTOR_FILES = (
        "seal.json",
        "checkpoints.sqlite",
        "journal.jsonl",
        "verify_env.json",
    )

    def __init__(self, lictor_dir: Path, project_root: Path | None = None) -> None:
        self.lictor_dir = lictor_dir
        self.project_root = project_root if project_root is not None else lictor_dir.parent
        self.snapshots: dict[Path, bytes | None] = {}
        self.take()

    def _monitored_paths(self) -> list[Path]:
        paths = [self.lictor_dir / name for name in self.MONITORED_LICTOR_FILES]
        paths.append(self.project_root / "lictor.json")
        paths.append(self.lictor_dir / "lictor.json")
        return paths

    def take(self) -> None:
        for target in self._monitored_paths():
            if target.is_file():
                self.snapshots[target] = target.read_bytes()
            else:
                self.snapshots[target] = None

    def verify_and_restore(self) -> bool:
        """Check if monitored files changed. If so, restore them and return False."""
        changed = False
        for target, initial_bytes in self.snapshots.items():
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
    diff_files: set[str] = set()

    for line in diff_text.splitlines():
        if line.startswith("diff --git a/"):
            _, sep, b_path = line.partition(" b/")
            if sep:
                diff_files.add(b_path.strip().strip('"'))
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])

    for rel_path in vcs.status():
        norm_rel = rel_path.strip('"')
        if norm_rel not in diff_files:
            target = project_root / norm_rel
            if target.is_file():
                with contextlib.suppress(OSError):
                    added_lines.extend(
                        target.read_text(encoding="utf-8", errors="ignore").splitlines()
                    )
    return added_lines


def _is_path_allowed(rel_path: str, allowed_roots: Sequence[str]) -> bool:
    target = PurePosixPath(rel_path)
    for root_str in allowed_roots:
        root_norm = PurePosixPath(root_str.strip("/"))
        if target == root_norm or target.is_relative_to(root_norm):
            return True
    return False


def _scenario_str(scenario_id: str | ScenarioId) -> str:
    return str(scenario_id.value if isinstance(scenario_id, ScenarioId) else scenario_id).strip()


class VerifyAttempt:
    """Deterministic multi-gate verification pipeline for an agent attempt."""

    def __init__(
        self,
        vcs: Vcs,
        runner: CommandRunner,
        readers: Mapping[str, ResultReader],
        project_root: Path,
        environment: VerificationEnvironment | None = None,
    ) -> None:
        self.vcs = vcs
        self.runner = runner
        self.readers = readers
        self.project_root = project_root
        self.environment = environment

    def execute(self, req: VerifyAttemptRequest) -> VerifyResult:
        """Run verification steps L0 to Acceptance sequentially."""
        scenario = _scenario_str(req.scenario_id)
        prefix = attempt_prefix(self.project_root, req.run_id, scenario, req.attempt)
        step_dir = new_step_dir(prefix.with_name(f"{prefix.name}-verify"))
        req = replace(req, step_dir=step_dir)

        gates = self._run_gates(req)
        if not gates.ok:
            return gates

        # Entorno, candidato y clon limpio: nada de lo ignorado o sin versionar cuenta
        env_problem = self._environment_problem(req)
        if env_problem:
            return self._fail(req, _Failure("L0-entorno", env_problem))

        candidate = self._create_candidate(req, scenario)
        with verification_tree(self.vcs, candidate.sha) as tree:
            result = self._run_commands(replace(req, tree_dir=tree))
        if not result.ok:
            return result

        # The commands above run project code, which can reach the environment as well.
        env_problem = self._environment_problem(req)
        if env_problem:
            return self._fail(
                req,
                _Failure("L0-entorno", env_problem, control_results=result.control_results),
            )
        return replace(result, candidate=candidate)

    def _run_gates(self, req: VerifyAttemptRequest) -> VerifyResult:
        """Run L0 over the working tree, the only place where the agent's changes can be seen."""
        routes_res = self._check_routes(req)
        if not routes_res.ok:
            return routes_res

        if req.role == "implement":
            seal_res = self._check_seal(req)
            if not seal_res.ok:
                return seal_res

        return self._check_suppressions(req)

    def _environment_problem(self, req: VerifyAttemptRequest) -> str | None:
        return self.environment.ensure(req.run_id) if self.environment else None

    def _command_env(self) -> dict[str, str] | None:
        return self.environment.command_env() if self.environment else None

    def _create_candidate(self, req: VerifyAttemptRequest, scenario: str) -> Candidate:
        paths = list(req.paths_writable)
        if req.role == "driver":
            paths.extend(req.paths_driver)
        return self.vcs.create_candidate(
            message=f"feat({scenario}): implementar escenario",
            trailers={
                "Scenario": scenario,
                "Lictor-Run": req.run_id,
                "Lictor-Attempts": str(req.attempt),
            },
            paths=paths,
        )

    def _run_commands(self, req: VerifyAttemptRequest) -> VerifyResult:
        control_results: dict[str, ControlResult] = {}
        for ctrl in req.controls:
            ctrl_run = self._run_single_control(ctrl, req)
            control_results[ctrl.id] = ctrl_run.result
            if ctrl_run.tampered:
                return self._fail(
                    req,
                    _Failure("L0-estado", _STATE_TAMPERED, control_results=control_results),
                )

            ctrl_res = ctrl_run.result
            if not ctrl_res.ok and ctrl.mode == "block":
                failed_label = f"{ctrl.level} ({ctrl.id})" if ctrl.level else ctrl.id
                output = format_output(ctrl_run.output) if ctrl_run.output else ""
                reason = ctrl_res.error or ""
                # With test cases they explain themselves; without them, only the output does.
                if ctrl.report == "junit" and not ctrl_res.test_cases:
                    reason = f"{reason}\n{output}".strip()
                return self._fail(
                    req,
                    _Failure(
                        ctrl.id,
                        reason,
                        label=failed_label,
                        cases=ctrl_res.test_cases,
                        output=output,
                        control_results=control_results,
                    ),
                )

        return self._run_acceptance(req, control_results)

    def _fail(self, req: VerifyAttemptRequest, failure: _Failure) -> VerifyResult:
        """Condense the failure for the agent and keep it whole in the attempt's directory."""
        label = failure.label or failure.failed_at
        all_cases = failure.cases if failure.all_cases is None else failure.all_cases
        error_path = self._step_dir(req) / "error.txt"
        write_text(
            error_path,
            render_full_error(label, failure.reason, all_cases, failure.output),
        )
        return VerifyResult(
            ok=False,
            failed_at=failure.failed_at,
            condensed_error=condense_error(
                label, failure.reason, failure.cases, max_chars=req.error_max_chars
            ),
            control_results=failure.control_results,
            scenario_results=failure.scenario_results,
            reverted_files=failure.reverted_files,
            error_path=relative_to_project(error_path, self.project_root),
        )

    def _step_dir(self, req: VerifyAttemptRequest) -> Path:
        if req.step_dir is None:
            raise InternalLictorError("step_dir lo asigna execute")
        return req.step_dir

    def _tree_dir(self, req: VerifyAttemptRequest) -> Path:
        if req.tree_dir is None:
            raise InternalLictorError("tree_dir lo asigna execute")
        return req.tree_dir

    def _check_routes(self, req: VerifyAttemptRequest) -> VerifyResult:
        allowed = list(req.paths_writable)
        if req.role == "driver":
            allowed.extend(req.paths_driver)

        changed_files = self.vcs.status()
        rejected = set(forbidden_paths(changed_files, req.forbidden_paths))
        # A link in the commit can point to what the commit leaves out.
        rejected.update(f for f in changed_files if (self.project_root / f).is_symlink())
        forbidden: list[str] = [
            f for f in changed_files if f in rejected or not _is_path_allowed(f, allowed)
        ]

        if forbidden:
            self.vcs.revert_paths(forbidden)
            err = f"Archivos modificados fuera de las rutas permitidas: {', '.join(forbidden)}"
            return self._fail(req, _Failure("L0-rutas", err, reverted_files=tuple(forbidden)))
        return VerifyResult(ok=True)

    def _check_seal(self, req: VerifyAttemptRequest) -> VerifyResult:
        seal_path = self.project_root / ".lictor" / "seal.json"
        if not seal_path.is_file():
            err = "No se encontró el archivo de sello .lictor/seal.json"
            return self._fail(req, _Failure("L0-sello", err))

        try:
            seal_data = json.loads(seal_path.read_text(encoding="utf-8"))
            sealed_files: dict[str, str] = seal_data.get("files", {})
        except (json.JSONDecodeError, OSError):
            err = "El archivo de sello .lictor/seal.json es inválido o ilegible"
            return self._fail(req, _Failure("L0-sello", err))

        validation_err = self._validate_sealed_files(sealed_files, req)
        if validation_err:
            return self._fail(req, _Failure("L0-sello", validation_err))

        return VerifyResult(ok=True)

    def _validate_sealed_files(
        self,
        sealed_files: dict[str, str],
        req: VerifyAttemptRequest,
    ) -> str | None:
        if not sealed_files:
            return "El archivo de sello .lictor/seal.json no contiene archivos sellados"

        try:
            current_files = collect_monitored_files(self.project_root, req.paths_driver)
        except SpecError as exc:
            return str(exc)

        for rel_path in sorted(sealed_files):
            if rel_path not in current_files:
                return f"Falta archivo sellado: {rel_path}"
            if current_files[rel_path] != sealed_files[rel_path]:
                return f"El sello no coincide para {rel_path}"

        for rel_path in sorted(current_files):
            if rel_path not in sealed_files:
                prefix = (
                    "Archivo de características no sellado"
                    if rel_path.startswith("features/")
                    else "Archivo de driver no sellado"
                )
                return f"{prefix}: {rel_path}"

        return None

    def _check_suppressions(self, req: VerifyAttemptRequest) -> VerifyResult:
        if not req.suppressions and not req.banned_patterns:
            return VerifyResult(ok=True)

        added_lines = _collect_added_lines(self.vcs, self.project_root)
        detected = banned_matches(added_lines, req.banned_patterns)
        for line in added_lines:
            for pattern in req.suppressions:
                if pattern in line:
                    detected.append(pattern)

        if detected:
            err = f"Se detectaron supresiones no permitidas: {', '.join(sorted(set(detected)))}"
            return self._fail(req, _Failure("L0-supresiones", err))

        return VerifyResult(ok=True)

    def _run_single_control(
        self,
        ctrl: VerificationControlProtocol,
        req: VerifyAttemptRequest,
    ) -> _ControlRun:
        step_dir = self._step_dir(req)
        out_file = step_dir / f"control_{ctrl.id}.xml"
        log_file = step_dir / f"control_{ctrl.id}.log"
        cmd_tokens = fill_report_path(ctrl.run, out_file)

        snapshot = IntegritySnapshot(self.project_root / ".lictor", project_root=self.project_root)
        cmd_out = self.runner.run(
            cmd_tokens,
            cwd=self._tree_dir(req),
            timeout_s=ctrl.timeout_s,
            env=self._command_env(),
        )
        tampered = not snapshot.verify_and_restore()
        save_output(log_file, cmd_out)

        def recorded(result: ControlResult) -> ControlResult:
            return replace(
                result,
                exit_code=cmd_out.returncode,
                mode=ctrl.mode,
                output_path=relative_to_project(log_file, self.project_root),
            )

        if tampered:
            return _ControlRun(recorded(ControlResult(ok=False)), tampered=True)

        reader = self.readers.get(ctrl.report)
        if reader is None:
            err_msg = f"No reader available for format: {ctrl.report}"
            return _ControlRun(recorded(ControlResult(ok=False, error=err_msg)), output=cmd_out)

        if ctrl.report == "junit":
            output_content = str(out_file) if "{out}" in ctrl.run else cmd_out.stdout
        else:
            output_content = format_output(cmd_out)
        c_res = reader.read(
            output=output_content,
            exit_code=cmd_out.returncode,
            duration_ms=cmd_out.duration_ms,
            allow_empty=ctrl.allow_empty,
        )

        return _ControlRun(recorded(c_res), output=cmd_out)

    def _run_acceptance(
        self,
        req: VerifyAttemptRequest,
        control_results: dict[str, ControlResult],
    ) -> VerifyResult:
        step_dir = self._step_dir(req)
        out_file = step_dir / "acceptance.xml"
        log_file = step_dir / "acceptance.log"
        cmd_tokens = fill_report_path(req.acceptance_run, out_file)

        snapshot = IntegritySnapshot(self.project_root / ".lictor", project_root=self.project_root)
        cmd_out = self.runner.run(
            cmd_tokens,
            cwd=self._tree_dir(req),
            timeout_s=req.acceptance_timeout_s,
            env=self._command_env(),
        )
        tampered = not snapshot.verify_and_restore()
        save_output(log_file, cmd_out)
        if tampered:
            return self._fail(
                req, _Failure("L0-estado", _STATE_TAMPERED, control_results=control_results)
            )

        output = format_output(cmd_out)
        reader = self.readers.get(req.acceptance_report)
        if reader is None:
            err = f"No reader available for format: {req.acceptance_report}"
            return self._fail(
                req,
                _Failure("acceptance", err, output=output, control_results=control_results),
            )

        output_content = str(out_file) if "{out}" in req.acceptance_run else cmd_out.stdout
        acc_res = replace(
            reader.read(
                output=output_content,
                exit_code=cmd_out.returncode,
                duration_ms=cmd_out.duration_ms,
                allow_empty=False,
            ),
            exit_code=cmd_out.returncode,
            output_path=relative_to_project(log_file, self.project_root),
        )
        control_results["acceptance"] = acc_res

        crash = suite_crash_reason(
            cmd_out.returncode, req.acceptance_failure_exit_codes, acc_res.test_cases
        )
        if crash is not None:
            return self._fail(
                req,
                _Failure(
                    "acceptance",
                    describe_crash(crash, acc_res, output),
                    cases=acc_res.test_cases,
                    output=output,
                    control_results=control_results,
                ),
            )

        # Map test cases to scenario IDs
        try:
            mapped = map_test_cases_to_scenarios(acc_res.test_cases, req.all_scenario_ids)
        except DriverError as exc:
            return self._fail(
                req,
                _Failure(
                    "acceptance",
                    str(exc),
                    cases=acc_res.test_cases,
                    output=output,
                    control_results=control_results,
                ),
            )

        scenario_results: dict[str, str] = {sid: str(tc.status) for sid, tc in mapped.items()}

        failure = acceptance_failure(_scenario_str(req.scenario_id), req.green_scenario_ids, mapped)
        if failure is not None:
            reason, relevant_cases = failure
            return self._fail(
                req,
                _Failure(
                    "acceptance",
                    reason,
                    cases=relevant_cases,
                    all_cases=acc_res.test_cases,
                    output=output,
                    control_results=control_results,
                    scenario_results=scenario_results,
                ),
            )

        return VerifyResult(
            ok=True,
            control_results=control_results,
            scenario_results=scenario_results,
        )
