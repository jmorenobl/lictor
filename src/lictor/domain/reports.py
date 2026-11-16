"""What a report says, as facts, and whether its counts are possible (ADR-0009).

A reader turns a file into one of these and decides nothing: the verdict is a rule of the domain
(`verdicts.py`). A report whose counts can't be true is not a report, so building one checks them.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from lictor.domain.model import CoverageScore, MutationScore, TestCaseResult
from lictor.domain.rules import coverage_score, mutation_score

ReportFormat = Literal["exit-code", "junit", "coverage", "mutation"]


@dataclass(frozen=True)
class MissingReport:
    """Nothing was left where the report should be."""


@dataclass(frozen=True)
class InvalidReport:
    """Something was left where the report should be, and it can't be read as one."""

    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("Un informe no válido necesita un motivo")


@dataclass(frozen=True)
class JUnitReport:
    """The test cases a JUnit report lists; none is a run with no tests, not a missing report."""

    cases: tuple[TestCaseResult, ...] = ()


@dataclass(frozen=True)
class CoverageReport:
    """The totals coverage.py exported: lines and branches, run and in all."""

    covered_lines: int
    num_statements: int
    covered_branches: int = 0
    num_branches: int = 0

    @property
    def score(self) -> CoverageScore:
        return coverage_score(
            {
                "covered_lines": self.covered_lines,
                "num_statements": self.num_statements,
                "covered_branches": self.covered_branches,
                "num_branches": self.num_branches,
            }
        )


@dataclass(frozen=True)
class MutationReport:
    """The mutants of a pass by outcome, as the mutation tool exported them."""

    counts: Mapping[str, int]

    @property
    def score(self) -> MutationScore:
        return mutation_score(self.counts)


type ReportOf[R] = R | MissingReport | InvalidReport
type Report = JUnitReport | CoverageReport | MutationReport | MissingReport | InvalidReport

_COVERAGE_TOTALS = ("covered_lines", "num_statements", "covered_branches", "num_branches")
_COVERAGE_REQUIRED = ("covered_lines", "num_statements")
MUTATION_OUTCOMES = ("killed", "survived", "timeout", "no_tests", "suspicious", "segfault")
_MUTATION_COUNTS = (*MUTATION_OUTCOMES, "skipped", "total")


def _count(value: object) -> int | None:
    """A count is a non-negative integer; a bool is not one, though Python says it is an int."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def coverage_report(data: object) -> CoverageReport | InvalidReport:
    """The totals of a coverage export, or why they can't be the totals of a real run."""
    totals = data.get("totals") if isinstance(data, dict) else None
    if not isinstance(totals, dict):
        return InvalidReport("no tiene 'totals'")
    for name in _COVERAGE_REQUIRED:
        if name not in totals:
            return InvalidReport(f"no tiene '{name}' en 'totals'")
    counts: dict[str, int] = {}
    for name in _COVERAGE_TOTALS:
        value = _count(totals.get(name, 0))
        if value is None:
            return InvalidReport(f"'{name}' no es un número: {totals.get(name)!r}")
        counts[name] = value
    pairs = (("covered_lines", "num_statements"), ("covered_branches", "num_branches"))
    for covered, total in pairs:
        if counts[covered] > counts[total]:
            return InvalidReport(
                f"'{covered}' ({counts[covered]}) es mayor que '{total}' ({counts[total]})"
            )
    return CoverageReport(**counts)


def mutation_report(data: object) -> MutationReport | InvalidReport:
    """The counts of a mutation export, or why they can't be the counts of a real pass."""
    if not isinstance(data, dict):
        return InvalidReport("no es un objeto JSON")
    counts: dict[str, int] = {}
    for name in _MUTATION_COUNTS:
        value = _count(data.get(name, 0))
        if value is None:
            return InvalidReport(f"'{name}' no es un número de mutantes: {data.get(name)!r}")
        counts[name] = value
    total = counts["total"]
    for name in (*MUTATION_OUTCOMES, "skipped"):
        if counts[name] > total:
            return InvalidReport(f"'{name}' ({counts[name]}) es mayor que 'total' ({total})")
    summed = sum(counts[name] for name in (*MUTATION_OUTCOMES, "skipped"))
    if summed > total:
        return InvalidReport(f"los mutantes por resultado ({summed}) son más que 'total' ({total})")
    return MutationReport(counts)
