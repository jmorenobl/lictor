"""A report is only a report if its counts can be true (ADR-0009, change 7)."""

import pytest

from lictor.domain.model import CoverageScore
from lictor.domain.reports import (
    CoverageReport,
    InvalidReport,
    MissingReport,
    MutationReport,
    coverage_report,
    mutation_report,
)


def test_an_invalid_report_needs_a_reason() -> None:
    with pytest.raises(ValueError, match="motivo"):
        InvalidReport("  ")
    assert InvalidReport("x").reason == "x"


def test_a_missing_report_is_a_value() -> None:
    assert MissingReport() == MissingReport()


def test_coverage_takes_the_four_totals() -> None:
    totals = {"covered_lines": 3, "num_statements": 4, "covered_branches": 1, "num_branches": 2}
    report = coverage_report({"totals": totals, "files": {}})

    assert report == CoverageReport(3, 4, 1, 2)
    assert isinstance(report, CoverageReport)
    assert report.score == CoverageScore(3, 4, 1, 2, percent=100 * 4 / 6)


def test_coverage_without_branches_counts_them_as_zero() -> None:
    assert coverage_report({"totals": {"covered_lines": 1, "num_statements": 2}}) == (
        CoverageReport(1, 2, 0, 0)
    )


def test_coverage_may_cover_everything() -> None:
    totals = {"covered_lines": 2, "num_statements": 2, "covered_branches": 2, "num_branches": 2}
    assert coverage_report({"totals": totals}) == CoverageReport(2, 2, 2, 2)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ([], "no tiene 'totals'"),
        ({}, "no tiene 'totals'"),
        ({"totals": []}, "no tiene 'totals'"),
        ({"totals": {}}, "no tiene 'covered_lines' en 'totals'"),
        ({"totals": {"covered_lines": 0}}, "no tiene 'num_statements' en 'totals'"),
        (
            {"totals": {"covered_lines": "9", "num_statements": 10}},
            "'covered_lines' no es un número: '9'",
        ),
        (
            {"totals": {"covered_lines": -1, "num_statements": 10}},
            "'covered_lines' no es un número: -1",
        ),
        (
            {"totals": {"covered_lines": True, "num_statements": 1}},
            "'covered_lines' no es un número: True",
        ),
        (
            {"totals": {"covered_lines": 1.0, "num_statements": 2}},
            "'covered_lines' no es un número: 1.0",
        ),
        (
            {"totals": {"covered_lines": 1, "num_statements": 2, "num_branches": None}},
            "'num_branches' no es un número: None",
        ),
        (
            {"totals": {"covered_lines": 3, "num_statements": 2}},
            "'covered_lines' (3) es mayor que 'num_statements' (2)",
        ),
        (
            {
                "totals": {
                    "covered_lines": 0,
                    "num_statements": 2,
                    "covered_branches": 2,
                    "num_branches": 1,
                }
            },
            "'covered_branches' (2) es mayor que 'num_branches' (1)",
        ),
    ],
)
def test_coverage_that_cannot_be_true_is_invalid(data: object, reason: str) -> None:
    assert coverage_report(data) == InvalidReport(reason)


def test_mutation_takes_the_counts_of_every_outcome() -> None:
    counts = {"killed": 7, "survived": 1, "timeout": 1, "skipped": 1, "total": 10}
    report = mutation_report({**counts, "otra": "cosa"})

    assert isinstance(report, MutationReport)
    assert report.score.killed == 7
    assert report.score.total == 10
    assert report.score.percent == 100 * 7 / 9


def test_mutation_may_count_every_mutant() -> None:
    counts = {
        "killed": 4,
        "survived": 2,
        "timeout": 1,
        "no_tests": 1,
        "suspicious": 1,
        "segfault": 0,
        "skipped": 1,
        "total": 10,
    }
    assert isinstance(mutation_report(counts), MutationReport)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ([1], "no es un objeto JSON"),
        ({"killed": "3"}, "'killed' no es un número de mutantes: '3'"),
        ({"total": -1}, "'total' no es un número de mutantes: -1"),
        ({"survived": True}, "'survived' no es un número de mutantes: True"),
        ({"killed": 9, "total": 3}, "'killed' (9) es mayor que 'total' (3)"),
        ({"skipped": 4, "total": 1}, "'skipped' (4) es mayor que 'total' (1)"),
        ({"segfault": 2, "total": 1}, "'segfault' (2) es mayor que 'total' (1)"),
        (
            {"killed": 2, "survived": 2, "total": 3},
            "los mutantes por resultado (4) son más que 'total' (3)",
        ),
        (
            {"killed": 1, "skipped": 1, "total": 1},
            "los mutantes por resultado (2) son más que 'total' (1)",
        ),
    ],
)
def test_mutation_that_cannot_be_true_is_invalid(data: object, reason: str) -> None:
    assert mutation_report(data) == InvalidReport(reason)
