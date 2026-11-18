"""The refactor step after each implemented scenario (ADR-0010): what it may end in, and why.

A refactor changes no behaviour, so what the oracle measured when the scenario turned green must
not go down: no JUnit control may pass fewer cases, and no scored control may score less. Without
that, a refactor could delete the agent's unit tests and pass: a control that runs no case may
pass, and a coverage that only measures doesn't block.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from lictor.domain.model import AgentSignal, AgentStatus
from lictor.domain.reports import JUnitReport
from lictor.domain.results import ControlEntry, Executed, Judged, Read
from lictor.domain.rules import percent_text

RefactorOutcome = Literal["applied", "discarded", "unchanged"]
REFACTOR_REGRESSION: Final[str] = "L0-regresión"
REFACTORED: Final[str] = "lictor.scenario.refactored.v1"
# A call cut short that left nothing: there is no tree to judge, and nothing says it was done.
CUT_SHORT: Final[str] = "La llamada de refactor se cortó sin dejar cambios"
_FINISHED_TURN: Final[frozenset[str]] = frozenset({"ok"})


@dataclass(frozen=True)
class RefactorRecord:
    """What came of the refactor of a scenario, as `scenario.refactored` tells it."""

    scenario_id: str
    outcome: RefactorOutcome
    attempt: int
    commit: str | None = None  # the refactor's own commit, when it was applied
    failed_at: str | None = None  # where a discarded one failed
    collateral: tuple[str, ...] = ()  # pending scenarios its tree also turned green

    def data(self) -> dict[str, object]:
        """The event's data: every field but the scenario, which the event carries apart."""
        return {
            "outcome": self.outcome,
            "attempt": self.attempt,
            "commit": self.commit,
            "failed_at": self.failed_at,
            "collateral": list(self.collateral),
        }


def refactor_subject(scenario_id: str) -> str:
    """The subject of a refactor commit: Lictor writes it, never the agent."""
    return f"refactor({scenario_id}): mejorar el código sin cambiar el comportamiento"


@dataclass(frozen=True)
class Measure:
    """What one control measured: cases that passed, and its score, when it has them."""

    passed: int | None = None
    score: float | None = None


def oracle_measures(entries: Sequence[ControlEntry]) -> dict[str, Measure]:
    """What each control of a verification measured, by id, if it measured anything."""
    found: dict[str, Measure] = {}
    for entry in entries:
        measure = _measure_of(entry)
        if measure is not None:
            found[entry.control.id] = measure
    return found


def _measure_of(entry: ControlEntry) -> Measure | None:
    outcome = entry.outcome
    if isinstance(outcome, Read):
        return Measure(score=outcome.score.percent) if outcome.score is not None else None
    if not isinstance(outcome, Executed) or not isinstance(outcome.judgement, Judged):
        return None
    judged = outcome.judgement
    passed = (
        sum(1 for case in judged.cases if case.status == "passed")
        if isinstance(judged.report, JUnitReport)
        else None
    )
    score = judged.score.percent if judged.score is not None else None
    if passed is None and score is None:
        return None
    return Measure(passed=passed, score=score)


def measures_record(measures: Mapping[str, Measure]) -> dict[str, dict[str, float | int | None]]:
    """The measures as the checkpoint keeps them."""
    return {cid: {"passed": m.passed, "score": m.score} for cid, m in measures.items()}


def measures_from_record(record: Mapping[str, Mapping[str, object]]) -> dict[str, Measure]:
    """Read back what `measures_record` wrote; a field of another type counts as absent."""
    return {
        cid: Measure(passed=_int_or_none(fields.get("passed")), score=_float_or_none(fields))
        for cid, fields in record.items()
    }


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float_or_none(fields: Mapping[str, object]) -> float | None:
    value = fields.get("score")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def refactor_regressions(baseline: Mapping[str, Measure], now: Mapping[str, Measure]) -> list[str]:
    """Say, for each control, what the refactor measures below the green it started from."""
    found: list[str] = []
    for cid, before in baseline.items():
        after = now.get(cid, Measure())
        if before.passed is not None and (after.passed or 0) < before.passed:
            found.append(
                f"{cid}: pasan {after.passed or 0} casos, y en el verde pasaban {before.passed}"
            )
        if before.score is not None and (after.score is None or after.score < before.score):
            score = "sin puntuación" if after.score is None else percent_text(after.score)
            found.append(f"{cid}: {score}, y en el verde {percent_text(before.score)}")
    return found


def regression_reason(regressions: Sequence[str]) -> str:
    """The reason of a refactor that measures less than its green."""
    return "El refactor baja lo que mide la verificación del verde:\n" + "\n".join(
        f"- {line}" for line in regressions
    )


def settled_without_verifying(
    signal: AgentSignal, status: AgentStatus, changed: bool
) -> RefactorOutcome | None:
    """What a refactor call ends in before any verification, or None if its tree is verified.

    A call the run state rejected, or whose copy can't be read, is discarded. A call that finished
    its turn and changed nothing had nothing to improve; one cut short that changed nothing is
    discarded. Any other tree is verified: what decides is whether it passes.
    """
    if signal == "rejected":
        return "discarded"
    if changed:
        return None
    return "unchanged" if status in _FINISHED_TURN else "discarded"
