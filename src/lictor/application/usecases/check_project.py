"""CheckProject use case for verifying project controls and acceptance."""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from lictor.application.control_reports import ControlEvidence, run_control
from lictor.application.ports import CommandRunner, ReportReaders
from lictor.application.run_files import (
    CHECK_DIR,
    aside_dir,
    fill_report_path,
    relative_to_project,
    save_output,
)
from lictor.domain.controls import CommandControl
from lictor.domain.errors import ConfigError
from lictor.domain.model import CommandOutput, ControlResult
from lictor.domain.rules import (
    condense_error,
    control_path_status,
    missing_plugin,
    plugin_skip_reason,
)
from lictor.domain.verdicts import (
    Execution,
    acceptance_check_verdict,
    judged_result,
    trust,
)

ACCEPTANCE_ID = "acceptance"


@dataclass(frozen=True)
class CheckAcceptanceSpec:
    """Specification for acceptance tests."""

    run: str
    timeout_s: int = 600
    failure_exit_codes: Sequence[int] = (1,)


@dataclass(frozen=True)
class CheckProjectRequest:
    """Request payload for CheckProject use case.

    The commands run in `tree`, the project unless said otherwise, and leave their reports and
    logs in `evidence_dir`. `deleted` are the paths the checked tree removed from HEAD: a control
    whose `when_exists` path is among them fails instead of being skipped. `installed_plugins`
    are what the environment has, or None if nobody can say: a control whose `when_plugin`
    isn't among them is skipped.
    """

    project_root: Path
    controls: Sequence[CommandControl]
    acceptance: CheckAcceptanceSpec | None = None
    levels: Sequence[str] | None = None
    error_max_chars: int = 4000
    tree: Path | None = None
    evidence_dir: Path | None = None
    keep_going: bool = False
    control_ids: Sequence[str] | None = None
    deleted: Sequence[str] = ()
    installed_plugins: Sequence[str] | None = None


@dataclass(frozen=True)
class CheckProjectResult:
    """Result of CheckProject use case."""

    ok: bool
    failed_at: str | None = None
    control_results: dict[str, ControlResult] = field(default_factory=dict)
    condensed_error: str | None = None


class CheckProject:
    """Use case to run controls and acceptance suite without AI agent or graph."""

    def __init__(
        self,
        runner: CommandRunner,
        readers: ReportReaders,
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
            result = self._run_acceptance(req, acceptance)
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
    ) -> tuple[list[CommandControl], CheckAcceptanceSpec | None]:
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

    def _control(self, req: CheckProjectRequest, ctrl: CommandControl) -> ControlResult:
        """Skip a control whose path the tree lacks and HEAD never had; fail one it removed."""
        tree = req.tree or req.project_root
        if plugin := missing_plugin(ctrl.when_plugin, req.installed_plugins):
            return ControlResult(ok=True, mode=ctrl.mode, skipped=plugin_skip_reason(plugin))
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
        evidence = self._evidence(req, ctrl.id)

        def execute(
            command: Sequence[str], timeout_s: int
        ) -> tuple[CommandOutput, tuple[str, ...]]:
            # `check` keeps no snapshot: nothing it runs is trusted to leave the state alone.
            return self.runner.run(cmd=command, cwd=tree, timeout_s=timeout_s), ()

        return run_control(ctrl, self.readers, evidence, execute).result

    def _evidence(self, req: CheckProjectRequest, step_id: str) -> ControlEvidence:
        evidence_dir = req.evidence_dir or aside_dir(req.project_root, CHECK_DIR)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        return ControlEvidence(
            evidence_dir / f"{step_id}.xml", evidence_dir / f"{step_id}.log", req.project_root
        )

    def _run_acceptance(
        self, req: CheckProjectRequest, acceptance: CheckAcceptanceSpec
    ) -> ControlResult:
        """The acceptance is judged as in a run: a report it can trust, then its cases."""
        evidence = self._evidence(req, ACCEPTANCE_ID)
        command = fill_report_path(acceptance.run, evidence.report)
        tree = req.tree or req.project_root
        cmd_out = self.runner.run(cmd=command, cwd=tree, timeout_s=acceptance.timeout_s)
        save_output(evidence.log, command, cmd_out)
        junit = self.readers.junit.read(evidence.report)
        execution = Execution.of(cmd_out, acceptance.timeout_s)
        trusted = trust(junit, execution, acceptance.failure_exit_codes)
        result = judged_result(acceptance_check_verdict(trusted), junit, cmd_out.duration_ms)
        return replace(
            result,
            exit_code=cmd_out.returncode,
            output_path=relative_to_project(evidence.log, req.project_root),
        )
