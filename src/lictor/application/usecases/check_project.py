"""CheckProject use case for verifying project controls and acceptance."""

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import assert_never

from lictor.application.control_reports import ControlEvidence, ControlRun, run_control
from lictor.application.ports import CommandRunner, ReportReaders
from lictor.application.run_files import (
    CHECK_DIR,
    aside_dir,
    fill_report_path,
    relative_to_project,
    save_output,
)
from lictor.application.usecases.verify_attempt import describe_tampering
from lictor.domain.controls import CommandControl
from lictor.domain.errors import ConfigError
from lictor.domain.model import CommandOutput
from lictor.domain.results import (
    AcceptanceRun,
    Block,
    CommandEntry,
    Consequence,
    ControlEntry,
    NotExecuted,
    Proceed,
    Ran,
    Skipped,
    StateTampered,
    Unjudged,
    acceptance_cases,
    consequence,
)
from lictor.domain.rules import (
    condense_error,
    control_path_status,
    missing_plugin,
    plugin_skip_reason,
)
from lictor.domain.verdicts import (
    Execution,
    Failed,
    Verdict,
    acceptance_check_verdict,
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
    are what the environment loads: a control whose `when_plugin` isn't among them is skipped.
    """

    project_root: Path
    controls: Sequence[CommandControl]
    installed_plugins: Sequence[str] = field(kw_only=True)
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
    controls: tuple[ControlEntry, ...] = ()
    acceptance: AcceptanceRun | None = None  # None when it wasn't selected or wasn't reached
    condensed_error: str | None = None

    @property
    def acceptance_verdict(self) -> Verdict | None:
        """The verdict of the acceptance in `check`: a report it can trust, with no failed case."""
        if self.acceptance is None or isinstance(self.acceptance.judgement, Unjudged):
            return None
        return acceptance_check_verdict(self.acceptance.judgement)


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
        entries: list[ControlEntry] = []
        first_failure: tuple[str, Block] | None = None

        for ctrl in controls:
            ctrl_run = self._control(req, ctrl)
            entries.append(ctrl_run.entry)
            failure = _blocking(consequence(ctrl_run.entry, ctrl_run.output))
            if failure is not None:
                first_failure = first_failure or (ctrl.id, failure)
                if not req.keep_going:
                    break

        accepted: AcceptanceRun | None = None
        if acceptance is not None and (first_failure is None or req.keep_going):
            accepted = self._run_acceptance(req, acceptance)
            verdict = CheckProjectResult(ok=True, acceptance=accepted).acceptance_verdict
            if isinstance(verdict, Failed):
                failure = Block(verdict.reason, acceptance_cases(accepted))
                first_failure = first_failure or (ACCEPTANCE_ID, failure)

        if first_failure is None:
            return CheckProjectResult(ok=True, controls=tuple(entries), acceptance=accepted)
        failed_at, failed = first_failure
        condensed = condense_error(
            failed_at=failed_at,
            raw_error=failed.reason,
            test_cases=failed.cases,
            max_chars=req.error_max_chars,
        )
        return CheckProjectResult(
            ok=False,
            failed_at=failed_at,
            controls=tuple(entries),
            acceptance=accepted,
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

    def _control(self, req: CheckProjectRequest, ctrl: CommandControl) -> ControlRun:
        """Skip a control whose path the tree lacks and HEAD never had; fail one it removed."""
        tree = req.tree or req.project_root
        if plugin := missing_plugin(ctrl.when_plugin, req.installed_plugins):
            return ControlRun(CommandEntry(ctrl, Skipped(plugin_skip_reason(plugin))))
        if ctrl.when_exists:
            in_tree = os.path.lexists(tree / ctrl.when_exists)
            status = control_path_status(ctrl.when_exists, in_tree=in_tree, deleted=req.deleted)
            if status == "absent":
                reason = f"No existe {ctrl.when_exists} en lo verificado"
                return ControlRun(CommandEntry(ctrl, Skipped(reason)))
            if status == "removed":
                reason = (
                    f"Lo verificado elimina {ctrl.when_exists}, que existía en HEAD: "
                    f"el control {ctrl.id} ya no puede ejecutarse"
                )
                return ControlRun(CommandEntry(ctrl, NotExecuted(reason)))
        evidence = self._evidence(req, ctrl.id)

        def execute(
            command: Sequence[str], timeout_s: int
        ) -> tuple[CommandOutput, tuple[str, ...]]:
            # `check` keeps no snapshot: nothing it runs is trusted to leave the state alone.
            return self.runner.run(cmd=command, cwd=tree, timeout_s=timeout_s), ()

        return run_control(ctrl, self.readers, evidence, execute)

    def _evidence(self, req: CheckProjectRequest, step_id: str) -> ControlEvidence:
        evidence_dir = req.evidence_dir or aside_dir(req.project_root, CHECK_DIR)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        return ControlEvidence(
            evidence_dir / f"{step_id}.xml", evidence_dir / f"{step_id}.log", req.project_root
        )

    def _run_acceptance(
        self, req: CheckProjectRequest, acceptance: CheckAcceptanceSpec
    ) -> AcceptanceRun:
        """The acceptance is judged as in a run: a report it can trust, then its cases."""
        evidence = self._evidence(req, ACCEPTANCE_ID)
        command = fill_report_path(acceptance.run, evidence.report)
        tree = req.tree or req.project_root
        cmd_out = self.runner.run(cmd=command, cwd=tree, timeout_s=acceptance.timeout_s)
        save_output(evidence.log, command, cmd_out)
        junit = self.readers.junit.read(evidence.report)
        execution = Execution.of(cmd_out, acceptance.timeout_s)
        ran = Ran(tuple(command), execution, relative_to_project(evidence.log, req.project_root))
        return AcceptanceRun(ran, trust(junit, execution, acceptance.failure_exit_codes))


def _blocking(outcome: Consequence) -> Block | None:
    """What fails a `check`. It keeps no snapshot, so a tampering can only be a failure here."""
    match outcome:
        case Proceed():
            return None
        case Block():
            return outcome
        case StateTampered(changes=changes):
            return Block(describe_tampering(changes))
        case _:
            assert_never(outcome)
