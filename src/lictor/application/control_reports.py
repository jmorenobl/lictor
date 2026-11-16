"""Read what a control's command left and ask the domain for its verdict (ADR-0009).

The format picks the reader, and each branch pairs that reader with the verdict of its kind, so
no report can reach the verdict of another format. Reading is I/O; deciding is the domain's.
"""

from collections.abc import Collection
from pathlib import Path
from typing import assert_never

from lictor.application.ports import ReportReaders
from lictor.application.run_files import format_output
from lictor.domain.model import CommandOutput, ControlResult
from lictor.domain.reports import ReportFormat
from lictor.domain.verdicts import (
    coverage_verdict,
    exit_code_verdict,
    judged_result,
    junit_verdict,
    mutation_verdict,
)


def read_and_judge(
    readers: ReportReaders,
    report_format: ReportFormat,
    report_path: Path,
    output: CommandOutput,
    empty_exit_codes: Collection[int],
) -> ControlResult:
    """The result of a control that ran, before its minimum score is enforced."""
    code, duration = output.returncode, output.duration_ms
    match report_format:
        case "exit-code":
            verdict = exit_code_verdict(format_output(output), code, empty_exit_codes)
            return judged_result(verdict, None, duration)
        case "junit":
            junit = readers.junit.read(report_path)
            return judged_result(junit_verdict(junit, code, empty_exit_codes), junit, duration)
        case "coverage":
            coverage = readers.coverage.read(report_path)
            verdict = coverage_verdict(coverage, code, empty_exit_codes)
            return judged_result(verdict, coverage, duration)
        case "mutation":
            mutation = readers.mutation.read(report_path)
            return judged_result(mutation_verdict(mutation, code), mutation, duration)
        case _:
            assert_never(report_format)
