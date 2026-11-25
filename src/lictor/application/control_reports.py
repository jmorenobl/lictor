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
from lictor.domain.api import ApiBase, compare_api
from lictor.domain.controls import ReadStep, RunStep, Step, executed_as, lent_by_check
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
    api_verdict,
    complexity_verdict,
    coverage_verdict,
    duplication_verdict,
    enforce_min_score,
    exit_code_verdict,
    junit_verdict,
    mutation_verdict,
)


def read_and_judge(
    readers: ReportReaders,
    rule: VerdictRule,
    report_path: Path,
    output: CommandOutput,
    api_base: ApiBase | None = None,
) -> Judged:
    """What a control's command left, read with the reader of its format, and its verdict.

    An API report is compared with `api_base`, the run's base, when there is one (ADR-0017).
    """
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
        case "complexity" | "duplication" | "api":
            return _judge_the_code(readers, rule, report_path, run, api_base)
        case _:
            assert_never(rule.report)


def _judge_the_code(
    readers: ReportReaders,
    rule: VerdictRule,
    report_path: Path,
    run: Execution,
    api_base: ApiBase | None,
) -> Judged:
    """A control that measures the code itself: its complexity, its clones or its public API."""
    if rule.report == "duplication":
        duplication = readers.duplication.read(report_path)
        return Judged(duplication, duplication_verdict(duplication, run))
    if rule.report == "api":
        api = compare_api(readers.api.read(report_path), api_base)
        return Judged(api, api_verdict(api, run))
    if rule.limit is None:  # a CommandControl can't be built so
        raise InternalLictorError("Un control de complejidad sin límite")
    complexity = readers.complexity.read(report_path)
    return Judged(complexity, complexity_verdict(complexity, run, rule.limit))


@dataclass(frozen=True)
class Judging:
    """What judges what a control left: a reader per format, and the base an API report is
    compared with in this verification (ADR-0017), if there is one."""

    readers: ReportReaders
    api_base: ApiBase | None = None


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
    """A control after its turn, with what its command printed, which a failure may need."""

    entry: ControlEntry
    output: str = ""


@dataclass
class Readings:
    """What each reader of one verification will find at its turn; a retry starts a new one.

    It is kept from the moment its read control's turn ends: a skipped or unrunnable control
    leaves the same to its reader, one that ran leaves its verdict and the coverage it wrote,
    and one that tampered leaves nothing, because the verification ends there.
    """

    _found: dict[str, ReadSource] = field(default_factory=dict)

    def keep(self, step: RunStep, source: ReadSource) -> None:
        if step.read_by is not None:
            self._found[step.read_by.id] = source

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


def take_turn(step: Step, readings: Readings, run: Callable[[RunStep], ControlRun]) -> ControlRun:
    """A step's turn, as both the run and `lictor check` take it: `run` does a control's."""
    match step:
        case ReadStep():
            return readings.turn(step)
        case RunStep():
            return run(step)
        case _:
            assert_never(step)


def not_run(step: RunStep, outcome: Skipped | NotExecuted, readings: Readings) -> ControlRun:
    """A control whose turn came and that didn't run: its reader finds the same."""
    readings.keep(step, outcome)
    return ControlRun(CommandEntry(step.control, outcome))


def run_control(
    step: RunStep,
    judging: Judging,
    evidence_of: EvidenceOf,
    execute: Execute,
    readings: Readings,
) -> ControlRun:
    """Run a control's effective command, keep its log, then read and judge what it left.

    A command that changed the run's state is not judged: none of its reports is even read.
    Its reader's report is read right then, under the same conditions as its own.
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
    readers = judging.readers
    judged = read_and_judge(
        readers, control.verdict_rule, evidence.report, output, judging.api_base
    )
    verdict = enforce_min_score(judged.verdict, judged.score, control.min_score)
    judgement = Judged(judged.report, verdict)
    if step.read_by is not None:
        coverage = readers.coverage.read(evidence_of(step.read_by.id).report)
        readings.keep(step, ReadFact(judgement, coverage))
    entry = CommandEntry(control, Executed(ran, judgement))
    return ControlRun(entry, format_output(output))


def failure_reason(step: Step, entry: ControlEntry, reason: str) -> str:
    """A failure's reason, with the command it ran when that isn't its `run` (ADR-0008, 4)."""
    if not isinstance(step, RunStep):
        return reason
    note = executed_as(step.command, ran=isinstance(entry.outcome, Executed))
    return f"{note}\n{reason}" if note else reason


def lent_note(step: Step, entry: ControlEntry) -> str | None:
    """The mark of a control that ran with a `check` reader's arguments, if it ran at all."""
    if not isinstance(step, RunStep) or not isinstance(entry.outcome, Executed):
        return None
    return lent_by_check(step.command)
