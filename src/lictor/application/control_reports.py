"""Run a control, read what its command left, and ask the domain for its verdict (ADR-0009).

The format picks the reader, and each branch pairs that reader with the verdict of its kind, so
no report can reach the verdict of another format. Running and reading are I/O; deciding is the
domain's. The run and `lictor check` both go through `run_control`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from lictor.application.ports import ReportReaders
from lictor.application.run_files import (
    fill_report_path,
    format_output,
    relative_to_project,
    save_output,
)
from lictor.domain.controls import CommandControl
from lictor.domain.model import CommandOutput
from lictor.domain.results import CommandEntry, ControlEntry, Executed, Judged, Ran, Unjudged
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


@dataclass(frozen=True)
class ControlRun:
    """A control after its turn, with what its command printed, which a failure may need."""

    entry: ControlEntry
    output: str = ""


def run_control(
    control: CommandControl,
    readers: ReportReaders,
    evidence: ControlEvidence,
    execute: Execute,
) -> ControlRun:
    """Run a control's command, keep its log, then read and judge what it left.

    A command that changed the run's state is not judged: its report isn't even read.
    """
    command = fill_report_path(control.run, evidence.report)
    output, tampered = execute(command, control.timeout_s)
    save_output(evidence.log, command, output)
    ran = Ran(
        tuple(command),
        Execution.of(output, control.timeout_s),
        relative_to_project(evidence.log, evidence.project_root),
    )
    judgement: Judged | Unjudged
    if tampered:
        judgement = Unjudged(tampered)
    else:
        judged = read_and_judge(readers, control.verdict_rule, evidence.report, output)
        verdict = enforce_min_score(judged.verdict, judged.score, control.min_score)
        judgement = Judged(judged.report, verdict)
    return ControlRun(CommandEntry(control, Executed(ran, judgement)), format_output(output))
