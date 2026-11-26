"""Whether a control passes, from the facts its report gives and how its command ended (ADR-0009).

Each verdict is a rule over facts: a reader only reads. The messages are the contract of
`tests/unit/characterization_reports.jsonl`; a change to one is a change of that contract.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import assert_never

from lictor.domain.api import broken_reason
from lictor.domain.complexity import over_limit, over_limit_reason
from lictor.domain.duplication import clones_reason, duplication_of
from lictor.domain.model import CommandOutput, CoverageScore, Score, TestCaseResult
from lictor.domain.reports import (
    ApiComparison,
    ApiReport,
    ComplexityReport,
    CoverageReport,
    DuplicationReport,
    InvalidReport,
    JUnitReport,
    MissingReport,
    MutationReport,
    Report,
    ReportFormat,
    ReportOf,
)
from lictor.domain.rules import describe_score, minimum_text, score_meets

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


@dataclass(frozen=True)
class Execution:
    """How a command ended, as the verdicts need it: with a timeout its exit code says nothing."""

    exit_code: int
    timeout_s: int
    timed_out: bool = False
    duration_ms: int = 0

    @classmethod
    def of(cls, output: CommandOutput, timeout_s: int) -> "Execution":
        return cls(output.returncode, timeout_s, output.timed_out, output.duration_ms)

    @property
    def cut_reason(self) -> str:
        return f"Se cortó a los {self.timeout_s} s, su tiempo límite"


@dataclass(frozen=True)
class VerdictRule:
    """What of a control decides its verdict: the format of its report, its timeout and its codes.

    `empty_exit_codes` are the codes that mean a run with nothing to run, which may pass;
    `limit`, the complexity no function may exceed, only with a complexity report (ADR-0013).
    """

    report: ReportFormat
    timeout_s: int
    empty_exit_codes: tuple[int, ...] = ()
    limit: int | None = None


def exit_code_verdict(
    output: str, execution: Execution, empty_exit_codes: Collection[int]
) -> Verdict:
    """0, or a code the profile declares as an empty run, passes; else the end of the output."""
    if execution.timed_out:
        return Failed(execution.cut_reason)
    exit_code = execution.exit_code
    if exit_code == 0 or exit_code in empty_exit_codes:
        return Passed()
    tail = "\n".join(output.splitlines()[-_TAIL_LINES:]).strip()
    return Failed(tail or f"El comando falló con el código {exit_code}")


def junit_verdict(
    report: ReportOf[JUnitReport], execution: Execution, empty_exit_codes: Collection[int]
) -> Verdict:
    """A JUnit control: no failed case and a 0, or no case at all with an empty-run code."""
    if execution.timed_out:
        return Failed(execution.cut_reason)
    exit_code = execution.exit_code
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
    report: ReportOf[CoverageReport], execution: Execution, empty_exit_codes: Collection[int]
) -> Verdict:
    """A coverage control that ran its own tests: a report, and tests that ended well."""
    if execution.timed_out:
        return Failed(execution.cut_reason)
    exit_code = execution.exit_code
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


def coverage_read_verdict(report: ReportOf[CoverageReport], *, ran_no_case: bool) -> Verdict:
    """A reader of coverage over the run of a JUnit control, which already has its verdict.

    It doesn't read that control's exit code again: `ran_no_case` says the control passed with no
    case, which is how it counts a run with no tests. Then, and only then, nothing to score
    passes; a report that can't be read never does (ADR-0008, point 6).
    """
    match report:
        case MissingReport():
            return Passed() if ran_no_case else Failed("Sin informe de cobertura")
        case InvalidReport(reason=reason):
            return Failed(f"El informe de cobertura no es válido: {reason}")
        case CoverageReport(measured=False):
            return Passed() if ran_no_case else Failed(NOTHING_MEASURED)
        case CoverageReport():
            return Passed()
        case _:
            assert_never(report)


def _nothing_to_score(exit_code: int, empty_exit_codes: Collection[int], reason: str) -> Verdict:
    """No report and a report that measured nothing are one fact: only an empty run explains it."""
    return Passed() if exit_code in empty_exit_codes else Failed(reason)


def _own_report[R](
    report: ReportOf[R], execution: Execution, kind: str, missing: str, command: str
) -> R | Failed:
    """What a command that writes its own report must show before its report is judged.

    It wasn't cut by its timeout, it left a report that can be read, and it ended with 0. `kind`
    names the report in a message, and `command` the command that failed.
    """
    if execution.timed_out:
        return Failed(execution.cut_reason)
    if isinstance(report, MissingReport):
        return Failed(missing)
    if isinstance(report, InvalidReport):
        return Failed(f"El informe de {kind} no es válido: {report.reason}")
    if execution.exit_code != 0:
        return Failed(f"{command} falló con el código {execution.exit_code}")
    return report


def mutation_verdict(report: ReportOf[MutationReport], execution: Execution) -> Verdict:
    """A mutation pass: a report, a pass that ended well, and mutants in it."""
    found = _own_report(
        report, execution, "mutación", "Sin informe de mutación", "La pasada de mutación"
    )
    if isinstance(found, Failed):
        return found
    if found.tried == 0:
        return Failed("El informe de mutación no tiene mutantes probados")
    return Passed()


def complexity_verdict(
    report: ReportOf[ComplexityReport], execution: Execution, limit: int
) -> Verdict:
    """A complexity control: a report, a command that ended well, and no function above `limit`.

    A file that can't be parsed fails the command and leaves no report, so it is never a pass.
    """
    missing = f"Sin informe de complejidad (código de salida {execution.exit_code})"
    found = _own_report(report, execution, "complejidad", missing, "El comando de complejidad")
    if isinstance(found, Failed):
        return found
    over = over_limit(found, limit)
    return Failed(over_limit_reason(over)) if over.functions else Passed()


def duplication_verdict(report: ReportOf[DuplicationReport], execution: Execution) -> Verdict:
    """A duplication control: a report, a command that ended well, and no clone (ADR-0014).

    A file that can't be parsed fails the command and leaves no report, so it is never a pass.
    """
    missing = f"Sin informe de duplicación (código de salida {execution.exit_code})"
    found = _own_report(report, execution, "duplicación", missing, "El comando de duplicación")
    if isinstance(found, Failed):
        return found
    duplication = duplication_of(found)
    return Failed(clones_reason(duplication)) if duplication.clones else Passed()


def api_verdict(report: ReportOf[ApiReport] | ApiComparison, execution: Execution) -> Verdict:
    """An API control (ADR-0017): a report, a command that ended well, and nothing of the base
    broken that the person hasn't accepted.

    Without a base there is nothing to compare, and the report is only facts: it passes. A base
    that couldn't be measured is no comparison, and never a pass.
    """
    missing = f"Sin informe de API (código de salida {execution.exit_code})"
    found: ApiReport | ApiComparison | Failed = _own_report(
        report, execution, "API", missing, "El comando de API"
    )
    if isinstance(found, Failed):
        return found
    if isinstance(found, ApiReport):
        return Passed()
    if found.problem is not None:
        return Failed(found.problem)
    return Failed(broken_reason(found)) if found.broken else Passed()


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
    report: ReportOf[JUnitReport], execution: Execution, failure_exit_codes: Sequence[int]
) -> Trust:
    """Whether an acceptance report can be trusted: a suite that crashed proves nothing.

    A non-zero exit is normal while scenarios are pending, but only with a code that means
    "tests failed" and a report that shows it. A suite cut by its timeout left whatever it had.
    """
    if execution.timed_out:
        return Crashed(
            f"La suite se cortó a los {execution.timeout_s} s, su tiempo límite: "
            "su informe no es fiable"
        )
    exit_code = execution.exit_code
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


def enforce_min_score(verdict: Verdict, score: Score | None, minimum: float | None) -> Verdict:
    """Fail a control whose score is below the minimum it declares.

    A verdict that already failed is left as it is: a run that crashed says so itself. One that
    passed with no score measured nothing (a coverage run with no tests), which no minimum meets.
    """
    if minimum is None or isinstance(verdict, Failed):
        return verdict
    floor = minimum_text(minimum)
    if score is None:
        return Failed(f"El control no midió nada y declara un mínimo de {floor} %")
    if score_meets(score, minimum):
        return verdict
    what = "La cobertura" if isinstance(score, CoverageScore) else "La puntuación de mutación"
    return Failed(f"{what}, {describe_score(score)}, no llega al mínimo de {floor} %")
