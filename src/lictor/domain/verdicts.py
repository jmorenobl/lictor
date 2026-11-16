"""Whether a control passes, from the facts its report gives and how its command ended (ADR-0009).

Each verdict is a rule over facts: a reader only reads. The messages are the contract of
`tests/unit/characterization_reports.jsonl`; a change to one is a change of that contract.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import assert_never

from lictor.domain.model import ControlResult, Score, TestCaseResult
from lictor.domain.reports import (
    CoverageReport,
    InvalidReport,
    JUnitReport,
    MissingReport,
    MutationReport,
    Report,
    ReportOf,
)

_FAILED = ("failure", "error")
NOTHING_MEASURED = "El informe de cobertura no midió nada: lo que mide no tiene código"
_TAIL_LINES = 40


@dataclass(frozen=True)
class Passed:
    """The control passes."""


@dataclass(frozen=True)
class Failed:
    """The control fails, and says why: a failure with no reason explains nothing."""

    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("Un fallo necesita un motivo")


type Verdict = Passed | Failed


def exit_code_verdict(output: str, exit_code: int, empty_exit_codes: Collection[int]) -> Verdict:
    """0, or a code the profile declares as an empty run, passes; else the end of the output."""
    if exit_code == 0 or exit_code in empty_exit_codes:
        return Passed()
    tail = "\n".join(output.splitlines()[-_TAIL_LINES:]).strip()
    return Failed(tail or f"El comando falló con el código {exit_code}")


def junit_verdict(
    report: ReportOf[JUnitReport], exit_code: int, empty_exit_codes: Collection[int]
) -> Verdict:
    """A JUnit control: no failed case and a 0, or no case at all with an empty-run code."""
    match report:
        case MissingReport():
            return Failed(f"La suite no dejó informe JUnit (código de salida {exit_code})")
        case InvalidReport(reason=reason):
            return Failed(f"No se pudo leer el informe JUnit: {reason}")
        case JUnitReport(cases=cases):
            return _cases_verdict(cases, exit_code, empty_exit_codes)
        case _:
            assert_never(report)


def _cases_verdict(
    cases: tuple[TestCaseResult, ...], exit_code: int, empty_exit_codes: Collection[int]
) -> Verdict:
    if not cases:
        if exit_code in empty_exit_codes:
            return Passed()
        return Failed(f"No hay casos de test (código de salida {exit_code})")
    failed = [case for case in cases if case.status in _FAILED]
    if failed:
        return Failed(f"Fallaron {len(failed)} casos de test")
    if exit_code != 0:
        return Failed(f"La suite falló con el código {exit_code}")
    return Passed()


def coverage_verdict(
    report: ReportOf[CoverageReport], exit_code: int, empty_exit_codes: Collection[int]
) -> Verdict:
    """A coverage control that ran its own tests: a report, and tests that ended well."""
    match report:
        case MissingReport():
            return _nothing_to_score(exit_code, empty_exit_codes, "Sin informe de cobertura")
        case InvalidReport(reason=reason):
            return Failed(f"El informe de cobertura no es válido: {reason}")
        case CoverageReport(measured=False):
            return _nothing_to_score(exit_code, empty_exit_codes, NOTHING_MEASURED)
        case CoverageReport():
            if exit_code != 0 and exit_code not in empty_exit_codes:
                return Failed(f"Los tests de la cobertura fallaron con el código {exit_code}")
            return Passed()
        case _:
            assert_never(report)


def _nothing_to_score(exit_code: int, empty_exit_codes: Collection[int], reason: str) -> Verdict:
    """No report and a report that measured nothing are one fact: only an empty run explains it."""
    return Passed() if exit_code in empty_exit_codes else Failed(reason)


def mutation_verdict(report: ReportOf[MutationReport], exit_code: int) -> Verdict:
    """A mutation pass: a report, a pass that ended well, and mutants in it."""
    match report:
        case MissingReport():
            return Failed("Sin informe de mutación")
        case InvalidReport(reason=reason):
            return Failed(f"El informe de mutación no es válido: {reason}")
        case MutationReport():
            if exit_code != 0:
                return Failed(f"La pasada de mutación falló con el código {exit_code}")
            if report.tried == 0:
                return Failed("El informe de mutación no tiene mutantes probados")
            return Passed()
        case _:
            assert_never(report)


def report_cases(report: Report | None) -> tuple[TestCaseResult, ...]:
    """The test cases a report lists; only a JUnit report lists any."""
    return report.cases if isinstance(report, JUnitReport) else ()


def report_score(report: Report | None) -> Score | None:
    """The score a report gives: a coverage report that measured, or a pass that tried mutants."""
    if isinstance(report, CoverageReport):
        return report.score if report.measured else None
    if isinstance(report, MutationReport):
        return report.score if report.tried > 0 else None
    return None


def judged_result(verdict: Verdict, report: Report | None, duration_ms: int) -> ControlResult:
    """What a control that ran ends up with, before its minimum score is enforced."""
    return ControlResult(
        ok=isinstance(verdict, Passed),
        duration_ms=duration_ms,
        error=verdict.reason if isinstance(verdict, Failed) else None,
        test_cases=report_cases(report),
        score=report_score(report),
    )


@dataclass(frozen=True)
class Crashed:
    """The suite crashed: its report proves nothing, and the reason says why."""

    reason: str


@dataclass(frozen=True)
class Reported:
    """The suite ran and its report can be trusted. Only `trust` makes one."""

    junit: JUnitReport

    @property
    def cases(self) -> tuple[TestCaseResult, ...]:
        return self.junit.cases


type Trust = Crashed | Reported


def trust(
    report: ReportOf[JUnitReport], exit_code: int, failure_exit_codes: Sequence[int]
) -> Trust:
    """Whether an acceptance report can be trusted: a suite that crashed proves nothing.

    A non-zero exit is normal while scenarios are pending, but only with a code that means
    "tests failed" and a report that shows it.
    """
    match report:
        case MissingReport():
            return Crashed(f"La suite terminó con código {exit_code} sin dejar informe")
        case InvalidReport(reason=reason):
            return Crashed(
                f"La suite terminó con código {exit_code} con un informe JUnit no válido: {reason}"
            )
        case JUnitReport(cases=cases):
            crash = _crash_of(cases, exit_code, failure_exit_codes)
            return Crashed(crash) if crash is not None else Reported(report)
        case _:
            assert_never(report)


def _crash_of(
    cases: tuple[TestCaseResult, ...], exit_code: int, failure_exit_codes: Sequence[int]
) -> str | None:
    if not cases:
        return f"La suite terminó con código {exit_code} sin casos de test en su informe"
    if exit_code == 0:
        return None
    if exit_code not in failure_exit_codes:
        return f"La suite se interrumpió con código {exit_code}: su informe no es fiable"
    if not any(case.status in _FAILED for case in cases):
        return f"La suite terminó con código {exit_code} pero ningún test falló en su informe"
    return None


def acceptance_check_verdict(trusted: Trust) -> Verdict:
    """The acceptance of `lictor check`: a report it can trust, with no failed case."""
    match trusted:
        case Crashed(reason=reason):
            return Failed(reason)
        case Reported(cases=cases):
            failed = [case for case in cases if case.status in _FAILED]
            return Failed(f"Fallaron {len(failed)} casos de test") if failed else Passed()
        case _:
            assert_never(trusted)
