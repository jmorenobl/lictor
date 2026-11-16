"""Every verdict is a rule over facts, with its message (ADR-0009).

The contract in `characterization_reports.jsonl` checks them through the sites; these check each
rule on its own, so mutation testing reaches them.
"""

import pytest

from lictor.domain.model import CommandOutput, TestCaseResult
from lictor.domain.reports import (
    CoverageReport,
    InvalidReport,
    JUnitReport,
    MissingReport,
    MutationReport,
)
from lictor.domain.verdicts import (
    NOTHING_MEASURED,
    Crashed,
    Execution,
    Failed,
    Passed,
    Reported,
    VerdictRule,
    acceptance_check_verdict,
    coverage_verdict,
    exit_code_verdict,
    junit_verdict,
    mutation_verdict,
    report_cases,
    report_score,
    trust,
)

_PASSED = TestCaseResult(name="a")


def _ran(code: int) -> Execution:
    return Execution(code, 60)


_CUT = Execution(-9, 60, timed_out=True)
_CUT_REASON = "Se cortó a los 60 s, su tiempo límite"
_SKIPPED = TestCaseResult(name="s", status="skipped")
_FAILURE = TestCaseResult(name="f", status="failure")
_ERROR = TestCaseResult(name="e", status="error")


def test_a_failure_needs_a_reason() -> None:
    with pytest.raises(ValueError, match="motivo"):
        Failed(" ")


@pytest.mark.parametrize(
    ("code", "empty", "expected"),
    [
        (0, (), Passed()),
        (5, (5,), Passed()),
        (5, (), Failed("inicio\nfin")),
        (1, (5,), Failed("inicio\nfin")),
    ],
)
def test_exit_code(code: int, empty: tuple[int, ...], expected: object) -> None:
    assert exit_code_verdict("inicio\nfin", _ran(code), empty) == expected


def test_exit_code_keeps_the_last_40_lines() -> None:
    output = "\n".join(f"línea {i}" for i in range(100))
    verdict = exit_code_verdict(output, _ran(1), ())
    assert verdict == Failed("\n".join(f"línea {i}" for i in range(60, 100)))


@pytest.mark.parametrize("output", ["", "\n \n", "x\n" + "\n" * 50])
def test_exit_code_names_the_code_when_the_last_lines_say_nothing(output: str) -> None:
    """Text followed by 40 blank lines leaves nothing in the tail, as no output at all."""
    assert exit_code_verdict(output, _ran(3), ()) == Failed("El comando falló con el código 3")


def test_exit_code_trims_the_tail() -> None:
    assert exit_code_verdict("  fallo  \n", _ran(1), ()) == Failed("fallo")


@pytest.mark.parametrize(
    ("report", "code", "empty", "expected"),
    [
        (MissingReport(), 5, (5,), Failed("La suite no dejó informe JUnit (código de salida 5)")),
        (InvalidReport("roto"), 0, (), Failed("No se pudo leer el informe JUnit: roto")),
        (JUnitReport(), 5, (5,), Passed()),
        (JUnitReport(), 0, (5,), Failed("No hay casos de test (código de salida 0)")),
        (JUnitReport(), 0, (0,), Passed()),
        (JUnitReport((_PASSED,)), 0, (), Passed()),
        (JUnitReport((_SKIPPED,)), 0, (), Passed()),
        (JUnitReport((_PASSED,)), 5, (5,), Failed("La suite falló con el código 5")),
        (JUnitReport((_PASSED, _FAILURE)), 0, (), Failed("Fallaron 1 casos de test")),
        (JUnitReport((_FAILURE, _ERROR)), 1, (), Failed("Fallaron 2 casos de test")),
    ],
)
def test_junit(
    report: JUnitReport | MissingReport | InvalidReport,
    code: int,
    empty: tuple[int, ...],
    expected: object,
) -> None:
    assert junit_verdict(report, _ran(code), empty) == expected


_COVERAGE = CoverageReport(1, 2)
_NOTHING = CoverageReport(0, 0)  # ADR-0009, change 6: nothing to measure is no score


@pytest.mark.parametrize(
    ("report", "code", "empty", "expected"),
    [
        (MissingReport(), 5, (5,), Passed()),
        (MissingReport(), 0, (), Failed("Sin informe de cobertura")),
        (MissingReport(), 0, (5,), Failed("Sin informe de cobertura")),
        (
            InvalidReport("roto"),
            0,
            (),
            Failed("El informe de cobertura no es válido: roto"),
        ),
        (_COVERAGE, 0, (), Passed()),
        (_COVERAGE, 5, (5,), Passed()),
        (_COVERAGE, 1, (), Failed("Los tests de la cobertura fallaron con el código 1")),
        (_COVERAGE, 5, (), Failed("Los tests de la cobertura fallaron con el código 5")),
        (_NOTHING, 5, (5,), Passed()),
        (_NOTHING, 0, (), Failed(NOTHING_MEASURED)),
        (_NOTHING, 0, (5,), Failed(NOTHING_MEASURED)),
        (_NOTHING, 1, (), Failed(NOTHING_MEASURED)),
        (CoverageReport(0, 0, 1, 2), 0, (), Passed()),
    ],
)
def test_coverage(
    report: CoverageReport | MissingReport | InvalidReport,
    code: int,
    empty: tuple[int, ...],
    expected: object,
) -> None:
    assert coverage_verdict(report, _ran(code), empty) == expected


_MUTANTS = MutationReport({"killed": 1, "total": 2})


@pytest.mark.parametrize(
    ("report", "code", "expected"),
    [
        (MissingReport(), 0, Failed("Sin informe de mutación")),
        (InvalidReport("roto"), 0, Failed("El informe de mutación no es válido: roto")),
        (_MUTANTS, 0, Passed()),
        (_MUTANTS, 4, Failed("La pasada de mutación falló con el código 4")),
        (
            MutationReport({"total": 0}),
            0,
            Failed("El informe de mutación no tiene mutantes probados"),
        ),
        (
            MutationReport({"total": 5, "skipped": 5}),
            0,
            Failed("El informe de mutación no tiene mutantes probados"),
        ),
        (MutationReport({"killed": 1, "total": 2, "skipped": 1}), 0, Passed()),
        (
            MutationReport({"total": 0}),
            1,
            Failed("La pasada de mutación falló con el código 1"),
        ),
    ],
)
def test_mutation(
    report: MutationReport | MissingReport | InvalidReport, code: int, expected: object
) -> None:
    assert mutation_verdict(report, _ran(code)) == expected


def test_only_junit_has_cases_and_only_scores_have_scores() -> None:
    cases = (_PASSED,)
    assert report_cases(JUnitReport(cases)) == cases
    assert report_cases(_COVERAGE) == ()
    assert report_cases(None) == ()
    assert report_score(_COVERAGE) == _COVERAGE.score
    assert report_score(_NOTHING) is None
    assert report_score(CoverageReport(0, 0, 1, 2)) is not None
    assert report_score(_MUTANTS) == _MUTANTS.score
    assert report_score(MutationReport({"total": 3, "skipped": 3})) is None
    assert report_score(JUnitReport(cases)) is None
    assert report_score(MissingReport()) is None
    assert report_score(None) is None


@pytest.mark.parametrize(
    ("report", "code", "expected"),
    [
        (MissingReport(), 1, Crashed("La suite terminó con código 1 sin dejar informe")),
        (
            InvalidReport("roto"),
            0,
            Crashed("La suite terminó con código 0 con un informe JUnit no válido: roto"),
        ),
        (
            JUnitReport(),
            0,
            Crashed("La suite terminó con código 0 sin casos de test en su informe"),
        ),
        (
            JUnitReport((_FAILURE,)),
            3,
            Crashed("La suite se interrumpió con código 3: su informe no es fiable"),
        ),
        (
            JUnitReport((_PASSED,)),
            1,
            Crashed("La suite terminó con código 1 pero ningún test falló en su informe"),
        ),
        (
            JUnitReport((_SKIPPED,)),
            1,
            Crashed("La suite terminó con código 1 pero ningún test falló en su informe"),
        ),
        (JUnitReport((_PASSED,)), 0, Reported(JUnitReport((_PASSED,)))),
        (JUnitReport((_FAILURE,)), 0, Reported(JUnitReport((_FAILURE,)))),
        (JUnitReport((_FAILURE,)), 1, Reported(JUnitReport((_FAILURE,)))),
        (JUnitReport((_ERROR,)), 1, Reported(JUnitReport((_ERROR,)))),
    ],
)
def test_trust(
    report: JUnitReport | MissingReport | InvalidReport, code: int, expected: object
) -> None:
    assert trust(report, _ran(code), (1,)) == expected


def test_trust_takes_the_failure_codes_from_the_caller() -> None:
    report = JUnitReport((_FAILURE,))
    assert trust(report, _ran(2), (1, 2)) == Reported(report)
    assert isinstance(trust(report, _ran(2), (1,)), Crashed)


def test_a_trusted_report_gives_its_cases() -> None:
    assert Reported(JUnitReport((_PASSED, _FAILURE))).cases == (_PASSED, _FAILURE)


@pytest.mark.parametrize(
    ("trusted", "expected"),
    [
        (Crashed("se cayó"), Failed("se cayó")),
        (Reported(JUnitReport((_PASSED, _SKIPPED))), Passed()),
        (Reported(JUnitReport((_PASSED, _FAILURE, _ERROR))), Failed("Fallaron 2 casos de test")),
    ],
)
def test_the_acceptance_of_check(trusted: Crashed | Reported, expected: object) -> None:
    assert acceptance_check_verdict(trusted) == expected


def test_an_execution_comes_from_how_the_command_ended() -> None:
    output = CommandOutput(returncode=-9, stdout="", stderr="", duration_ms=12, timed_out=True)
    assert Execution.of(output, 30) == Execution(-9, 30, timed_out=True, duration_ms=12)
    assert Execution.of(CommandOutput(0, "", ""), 30) == Execution(0, 30)


def test_a_command_cut_by_its_timeout_fails_whatever_it_left() -> None:
    """ADR-0009, change 10: its exit code says nothing, so it is not what the reason names."""
    assert exit_code_verdict("algo", _CUT, (-9,)) == Failed(_CUT_REASON)
    assert junit_verdict(JUnitReport((_PASSED,)), _CUT, ()) == Failed(_CUT_REASON)
    assert junit_verdict(JUnitReport(), _CUT, (-9,)) == Failed(_CUT_REASON)
    assert coverage_verdict(_COVERAGE, _CUT, (-9,)) == Failed(_CUT_REASON)
    assert coverage_verdict(MissingReport(), _CUT, (-9,)) == Failed(_CUT_REASON)
    assert mutation_verdict(_MUTANTS, _CUT) == Failed(_CUT_REASON)


def test_a_suite_cut_by_its_timeout_cannot_be_trusted() -> None:
    expected = Crashed("La suite se cortó a los 60 s, su tiempo límite: su informe no es fiable")
    assert trust(JUnitReport((_FAILURE,)), _CUT, (1, -9)) == expected
    assert trust(MissingReport(), _CUT, (1,)) == expected


def test_a_verdict_rule_says_how_a_control_is_judged() -> None:
    rule = VerdictRule("junit", 30, (5,))
    assert (rule.report, rule.timeout_s, rule.empty_exit_codes) == ("junit", 30, (5,))
    assert VerdictRule("exit-code", 10).empty_exit_codes == ()
