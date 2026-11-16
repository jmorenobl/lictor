"""Run a control, read what its command left, and ask the domain for its verdict (ADR-0009).

The format picks the reader, and each branch pairs that reader with the verdict of its kind, so
no report can reach the verdict of another format. Running and reading are I/O; deciding is the
domain's. The run and `lictor check` both go through `run_control`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
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
from lictor.domain.model import CommandOutput, ControlResult
from lictor.domain.rules import enforce_min_score
from lictor.domain.verdicts import (
    Execution,
    VerdictRule,
    coverage_verdict,
    exit_code_verdict,
    judged_result,
    junit_verdict,
    mutation_verdict,
)


def read_and_judge(
    readers: ReportReaders, rule: VerdictRule, report_path: Path, output: CommandOutput
) -> ControlResult:
    """The result of a control that ran, before its minimum score is enforced."""
    run, duration = Execution.of(output, rule.timeout_s), output.duration_ms
    empty_exit_codes = rule.empty_exit_codes
    match rule.report:
        case "exit-code":
            verdict = exit_code_verdict(format_output(output), run, empty_exit_codes)
            return judged_result(verdict, None, duration)
        case "junit":
            junit = readers.junit.read(report_path)
            return judged_result(junit_verdict(junit, run, empty_exit_codes), junit, duration)
        case "coverage":
            coverage = readers.coverage.read(report_path)
            verdict = coverage_verdict(coverage, run, empty_exit_codes)
            return judged_result(verdict, coverage, duration)
        case "mutation":
            mutation = readers.mutation.read(report_path)
            return judged_result(mutation_verdict(mutation, run), mutation, duration)
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
    """A control after its turn: its result, what its command printed, and what it tampered."""

    result: ControlResult
    output: CommandOutput | None = None
    tampered: tuple[str, ...] = ()


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

    def recorded(result: ControlResult) -> ControlResult:
        return replace(
            result,
            exit_code=output.returncode,
            mode=control.mode,
            output_path=relative_to_project(evidence.log, evidence.project_root),
        )

    if tampered:
        return ControlRun(recorded(ControlResult(ok=False)), output, tampered)
    judged = read_and_judge(readers, control.verdict_rule, evidence.report, output)
    return ControlRun(recorded(enforce_min_score(judged, control.min_score)), output)
