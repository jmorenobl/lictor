"""What a test sees of a verification's results, whichever variant each one is (ADR-0009)."""

from dataclasses import dataclass

from lictor.application.usecases.check_project import CheckProjectResult
from lictor.domain.model import Score
from lictor.domain.results import (
    AcceptanceRun,
    Executed,
    NotExecuted,
    Skipped,
    Unjudged,
    VerifyResult,
)
from lictor.domain.verdicts import Failed, Passed, Reported

ACCEPTANCE = "acceptance"


@dataclass(frozen=True)
class Seen:
    """The observable facts of a result: tests assert these, not how the variant is built."""

    ok: bool
    skipped: str | None = None
    error: str | None = None
    exit_code: int | None = None
    mode: str | None = None
    output_path: str | None = None
    score: Score | None = None
    tampered: bool = False


def ids(result: VerifyResult | CheckProjectResult) -> list[str]:
    """The controls a verification reached, in order, and the acceptance if it ran."""
    reached = [entry.control.id for entry in result.controls]
    return [*reached, ACCEPTANCE] if result.acceptance is not None else reached


def seen(result: VerifyResult | CheckProjectResult, control_id: str) -> Seen:
    if control_id == ACCEPTANCE:
        return _acceptance(result)
    entry = next(entry for entry in result.controls if entry.control.id == control_id)
    mode = entry.control.mode
    match entry.outcome:
        case Skipped(reason=reason):
            return Seen(True, skipped=reason, mode=mode)
        case NotExecuted(reason=reason):
            return Seen(False, error=reason, mode=mode)
        case Executed(ran=ran, judgement=judgement):
            code, log = ran.execution.exit_code, ran.log
            if isinstance(judgement, Unjudged):
                return Seen(False, exit_code=code, mode=mode, output_path=log, tampered=True)
            verdict = judgement.verdict
            return Seen(
                isinstance(verdict, Passed),
                error=verdict.reason if isinstance(verdict, Failed) else None,
                exit_code=code,
                mode=mode,
                output_path=log,
                score=judgement.score,
            )
    raise AssertionError(entry.outcome)


def _acceptance(result: VerifyResult | CheckProjectResult) -> Seen:
    acceptance: AcceptanceRun | None = result.acceptance
    assert acceptance is not None, "la verificación no llegó a la aceptación"
    if isinstance(result, CheckProjectResult):
        verdict = result.acceptance_verdict
        ok = isinstance(verdict, Passed)
        error = verdict.reason if isinstance(verdict, Failed) else None
    else:
        ok, error = isinstance(acceptance.judgement, Reported), None
    ran = acceptance.ran
    return Seen(ok, error=error, exit_code=ran.execution.exit_code, output_path=ran.log)
