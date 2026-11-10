"""Unit tests for the coverage score, and for scores in general (0.3.0, F29)."""

from dataclasses import asdict

import pytest

from lictor.domain.model import ControlResult, CoverageScore, MutationScore
from lictor.domain.rules import (
    coverage_score,
    describe_score,
    enforce_min_score,
    mutation_score,
    score_from_record,
    score_meets,
)

TOTALS = {"covered_lines": 412, "num_statements": 450, "covered_branches": 90, "num_branches": 110}


def test_coverage_counts_lines_and_branches_together_as_coverage_py_does() -> None:
    score = coverage_score(TOTALS)

    assert score == CoverageScore(
        covered_lines=412,
        num_statements=450,
        covered_branches=90,
        num_branches=110,
        percent=100 * 502 / 560,
    )


def test_coverage_without_branches_is_the_share_of_lines() -> None:
    assert coverage_score({"covered_lines": 3, "num_statements": 4}).percent == 75.0


def test_coverage_of_nothing_to_measure_is_complete() -> None:
    """As coverage.py says it: no line is left uncovered."""
    assert coverage_score({}).percent == 100.0


def test_a_coverage_score_names_lines_and_branches() -> None:
    assert describe_score(coverage_score(TOTALS)) == ("89,6 % (412 de 450 líneas, 90 de 110 ramas)")


def test_a_coverage_score_without_branches_names_only_lines() -> None:
    score = coverage_score({"covered_lines": 3, "num_statements": 4})

    assert describe_score(score) == "75,0 % (3 de 4 líneas)"


@pytest.mark.parametrize(
    ("covered", "expected"), [(8999, "89,9 %"), (9000, "90,0 %"), (8995, "89,9 %")]
)
def test_a_score_is_never_shown_above_what_it_is(covered: int, expected: str) -> None:
    """Rounded up, 89,99 would read 90,0 and fail a minimum of 90."""
    score = coverage_score({"covered_lines": covered, "num_statements": 10000})

    assert describe_score(score).startswith(expected)


def test_a_record_names_its_kind() -> None:
    coverage = coverage_score(TOTALS)
    mutation = mutation_score({"killed": 3, "total": 4})

    assert score_from_record(_record(coverage)) == coverage
    assert score_from_record(_record(mutation)) == mutation


def test_a_record_without_a_kind_is_a_mutation_score_from_0_2_0() -> None:
    old = {"killed": 3, "survived": 1, "total": 4, "percent": 75.0}

    assert score_from_record(old) == MutationScore(killed=3, survived=1, total=4, percent=75.0)


@pytest.mark.parametrize(
    "raw", [None, {}, [], {"x": 1}, {"kind": "otra", "percent": 1.0}, {"kind": "coverage", "x": 1}]
)
def test_a_record_that_cant_be_read_is_no_score(raw: object) -> None:
    assert score_from_record(raw) is None


def test_a_coverage_below_the_minimum_fails_and_says_both() -> None:
    passed = ControlResult(ok=True, score=coverage_score(TOTALS))

    failed = enforce_min_score(passed, 90)

    assert failed.ok is False
    assert failed.error == (
        "La cobertura, 89,6 % (412 de 450 líneas, 90 de 110 ramas), no llega al mínimo de 90 %"
    )


def test_a_coverage_at_the_minimum_passes() -> None:
    score = coverage_score({"covered_lines": 9, "num_statements": 10})

    assert score_meets(score, 90)
    assert enforce_min_score(ControlResult(ok=True, score=score), 90).ok


def test_a_mutation_score_below_the_minimum_still_names_mutation() -> None:
    passed = ControlResult(ok=True, score=mutation_score({"killed": 3, "total": 4}))

    error = enforce_min_score(passed, 80).error

    assert error is not None
    assert error.startswith("La puntuación de mutación, 75,0 %")


def _record(score: CoverageScore | MutationScore) -> dict[str, object]:
    return asdict(score)


def test_a_minimum_over_a_run_that_measured_nothing_fails() -> None:
    """A run with no tests passes without a score; with a minimum, nothing measured is below it."""
    failed = enforce_min_score(ControlResult(ok=True), 80)

    assert failed.ok is False
    assert failed.error == "El control no midió nada y declara un mínimo de 80 %"


def test_without_a_minimum_a_run_that_measured_nothing_stays_as_it_is() -> None:
    assert enforce_min_score(ControlResult(ok=True), None).ok
