"""The refactor after each implemented green (ADR-0010): how it ends, and what it may not lower."""

import pytest

from lictor.domain.controls import CommandControl, ReaderControl
from lictor.domain.model import (
    AgentSignal,
    AgentStatus,
    CoverageScore,
    MutationScore,
    TestCaseResult,
)
from lictor.domain.refactor import (
    Measure,
    RefactorRecord,
    discards_text,
    guards_tests,
    measures_from_record,
    measures_record,
    oracle_measures,
    refactor_regressions,
    refactor_subject,
    regression_reason,
    score_misses,
    settled_without_verifying,
    unit_tests_rule,
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

    assert oracle_measures([read, lint]) == {
        "coverage": Measure(misses=(("lines", 1), ("branches", 0)))
    }


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


_COVERED = (("lines", 2), ("branches", 2))


def test_measures_go_through_the_checkpoint_as_they_were() -> None:
    measures = {
        "unit": Measure(passed=4),
        "coverage": Measure(misses=_COVERED),
        "mutation": Measure(misses=(("mutants", 7),)),
    }
    assert measures_from_record(measures_record(measures)) == measures


@pytest.mark.parametrize("bad", ["4", True, None, 1.5])
def test_a_count_of_another_type_reads_as_absent(bad: object) -> None:
    record = {"unit": {"passed": bad, "misses": {"lines": bad, "branches": 3, "otra": 1}}}
    assert measures_from_record(record) == {"unit": Measure(misses=(("branches", 3),))}


@pytest.mark.parametrize("record", [{}, {"misses": None}, {"misses": [1]}, {"score": 80.0}])
def test_misses_of_another_shape_read_as_no_score(record: dict[str, object]) -> None:
    # A 0.7.0 checkpoint kept the percentage under `score`: it reads as no measure at all.
    assert measures_from_record({"c": record}) == {"c": Measure()}


def test_what_a_coverage_leaves_untested_is_its_lines_and_branches_not_run() -> None:
    score = CoverageScore(
        covered_lines=126, num_statements=128, covered_branches=48, num_branches=50
    )
    assert score_misses(score) == (("lines", 2), ("branches", 2))


def test_what_a_mutation_leaves_untested_is_every_tried_mutant_not_killed() -> None:
    score = MutationScore(killed=90, survived=4, timeout=2, no_tests=1, skipped=5, total=102)
    assert score_misses(score) == (("mutants", 7),)


def test_fewer_passing_cases_than_the_green_is_a_regression() -> None:
    assert refactor_regressions({"unit": Measure(passed=3)}, {"unit": Measure(passed=2)}) == [
        "unit: pasan 2 casos, y en el verde pasaban 3"
    ]


def test_as_many_or_more_cases_is_no_regression() -> None:
    baseline = {"unit": Measure(passed=3)}
    assert refactor_regressions(baseline, {"unit": Measure(passed=3)}) == []
    assert refactor_regressions(baseline, {"unit": Measure(passed=5)}) == []


def _coverage(lines: int, statements: int, branches: int, all_branches: int) -> Measure:
    score = CoverageScore(
        covered_lines=lines,
        num_statements=statements,
        covered_branches=branches,
        num_branches=all_branches,
    )
    return Measure(misses=score_misses(score))


@pytest.mark.parametrize(
    ("green", "refactor"),
    [
        # The three refactors F61 discarded in the second pass of phase 2: the percentage went
        # down by a tenth (97,8 → 97,7 %, 98,4 → 98,3 %, 96,7 → 96,6 %) because they removed
        # covered code, and they left untested exactly what the green did.
        ((128, 130, 52, 54), (126, 128, 48, 50)),
        ((178, 180, 72, 74), (176, 178, 68, 70)),
        ((205, 210, 89, 94), (203, 208, 85, 90)),
    ],
)
def test_removing_covered_code_is_no_regression(
    green: tuple[int, int, int, int], refactor: tuple[int, int, int, int]
) -> None:
    baseline = {"coverage": _coverage(*green)}
    assert refactor_regressions(baseline, {"coverage": _coverage(*refactor)}) == []


def test_more_lines_or_branches_left_uncovered_is_a_regression() -> None:
    baseline = {"coverage": _coverage(128, 130, 52, 54)}
    # More code and a higher percentage, but one more line and one more branch never run.
    now = {"coverage": _coverage(197, 200, 97, 100)}
    assert refactor_regressions(baseline, now) == [
        "coverage: líneas sin cubrir: 3, y en el verde 2",
        "coverage: ramas sin cubrir: 3, y en el verde 2",
    ]


def test_deleting_the_unit_tests_leaves_everything_uncovered() -> None:
    baseline = {"unit": Measure(passed=20), "coverage": _coverage(128, 130, 52, 54)}
    now = {"unit": Measure(passed=0), "coverage": _coverage(0, 130, 0, 54)}
    assert refactor_regressions(baseline, now) == [
        "unit: pasan 0 casos, y en el verde pasaban 20",
        "coverage: líneas sin cubrir: 130, y en el verde 2",
        "coverage: ramas sin cubrir: 54, y en el verde 2",
    ]


def test_no_score_at_all_or_a_kind_it_no_longer_counts_is_a_regression() -> None:
    baseline = {"coverage": Measure(misses=_COVERED)}
    assert refactor_regressions(baseline, {}) == ["coverage: sin medida, y en el verde la tenía"]
    assert refactor_regressions(baseline, {"coverage": Measure(misses=(("lines", 2),))}) == [
        "coverage: no cuenta ramas sin cubrir, y en el verde sí"
    ]
    assert refactor_regressions(baseline, {"coverage": Measure(misses=_COVERED)}) == []


def test_fewer_mutants_alive_is_no_regression_and_more_is() -> None:
    baseline = {"mutation": Measure(misses=(("mutants", 7),))}
    assert refactor_regressions(baseline, {"mutation": Measure(misses=(("mutants", 5),))}) == []
    assert refactor_regressions(baseline, {"mutation": Measure(misses=(("mutants", 8),))}) == [
        "mutation: mutantes sin matar: 8, y en el verde 7"
    ]


def test_a_control_that_measured_nothing_in_the_green_demands_nothing() -> None:
    assert refactor_regressions({}, {"unit": Measure(passed=0)}) == []
    assert refactor_regressions({"unit": Measure()}, {}) == []


def test_a_control_gone_counts_as_zero_cases() -> None:
    assert refactor_regressions({"unit": Measure(passed=2)}, {}) == [
        "unit: pasan 0 casos, y en el verde pasaban 2"
    ]


def test_the_reason_lists_each_regression() -> None:
    assert regression_reason(["a", "b"]) == (
        "El refactor deja peor que el verde lo que miden los controles:\n- a\n- b"
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

    assert oracle_measures([entry]) == {"cov": Measure(misses=(("lines", 3), ("branches", 0)))}


def test_one_case_gone_is_a_regression_too() -> None:
    assert refactor_regressions({"unit": Measure(passed=1)}, {}) == [
        "unit: pasan 0 casos, y en el verde pasaban 1"
    ]


def test_only_a_control_that_counts_tests_or_what_they_leave_guards_them() -> None:
    assert not guards_tests({})
    assert not guards_tests({"cx": Measure(misses=(("excess", 3), ("peak", 1)))})
    assert not guards_tests({"api": Measure(breaks=frozenset())})
    assert guards_tests({"unit": Measure(passed=0)})
    assert guards_tests({"cov": Measure(misses=(("branches", 0),))})
    assert guards_tests({"mut": Measure(misses=(("mutants", 2),))})
    assert guards_tests({"cov": Measure(misses=(("lines", 1),))})


def test_the_refactor_hears_of_tests_only_what_the_verification_guards() -> None:
    assert "se descarta" in unit_tests_rule(guarded=True)
    assert unit_tests_rule(guarded=False) == (
        "- Puedes reorganizar los tests unitarios para que sigan al código."
    )
    assert "deje más sin probar" in discards_text(guarded=True)
    assert discards_text(guarded=False) == " o salga de lo que cambió el escenario"
