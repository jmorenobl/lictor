"""Read what a control's command left and ask the domain for its verdict (ADR-0009).

The format picks the reader, and each branch pairs that reader with the verdict of its kind, so
no report can reach the verdict of another format. Reading is I/O; deciding is the domain's.
"""

from pathlib import Path
from typing import assert_never

from lictor.application.ports import ReportReaders
from lictor.application.run_files import format_output
from lictor.domain.model import CommandOutput, ControlResult
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
