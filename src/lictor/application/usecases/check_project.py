"""CheckProject use case for verifying project controls and acceptance."""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import assert_never

from lictor.application.control_reports import (
    ControlEvidence,
    ControlRun,
    Judging,
    Readings,
    failure_reason,
    lent_note,
    not_run,
    run_control,
    take_turn,
)
from lictor.application.ports import CommandRunner, ReportReaders
from lictor.application.run_files import (
    CHECK_DIR,
    aside_dir,
    fill_report_paths,
    relative_to_project,
    save_output,
)
from lictor.application.usecases.verify_attempt import describe_tampering
from lictor.domain.api import ApiBase
from lictor.domain.controls import (
    ACCEPTANCE_ID,
    Added,
    Control,
    RunStep,
    check_selection,
    verification_steps,
)
from lictor.domain.model import CommandOutput
from lictor.domain.results import (
    AcceptanceRun,
    Block,
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
    plugin_skip_reason,
)
from lictor.domain.verdicts import (
    Execution,
    Failed,
    Verdict,
    acceptance_check_verdict,
    trust,
)

# What each blocked control keeps of its failure: its reason and its first cases.
BLOCKED_CHARS = 800


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
    controls: Sequence[Control]  # all of them, of both stages, in their order
    installed_plugins: Sequence[str] = field(kw_only=True)
    acceptance: CheckAcceptanceSpec | None = None
    levels: Sequence[str] | None = None
    error_max_chars: int = 4000
    tree: Path | None = None
    evidence_dir: Path | None = None
    keep_going: bool = False
    control_ids: Sequence[str] | None = None
    deleted: Sequence[str] = ()
    # The base the public API is compared with (`--base`), measured already (ADR-0017).
    api_base: ApiBase | None = None


@dataclass(frozen=True)
class CheckProjectResult:
    """Result of CheckProject use case."""

    ok: bool
    failed_at: str | None = None
    controls: tuple[ControlEntry, ...] = ()
    acceptance: AcceptanceRun | None = None  # None when it wasn't selected or wasn't reached
    condensed_error: str | None = None
    # What ran although it wasn't selected, because a selected reader reads it (ADR-0008).
    added: tuple[Added, ...] = ()
    # The note of each control that ran with the arguments of a reader that only runs in check.
    lent: Mapping[str, str] = field(default_factory=dict)
    # Each control that failed blocking, with why, in the order they ran.
    blocked: tuple[tuple[str, str], ...] = ()

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
        """Run the selected controls, then acceptance; stop at the first blocking failure.

        `check` runs the controls of both stages, so every reader runs where it is verified: the
        command of its read control is the same whatever was selected (ADR-0008, point 4).
        """
        installed = req.installed_plugins
        selection = check_selection(req.controls, req.levels, req.control_ids, installed)
        steps = verification_steps(selection.controls, req.controls, installed)
        entries: list[ControlEntry] = []
        readings = Readings()
        lent: dict[str, str] = {}
        first_failure: tuple[str, Block] | None = None
        blocked: list[tuple[str, str]] = []

        for step in steps:
            ctrl_run = take_turn(step, readings, lambda run: self._control(req, run, readings))
            entries.append(ctrl_run.entry)
            if note := lent_note(step, ctrl_run.entry):
                lent[step.control.id] = note
            failure = _blocking(consequence(ctrl_run.entry, ctrl_run.output))
            if failure is not None:
                reason = failure_reason(step, ctrl_run.entry, failure.reason)
                failure = replace(failure, reason=reason)
                blocked.append(
                    (
                        step.control.id,
                        condense_error(
                            failed_at=step.control.id,
                            raw_error=reason,
                            test_cases=failure.cases,
                            max_chars=BLOCKED_CHARS,
                        ),
                    )
                )
                first_failure = first_failure or (step.control.id, failure)
                if not req.keep_going:
                    break

        accepted: AcceptanceRun | None = None
        if (
            req.acceptance is not None
            and selection.acceptance
            and (first_failure is None or req.keep_going)
        ):
            accepted = self._run_acceptance(req, req.acceptance)
            verdict = CheckProjectResult(ok=True, acceptance=accepted).acceptance_verdict
            if isinstance(verdict, Failed):
                failure = Block(verdict.reason, acceptance_cases(accepted))
                first_failure = first_failure or (ACCEPTANCE_ID, failure)

        done = CheckProjectResult(
            ok=True,
            controls=tuple(entries),
            acceptance=accepted,
            added=selection.added,
            lent=lent,
            blocked=tuple(blocked),
        )
        if first_failure is None:
            return done
        failed_at, failed = first_failure
        condensed = condense_error(
            failed_at=failed_at,
            raw_error=failed.reason,
            test_cases=failed.cases,
            max_chars=req.error_max_chars,
        )
        return replace(done, ok=False, failed_at=failed_at, condensed_error=condensed)

    def _control(self, req: CheckProjectRequest, step: RunStep, readings: Readings) -> ControlRun:
        """Skip a control whose path the tree lacks and HEAD never had; fail one it removed."""
        ctrl = step.control
        tree = req.tree or req.project_root
        if step.missing is not None:
            return not_run(step, Skipped(plugin_skip_reason(step.missing)), readings)
        if ctrl.when_exists:
            in_tree = os.path.lexists(tree / ctrl.when_exists)
            status = control_path_status(ctrl.when_exists, in_tree=in_tree, deleted=req.deleted)
            if status == "absent":
                reason = f"No existe {ctrl.when_exists} en lo verificado"
                return not_run(step, Skipped(reason), readings)
            if status == "removed":
                reason = (
                    f"Lo verificado elimina {ctrl.when_exists}, que existía en HEAD: "
                    f"el control {ctrl.id} ya no puede ejecutarse"
                )
                return not_run(step, NotExecuted(reason), readings)

        def execute(
            command: Sequence[str], timeout_s: int
        ) -> tuple[CommandOutput, tuple[str, ...]]:
            # `check` keeps no snapshot: nothing it runs is trusted to leave the state alone.
            return self.runner.run(cmd=command, cwd=tree, timeout_s=timeout_s), ()

        evidence = partial(self._evidence, req)
        judging = Judging(self.readers, req.api_base)
        return run_control(step, judging, evidence, execute, readings)

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
        command = fill_report_paths([(acceptance.run, evidence.report)])
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
