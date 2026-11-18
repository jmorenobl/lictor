"""The refactor after each implemented green (ADR-0010): how it ends, and what it may not lower."""

import pytest

from lictor.domain.controls import CommandControl, ReaderControl
from lictor.domain.model import AgentSignal, AgentStatus, TestCaseResult
from lictor.domain.refactor import (
    Measure,
    RefactorRecord,
    measures_from_record,
    measures_record,
    oracle_measures,
    refactor_regressions,
    refactor_subject,
    regression_reason,
    settled_without_verifying,
)
from lictor.domain.reports import CoverageReport, JUnitReport
from lictor.domain.results import (
    CommandEntry,
    Executed,
    Judged,
    NotExecuted,
    Ran,
    Read,
    ReaderEntry,
    Skipped,
    Unjudged,
)
from lictor.domain.verdicts import Execution, Failed, Passed

_RAN = Ran(("cmd",), Execution(0, 60), "log")
_UNIT = CommandControl(id="unit", level="L2", run="u {out}", report="junit", mode="block")
_LINT = CommandControl(id="lint", level="L1", run="l", report="exit-code", mode="block")
_COV = ReaderControl(id="coverage", level="L2", reads="unit", adds="--cov", mode="measure")
_PASS = TestCaseResult(name="a")
_FAIL = TestCaseResult(name="b", status="failure")


def _unit(*cases: TestCaseResult) -> CommandEntry:
    return CommandEntry(_UNIT, Executed(_RAN, Judged(JUnitReport(cases), Passed())))


def test_the_subject_is_lictors_and_names_the_scenario() -> None:
    assert refactor_subject("A-001") == (
        "refactor(A-001): mejorar el código sin cambiar el comportamiento"
    )


def test_a_junit_control_measures_the_cases_that_passed() -> None:
    assert oracle_measures([_unit(_PASS, _PASS, _FAIL)]) == {"unit": Measure(passed=2)}


def test_a_reader_measures_its_score_and_an_exit_code_control_nothing() -> None:
    read = ReaderEntry(_COV, Read("unit", CoverageReport(3, 4), Passed()))
    lint = CommandEntry(_LINT, Executed(_RAN, Judged(None, Passed())))

    assert oracle_measures([read, lint]) == {"coverage": Measure(score=75.0)}


@pytest.mark.parametrize(
    "outcome",
    [Skipped("sin plugin"), NotExecuted("borrado"), Executed(_RAN, Unjudged(("seal.json",)))],
)
def test_a_control_that_wasnt_judged_measures_nothing(
    outcome: Skipped | NotExecuted | Executed,
) -> None:
    assert oracle_measures([CommandEntry(_UNIT, outcome)]) == {}


def test_a_reader_without_score_measures_nothing() -> None:
    read = ReaderEntry(_COV, Read("unit", CoverageReport(0, 0), Passed()))
    assert oracle_measures([read]) == {}


def test_a_failed_junit_control_still_counts_what_passed() -> None:
    entry = CommandEntry(_UNIT, Executed(_RAN, Judged(JUnitReport((_PASS,)), Failed("falló"))))
    assert oracle_measures([entry]) == {"unit": Measure(passed=1)}


def test_measures_go_through_the_checkpoint_as_they_were() -> None:
    measures = {"unit": Measure(passed=4), "coverage": Measure(score=81.5)}
    assert measures_from_record(measures_record(measures)) == measures


@pytest.mark.parametrize("bad", ["4", True, None, 1.5])
def test_a_count_of_another_type_reads_as_absent(bad: object) -> None:
    assert measures_from_record({"unit": {"passed": bad, "score": True}}) == {"unit": Measure()}


def test_a_whole_number_score_reads_as_a_score() -> None:
    assert measures_from_record({"c": {"passed": None, "score": 80}}) == {"c": Measure(score=80.0)}


def test_fewer_passing_cases_than_the_green_is_a_regression() -> None:
    assert refactor_regressions({"unit": Measure(passed=3)}, {"unit": Measure(passed=2)}) == [
        "unit: pasan 2 casos, y en el verde pasaban 3"
    ]


def test_as_many_or_more_cases_is_no_regression() -> None:
    baseline = {"unit": Measure(passed=3)}
    assert refactor_regressions(baseline, {"unit": Measure(passed=3)}) == []
    assert refactor_regressions(baseline, {"unit": Measure(passed=5)}) == []


def test_a_lower_score_or_none_at_all_is_a_regression() -> None:
    baseline = {"coverage": Measure(score=80.0)}
    # Cut, never rounded: 79,95 must not read as the 80 it doesn't reach.
    assert refactor_regressions(baseline, {"coverage": Measure(score=79.95)}) == [
        "coverage: 79,9 %, y en el verde 80,0 %"
    ]
    assert refactor_regressions(baseline, {}) == ["coverage: sin puntuación, y en el verde 80,0 %"]
    assert refactor_regressions(baseline, {"coverage": Measure(score=80.0)}) == []
    assert refactor_regressions(baseline, {"coverage": Measure(score=91.0)}) == []


def test_a_control_that_measured_nothing_in_the_green_demands_nothing() -> None:
    assert refactor_regressions({}, {"unit": Measure(passed=0)}) == []
    assert refactor_regressions({"unit": Measure()}, {}) == []


def test_a_control_gone_counts_as_zero_cases() -> None:
    assert refactor_regressions({"unit": Measure(passed=2)}, {}) == [
        "unit: pasan 0 casos, y en el verde pasaban 2"
    ]


def test_the_reason_lists_each_regression() -> None:
    assert regression_reason(["a", "b"]) == (
        "El refactor baja lo que mide la verificación del verde:\n- a\n- b"
    )


@pytest.mark.parametrize(
    ("signal", "status", "changed", "outcome"),
    [
        ("rejected", "ok", True, "discarded"),
        ("rejected", "ok", False, "discarded"),
        ("ok", "ok", False, "unchanged"),
        ("spec_conflict", "ok", False, "unchanged"),
        ("ok", "max_turns", False, "discarded"),
        ("error", "timeout", False, "discarded"),
        ("ok", "ok", True, None),
        ("ok", "max_turns", True, None),
        ("spec_conflict", "ok", True, None),
    ],
)
def test_what_a_call_settles_before_any_verification(
    signal: AgentSignal, status: AgentStatus, changed: bool, outcome: str | None
) -> None:
    assert settled_without_verifying(signal, status, changed) == outcome


def test_a_record_carries_everything_but_the_scenario() -> None:
    record = RefactorRecord("A-001", "applied", 2, commit="c", collateral=("A-002",))
    assert record.data() == {
        "outcome": "applied",
        "attempt": 2,
        "commit": "c",
        "failed_at": None,
        "collateral": ["A-002"],
    }


def test_a_scored_command_control_measures_its_score() -> None:
    mutation = CommandControl(id="cov", level="L2", run="c {out}", report="coverage", mode="block")
    entry = CommandEntry(mutation, Executed(_RAN, Judged(CoverageReport(1, 4), Passed())))

    assert oracle_measures([entry]) == {"cov": Measure(score=25.0)}


def test_one_case_gone_is_a_regression_too() -> None:
    assert refactor_regressions({"unit": Measure(passed=1)}, {}) == [
        "unit: pasan 0 casos, y en el verde pasaban 1"
    ]
