"""CheckProject use case for verifying project controls and acceptance."""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from lictor.application.ports import CommandRunner, ResultReader
from lictor.application.run_files import (
    CHECK_DIR,
    aside_dir,
    fill_report_path,
    reader_input,
    relative_to_project,
    save_output,
)
from lictor.domain.errors import ConfigError
from lictor.domain.model import ControlResult
from lictor.domain.rules import condense_error, control_path_status

ACCEPTANCE_ID = "acceptance"


@dataclass(frozen=True)
class CheckControlSpec:
    """Specification for a verification control."""

    id: str
    level: str
    run: str
    report: str = "exit-code"
    mode: str = "block"
    empty_exit_codes: Sequence[int] = ()
    timeout_s: int = 600
    when_exists: str | None = None


@dataclass(frozen=True)
class CheckAcceptanceSpec:
    """Specification for acceptance tests."""

    run: str
    report: str = "junit"
    timeout_s: int = 600


@dataclass(frozen=True)
class CheckProjectRequest:
    """Request payload for CheckProject use case.

    The commands run in `tree`, the project unless said otherwise, and leave their reports and
    logs in `evidence_dir`. `deleted` are the paths the checked tree removed from HEAD: a control
    whose `when_exists` path is among them fails instead of being skipped.
    """

    project_root: Path
    controls: Sequence[CheckControlSpec]
    acceptance: CheckAcceptanceSpec | None = None
    levels: Sequence[str] | None = None
    error_max_chars: int = 4000
    tree: Path | None = None
    evidence_dir: Path | None = None
    keep_going: bool = False
    control_ids: Sequence[str] | None = None
    deleted: Sequence[str] = ()


@dataclass(frozen=True)
class CheckProjectResult:
    """Result of CheckProject use case."""

    ok: bool
    failed_at: str | None = None
    control_results: dict[str, ControlResult] = field(default_factory=dict)
    condensed_error: str | None = None


@dataclass(frozen=True)
class _RunStepParams:
    tree: Path
    evidence_dir: Path
    project_root: Path
    command_template: str
    report_format: str
    timeout_s: int
    empty_exit_codes: Sequence[int]
    step_id: str
    mode: str = "block"


class CheckProject:
    """Use case to run controls and acceptance suite without AI agent or graph."""

    def __init__(
        self,
        runner: CommandRunner,
        readers: Mapping[str, ResultReader],
    ) -> None:
        self.runner = runner
        self.readers = readers

    def execute(self, req: CheckProjectRequest) -> CheckProjectResult:
        """Run the selected controls, then acceptance; stop at the first blocking failure."""
        controls, acceptance = self._selected(req)
        control_results: dict[str, ControlResult] = {}
        first_failure: tuple[str, ControlResult] | None = None

        for ctrl in controls:
            result = self._control(req, ctrl)
            control_results[ctrl.id] = result
            if not result.ok and ctrl.mode != "measure":
                first_failure = first_failure or (ctrl.id, result)
                if not req.keep_going:
                    break

        if acceptance is not None and (first_failure is None or req.keep_going):
            result = self._run_command(
                self._params(req, ACCEPTANCE_ID, acceptance.run, acceptance.report, acceptance)
            )
            control_results[ACCEPTANCE_ID] = result
            if not result.ok:
                first_failure = first_failure or (ACCEPTANCE_ID, result)

        if first_failure is None:
            return CheckProjectResult(ok=True, control_results=control_results)
        failed_at, failed = first_failure
        condensed = condense_error(
            failed_at=failed_at,
            raw_error=failed.error or f"Falló {failed_at}",
            test_cases=failed.test_cases,
            max_chars=req.error_max_chars,
        )
        return CheckProjectResult(
            ok=False,
            failed_at=failed_at,
            control_results=control_results,
            condensed_error=condensed,
        )

    def _selected(
        self, req: CheckProjectRequest
    ) -> tuple[list[CheckControlSpec], CheckAcceptanceSpec | None]:
        """Apply `levels` and `control_ids` together; an id that names nothing is refused."""
        levels = {lvl.strip().upper() for lvl in req.levels} if req.levels is not None else None
        ids = set(req.control_ids) if req.control_ids is not None else None
        if ids is not None:
            known = {c.id for c in req.controls} | {ACCEPTANCE_ID}
            unknown = sorted(ids - known)
            if unknown:
                raise ConfigError(
                    f"No hay ningún control {', '.join(unknown)}. "
                    f"Los controles son: {', '.join(sorted(known))}."
                )
        controls = [
            c
            for c in req.controls
            if (levels is None or c.level.strip().upper() in levels)
            and (ids is None or c.id in ids)
        ]
        wants_acceptance = (levels is None or {"L3", "ACCEPTANCE"} & levels) and (
            ids is None or ACCEPTANCE_ID in ids
        )
        return controls, req.acceptance if wants_acceptance else None

    def _control(self, req: CheckProjectRequest, ctrl: CheckControlSpec) -> ControlResult:
        """Skip a control whose path the tree lacks and HEAD never had; fail one it removed."""
        tree = req.tree or req.project_root
        if ctrl.when_exists:
            in_tree = os.path.lexists(tree / ctrl.when_exists)
            status = control_path_status(ctrl.when_exists, in_tree=in_tree, deleted=req.deleted)
            if status == "absent":
                reason = f"No existe {ctrl.when_exists} en lo verificado"
                return ControlResult(ok=True, mode=ctrl.mode, skipped=reason)
            if status == "removed":
                reason = (
                    f"Lo verificado elimina {ctrl.when_exists}, que existía en HEAD: "
                    f"el control {ctrl.id} ya no puede ejecutarse"
                )
                return ControlResult(ok=False, mode=ctrl.mode, error=reason)
        return self._run_command(self._params(req, ctrl.id, ctrl.run, ctrl.report, ctrl))

    def _params(
        self,
        req: CheckProjectRequest,
        step_id: str,
        run: str,
        report: str,
        spec: CheckControlSpec | CheckAcceptanceSpec,
    ) -> _RunStepParams:
        control = spec if isinstance(spec, CheckControlSpec) else None
        return _RunStepParams(
            tree=req.tree or req.project_root,
            evidence_dir=req.evidence_dir or aside_dir(req.project_root, CHECK_DIR),
            project_root=req.project_root,
            command_template=run,
            report_format=report,
            timeout_s=spec.timeout_s,
            empty_exit_codes=control.empty_exit_codes if control else (),
            step_id=step_id,
            mode=control.mode if control else "block",
        )

    def _run_command(self, params: _RunStepParams) -> ControlResult:
        params.evidence_dir.mkdir(parents=True, exist_ok=True)
        out_file = params.evidence_dir / f"{params.step_id}.xml"
        log_file = params.evidence_dir / f"{params.step_id}.log"

        cmd_args = fill_report_path(params.command_template, out_file)

        cmd_out = self.runner.run(cmd=cmd_args, cwd=params.tree, timeout_s=params.timeout_s)
        save_output(log_file, cmd_out)

        reader = self.readers.get(params.report_format)
        if reader is None:
            return ControlResult(
                ok=False, error=f"No hay lector para el formato: {params.report_format}"
            )

        content = reader_input(params.report_format, params.command_template, out_file, cmd_out)

        return replace(
            reader.read(
                output=content,
                exit_code=cmd_out.returncode,
                duration_ms=cmd_out.duration_ms,
                empty_exit_codes=params.empty_exit_codes,
            ),
            exit_code=cmd_out.returncode,
            mode=params.mode,
            output_path=relative_to_project(log_file, params.project_root),
        )
