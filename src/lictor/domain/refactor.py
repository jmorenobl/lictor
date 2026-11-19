"""The refactor step after each implemented scenario (ADR-0010): what it may end in, and why.

A refactor changes no behaviour, so what the oracle measured when the scenario turned green must
not go down: no JUnit control may pass fewer cases, and no scored control may leave more untested
than it did (lines and branches not covered, mutants not killed). Without that, a refactor could
delete the agent's unit tests and pass: a control that runs no case may pass, and a coverage that
only measures doesn't block. The percentage is no guard: removing covered code lowers it while
leaving untested exactly what was (F61), and that is the simplification a refactor is for.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from lictor.domain.model import AgentSignal, AgentStatus, CoverageScore, Score
from lictor.domain.reports import JUnitReport
from lictor.domain.results import ControlEntry, Executed, Judged, Read

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


# What a score leaves untested, by kind: the checkpoint keeps the kind, a message its words.
MissKind = Literal["lines", "branches", "mutants"]
_MISS_WORDS: Final[Mapping[MissKind, str]] = {
    "lines": "líneas sin cubrir",
    "branches": "ramas sin cubrir",
    "mutants": "mutantes sin matar",
}


@dataclass(frozen=True)
class Measure:
    """What one control measured: cases that passed, and what its score left untested."""

    passed: int | None = None
    misses: tuple[tuple[MissKind, int], ...] | None = None


def score_misses(score: Score) -> tuple[tuple[MissKind, int], ...]:
    """What a score leaves untested: counts, which removing tested code never raises."""
    if isinstance(score, CoverageScore):
        return (
            ("lines", score.num_statements - score.covered_lines),
            ("branches", score.num_branches - score.covered_branches),
        )
    # Skipped mutants were never tried, as in `mutation_score`: only the rest can be left alive.
    return (("mutants", score.total - score.skipped - score.killed),)


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
        return Measure(misses=score_misses(outcome.score)) if outcome.score is not None else None
    if not isinstance(outcome, Executed) or not isinstance(outcome.judgement, Judged):
        return None
    judged = outcome.judgement
    passed = (
        sum(1 for case in judged.cases if case.status == "passed")
        if isinstance(judged.report, JUnitReport)
        else None
    )
    misses = score_misses(judged.score) if judged.score is not None else None
    if passed is None and misses is None:
        return None
    return Measure(passed=passed, misses=misses)


def measures_record(measures: Mapping[str, Measure]) -> dict[str, dict[str, object]]:
    """The measures as the checkpoint keeps them."""
    return {
        cid: {"passed": m.passed, "misses": None if m.misses is None else dict(m.misses)}
        for cid, m in measures.items()
    }


def measures_from_record(record: Mapping[str, Mapping[str, object]]) -> dict[str, Measure]:
    """Read back what `measures_record` wrote; a field of another type counts as absent."""
    return {
        cid: Measure(passed=_int_or_none(fields.get("passed")), misses=_misses_or_none(fields))
        for cid, fields in record.items()
    }


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _misses_or_none(fields: Mapping[str, object]) -> tuple[tuple[MissKind, int], ...] | None:
    value = fields.get("misses")
    if not isinstance(value, Mapping):
        return None
    found: list[tuple[MissKind, int]] = []
    for kind in _MISS_WORDS:
        count = _int_or_none(value.get(kind))
        if count is not None:
            found.append((kind, count))
    return tuple(found)


def refactor_regressions(baseline: Mapping[str, Measure], now: Mapping[str, Measure]) -> list[str]:
    """Say, for each control, what the refactor measures below the green it started from."""
    found: list[str] = []
    for cid, before in baseline.items():
        after = now.get(cid, Measure())
        if before.passed is not None and (after.passed or 0) < before.passed:
            found.append(
                f"{cid}: pasan {after.passed or 0} casos, y en el verde pasaban {before.passed}"
            )
        if before.misses is not None:
            found.extend(_more_untested(cid, before.misses, after.misses))
    return found


def _more_untested(
    cid: str,
    before: tuple[tuple[MissKind, int], ...],
    after: tuple[tuple[MissKind, int], ...] | None,
) -> list[str]:
    if after is None:
        return [f"{cid}: sin puntuación, y en el verde la tenía"]
    now = dict(after)
    found: list[str] = []
    for kind, count in before:
        left = now.get(kind)
        if left is None:
            found.append(f"{cid}: no cuenta {_MISS_WORDS[kind]}, y en el verde sí")
        elif left > count:
            found.append(f"{cid}: {_MISS_WORDS[kind]}: {left}, y en el verde {count}")
    return found


def regression_reason(regressions: Sequence[str]) -> str:
    """The reason of a refactor that measures less than its green."""
    return "El refactor deja sin probar más de lo que dejaba el verde:\n" + "\n".join(
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
