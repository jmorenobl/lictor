"""The refactor step after each implemented scenario (ADR-0010): what it may end in, and why.

A refactor changes no behaviour, so what the oracle measured when the scenario turned green must
not go down: no JUnit control may pass fewer cases, and no scored control may leave more untested
than it did (lines and branches not covered, mutants not killed), no complexity control may
leave more above its limit (ADR-0013), and no duplication control more tokens in clones
(ADR-0014). Without that, a refactor could delete the agent's unit
tests and pass: a control that runs no case may pass, and a coverage that only measures doesn't
block. The percentage is no guard: removing covered code lowers it while leaving untested exactly
what was (F61), and that is the simplification a refactor is for.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from lictor.domain.api import break_line, break_record, breaks_from_record
from lictor.domain.complexity import OverLimit, over_limit
from lictor.domain.controls import CommandControl, ReaderControl
from lictor.domain.duplication import Duplication, duplication_of
from lictor.domain.model import AgentSignal, AgentStatus, CoverageScore, Score
from lictor.domain.reports import (
    ApiBreak,
    ApiComparison,
    Clone,
    CloneOccurrence,
    ComplexityReport,
    DuplicationReport,
    FunctionComplexity,
    JUnitReport,
)
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


# What a control leaves undone, by kind: what a score leaves untested, what a complexity report
# leaves above its limit, or what a duplication report leaves repeated. The checkpoint keeps the
# kind, a message its words.
MissKind = Literal["lines", "branches", "mutants", "excess", "peak", "duplicated"]
_MISS_WORDS: Final[Mapping[MissKind, str]] = {
    "lines": "líneas sin cubrir",
    "branches": "ramas sin cubrir",
    "mutants": "mutantes sin matar",
    "excess": "complejidad por encima del límite, sumada",
    "peak": "complejidad por encima del límite en la peor función",
    "duplicated": "tokens en clones",
}

# What a measure of the code names for the refactor's prompt: the functions above a limit, or the
# clones (ADR-0013, ADR-0014).
type Findings = OverLimit | Duplication


@dataclass(frozen=True)
class Measure:
    """What one control measured: cases that passed, and what it left undone.

    A control that measures the code also keeps what it found, which the refactor's prompt names:
    the functions above a limit (ADR-0013) or the clones (ADR-0014). One of the API keeps what
    breaks against the run's base (ADR-0017): a set, which a refactor may not add to.
    """

    passed: int | None = None
    misses: tuple[tuple[MissKind, int], ...] | None = None
    findings: Findings | None = None
    breaks: frozenset[ApiBreak] | None = None


_TESTED: Final[frozenset[MissKind]] = frozenset({"lines", "branches", "mutants"})


def guards_tests(baseline: Mapping[str, Measure]) -> bool:
    """Tell whether a control of the green counts its tests or what they leave untested.

    Without one `refactor_regressions` compares nothing of the tests, and the refactor's prompt
    mustn't say that fewer of them discards it (ADR-0020).
    """
    return any(
        m.passed is not None or any(kind in _TESTED for kind, _ in m.misses or ())
        for m in baseline.values()
    )


def unit_tests_rule(guarded: bool) -> str:
    """The refactor's rule on the unit tests, as far as the verification guards them."""
    if not guarded:
        return "- Puedes reorganizar los tests unitarios para que sigan al código."
    return (
        "- Puedes reorganizar los tests unitarios para que sigan al código, pero no quites ninguno "
        "que pase\n  ni dejes más código sin probar: un refactor que pase menos tests, o deje más "
        "sin probar que el\n  verde, se descarta. Quitar código que ya estaba probado no cuenta en "
        "contra."
    )


def discards_text(guarded: bool) -> str:
    """The end of the sentence on what discards a refactor."""
    if not guarded:
        return " o salga de lo que cambió el escenario"
    return (
        ", salga de lo que cambió el escenario o deje más sin probar que el verde; quitar código\n"
        "probado no cuenta en contra"
    )


def complexity_misses(over: OverLimit) -> tuple[tuple[MissKind, int], ...]:
    """What a complexity report leaves above its limit: two counts a refactor may not raise."""
    return (("excess", over.excess), ("peak", over.peak))


def duplication_misses(duplication: Duplication) -> tuple[tuple[MissKind, int], ...]:
    """What a duplication report leaves repeated: one count a refactor may not raise."""
    return (("duplicated", duplication.in_clones),)


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
    if isinstance(judged.report, ComplexityReport):
        return _complexity_measure(entry.control, judged.report)
    if isinstance(judged.report, DuplicationReport):
        duplication = duplication_of(judged.report)
        return Measure(misses=duplication_misses(duplication), findings=duplication)
    if isinstance(judged.report, ApiComparison):
        # A base that couldn't be measured compares nothing: there is no set to keep.
        return Measure(breaks=judged.report.breaks) if judged.report.problem is None else None
    return _judged_measure(judged)


def _judged_measure(judged: Judged) -> Measure | None:
    """The cases a JUnit report passed and what a score left untested, if either is there."""
    passed = (
        sum(1 for case in judged.cases if case.status == "passed")
        if isinstance(judged.report, JUnitReport)
        else None
    )
    misses = score_misses(judged.score) if judged.score is not None else None
    if passed is None and misses is None:
        return None
    return Measure(passed=passed, misses=misses)


def _complexity_measure(
    control: CommandControl | ReaderControl, report: ComplexityReport
) -> Measure | None:
    """What a complexity report leaves above its control's limit, and which functions."""
    if not isinstance(control, CommandControl) or control.limit is None:
        return None
    over = over_limit(report, control.limit)
    return Measure(misses=complexity_misses(over), findings=over)


def measures_record(measures: Mapping[str, Measure]) -> dict[str, dict[str, object]]:
    """The measures as the checkpoint keeps them."""
    return {
        cid: {
            "passed": m.passed,
            "misses": None if m.misses is None else dict(m.misses),
            **({} if m.findings is None else {"findings": _findings_record(m.findings)}),
            **({} if m.breaks is None else {"api_breaks": _breaks_record(m.breaks)}),
        }
        for cid, m in measures.items()
    }


def _breaks_record(breaks: frozenset[ApiBreak]) -> list[dict[str, object]]:
    return [break_record(b) for b in sorted(breaks, key=lambda b: (b.name, b.change, b.param))]


def _breaks_or_none(value: object) -> frozenset[ApiBreak] | None:
    found = breaks_from_record(value)
    return None if found is None else frozenset(found)


def _findings_record(findings: Findings) -> dict[str, object]:
    if isinstance(findings, Duplication):
        return _duplication_record(findings)
    return {
        "kind": "complexity",
        "limit": findings.limit,
        "functions": [
            {
                "file": f.file,
                "function": f.function,
                "line": f.line,
                "last_line": f.last_line,
                "complexity": f.complexity,
            }
            for f in findings.functions
        ],
    }


def _duplication_record(duplication: Duplication) -> dict[str, object]:
    return {
        "kind": "duplication",
        "min_tokens": duplication.min_tokens,
        "in_clones": duplication.in_clones,
        "clones": [
            [[o.file, o.first_line, o.last_line, o.start, o.end] for o in clone.occurrences]
            for clone in duplication.clones
        ],
    }


def measures_from_record(record: Mapping[str, Mapping[str, object]]) -> dict[str, Measure]:
    """Read back what `measures_record` wrote; a field of another type counts as absent."""
    return {
        cid: Measure(
            passed=_int_or_none(fields.get("passed")),
            misses=_misses_or_none(fields),
            findings=_findings_or_none(fields.get("findings")),
            breaks=_breaks_or_none(fields.get("api_breaks")),
        )
        for cid, fields in record.items()
    }


def _findings_or_none(value: object) -> Findings | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("kind") == "duplication":
        return _duplication_or_none(value)
    if value.get("kind") == "complexity":
        return _over_or_none(value)
    return None


def _duplication_or_none(value: Mapping[str, object]) -> Duplication | None:
    min_tokens, in_clones = (
        _int_or_none(value.get("min_tokens")),
        _int_or_none(value.get("in_clones")),
    )
    raw = value.get("clones")
    if min_tokens is None or in_clones is None or not isinstance(raw, list):
        return None
    clones: list[Clone] = []
    for item in raw:
        occurrences = _occurrences_or_none(item)
        if occurrences is None:
            return None
        clones.append(Clone(occurrences))
    return Duplication(min_tokens, in_clones, tuple(clones))


def _occurrences_or_none(item: object) -> tuple[CloneOccurrence, ...] | None:
    if not isinstance(item, list) or len(item) < _PLACES:
        return None
    found = [_place_or_none(place) for place in item]
    if any(place is None for place in found):
        return None
    return tuple(place for place in found if place is not None)


def _place_or_none(place: object) -> CloneOccurrence | None:
    if not isinstance(place, list) or len(place) != _PLACE_FIELDS:
        return None
    file, *numbers = place
    values = [_int_or_none(n) for n in numbers]
    if not isinstance(file, str) or any(v is None for v in values):
        return None
    first, last, start, end = (v for v in values if v is not None)
    return CloneOccurrence(file, first, last, start, end)


_PLACE_FIELDS: Final[int] = 5  # file, first line, last line, start, end
_PLACES: Final[int] = 2  # a clone has two places at least


def _over_or_none(value: Mapping[str, object]) -> OverLimit | None:
    limit, raw = _int_or_none(value.get("limit")), value.get("functions")
    if limit is None or not isinstance(raw, list):
        return None
    functions: list[FunctionComplexity] = []
    for item in raw:
        if not isinstance(item, Mapping):
            return None
        file, name = item.get("file"), item.get("function")
        line, complexity = _int_or_none(item.get("line")), _int_or_none(item.get("complexity"))
        last = _int_or_none(item.get("last_line"))
        if not isinstance(file, str) or not isinstance(name, str):
            return None
        if line is None or complexity is None or last is None:
            return None
        functions.append(FunctionComplexity(file, name, line, complexity, last))
    return OverLimit(limit, tuple(functions))


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
        if before.breaks is not None:
            found.extend(_more_broken(cid, before.breaks, after.breaks))
    return found


def _more_broken(
    cid: str, before: frozenset[ApiBreak], after: frozenset[ApiBreak] | None
) -> list[str]:
    """What the refactor breaks of the public API that its green didn't (ADR-0017).

    Sets, not a count: a refactor that mends one break and makes another is caught.
    """
    if after is None:
        return [f"{cid}: sin medida, y en el verde la tenía"]
    new = sorted(after - before, key=lambda b: (b.name, b.change, b.param))
    return [f"{cid}: rompe {break_line(b)}, y el verde no" for b in new]


def _more_untested(
    cid: str,
    before: tuple[tuple[MissKind, int], ...],
    after: tuple[tuple[MissKind, int], ...] | None,
) -> list[str]:
    if after is None:
        return [f"{cid}: sin medida, y en el verde la tenía"]
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
    """The reason of a refactor that measures worse than its green."""
    return "El refactor deja peor que el verde lo que miden los controles:\n" + "\n".join(
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
