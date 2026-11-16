"""Run a control, read what its command left, and ask the domain for its verdict (ADR-0009).

The format picks the reader, and each branch pairs that reader with the verdict of its kind, so
no report can reach the verdict of another format. Running and reading are I/O; deciding is the
domain's. The run and `lictor check` both go through `run_control` and `Readings`.

A control may be read by another (ADR-0008): its command then carries the reader's arguments, and
the reader's report is read as soon as that command ends, under the same conditions as its own.
The reader only gives its verdict at its turn, over what was read then.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import assert_never

from lictor.application.ports import ReportReaders
from lictor.application.run_files import (
    fill_report_paths,
    format_output,
    relative_to_project,
    save_output,
)
from lictor.domain.controls import ReadStep, RunStep
from lictor.domain.errors import InternalLictorError
from lictor.domain.model import CommandOutput
from lictor.domain.results import (
    CommandEntry,
    CommandOutcome,
    ControlEntry,
    Executed,
    Judged,
    NotExecuted,
    Ran,
    ReaderEntry,
    ReadFact,
    ReadSource,
    Skipped,
    Unjudged,
    reader_outcome,
)
from lictor.domain.rules import plugin_skip_reason
from lictor.domain.verdicts import (
    Execution,
    VerdictRule,
    coverage_verdict,
    enforce_min_score,
    exit_code_verdict,
    junit_verdict,
    mutation_verdict,
)


def read_and_judge(
    readers: ReportReaders, rule: VerdictRule, report_path: Path, output: CommandOutput
) -> Judged:
    """What a control's command left, read with the reader of its format, and its verdict."""
    run = Execution.of(output, rule.timeout_s)
    empty_exit_codes = rule.empty_exit_codes
    match rule.report:
        case "exit-code":
            return Judged(None, exit_code_verdict(format_output(output), run, empty_exit_codes))
        case "junit":
            junit = readers.junit.read(report_path)
            return Judged(junit, junit_verdict(junit, run, empty_exit_codes))
        case "coverage":
            coverage = readers.coverage.read(report_path)
            return Judged(coverage, coverage_verdict(coverage, run, empty_exit_codes))
        case "mutation":
            mutation = readers.mutation.read(report_path)
            return Judged(mutation, mutation_verdict(mutation, run))
        case _:
            assert_never(rule.report)


@dataclass(frozen=True)
class ControlEvidence:
    """Where a control leaves its report and its log, and the project they are relative to."""

    report: Path
    log: Path
    project_root: Path


# Runs a command with its timeout; says what it changed of the run's state, if anybody watched.
type Execute = Callable[[Sequence[str], int], tuple[CommandOutput, tuple[str, ...]]]
# Where the control with that id leaves its report and its log.
type EvidenceOf = Callable[[str], ControlEvidence]


@dataclass(frozen=True)
class ControlRun:
    """A control after its turn, with what its command printed, which a failure may need.

    `read` is what its reader will find at its turn, when it has one in this verification: kept
    from the moment the command ended. A run that tampered leaves nothing to find.
    """

    entry: ControlEntry
    output: str = ""
    read: ReadSource | None = None


def not_run(step: RunStep, outcome: Skipped | NotExecuted) -> ControlRun:
    """A control whose turn came and that didn't run: its reader finds the same."""
    read = outcome if step.read_by is not None else None
    return ControlRun(CommandEntry(step.control, outcome), read=read)


def run_control(
    step: RunStep,
    readers: ReportReaders,
    evidence_of: EvidenceOf,
    execute: Execute,
) -> ControlRun:
    """Run a control's effective command, keep its log, then read and judge what it left.

    A command that changed the run's state is not judged: none of its reports is even read.
    """
    control = step.control
    evidence = evidence_of(control.id)
    parts = [(part.text, evidence_of(part.owner).report) for part in step.command.parts]
    command = fill_report_paths(parts)
    output, tampered = execute(command, control.timeout_s)
    save_output(evidence.log, command, output)
    ran = Ran(
        tuple(command),
        Execution.of(output, control.timeout_s),
        relative_to_project(evidence.log, evidence.project_root),
    )
    if tampered:
        unjudged: CommandOutcome = Executed(ran, Unjudged(tampered))
        return ControlRun(CommandEntry(control, unjudged), format_output(output))
    judged = read_and_judge(readers, control.verdict_rule, evidence.report, output)
    verdict = enforce_min_score(judged.verdict, judged.score, control.min_score)
    judgement = Judged(judged.report, verdict)
    read: ReadSource | None = None
    if step.read_by is not None:
        coverage = readers.coverage.read(evidence_of(step.read_by.id).report)
        read = ReadFact(judgement, coverage)
    entry = CommandEntry(control, Executed(ran, judgement))
    return ControlRun(entry, format_output(output), read)


@dataclass
class Readings:
    """What each reader of one verification will find at its turn; a retry starts a new one."""

    _found: dict[str, ReadSource] = field(default_factory=dict)

    def keep(self, step: RunStep, ran: ControlRun) -> None:
        """Keep what a control's reader will find, if that reader is in this verification."""
        if step.read_by is not None and ran.read is not None:
            self._found[step.read_by.id] = ran.read

    def turn(self, step: ReadStep) -> ControlRun:
        """A reader's turn: skipped without its plugin, or its verdict over what was kept.

        Its read control always comes before it and has a turn; one that tampered ended the
        verification before this one.
        """
        reader = step.control
        if step.missing is not None:
            return ControlRun(ReaderEntry(reader, Skipped(plugin_skip_reason(step.missing))))
        source = self._found.get(reader.id)
        if source is None:
            msg = f"El lector '{reader.id}' llegó a su turno antes que '{reader.reads}'"
            raise InternalLictorError(msg)
        return ControlRun(ReaderEntry(reader, reader_outcome(reader, source)))
