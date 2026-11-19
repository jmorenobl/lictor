"""What the rules the impostors don't apply yet would say of a fresh driver: measures, no verdict.

The red, the stubs' impostors and the interface's report in which kind of Gherkin step each test
failed, and the interface's how many calls each step made and how many it altered. These functions
read those facts and say, for each scenario, what a stricter rule would make of it: B for the
interface (only a Then's assertion, or the impostor's error from the When on, proves it), C for the
stubs (a Then's assertion of the driver must catch a constant). They decide nothing: what the run
does is still `judge_red`, `impostor_survivors` and `interface_proofs`.

B also stops altering what the interface returns to a Given, which changes the run itself. Over the
runs of today a test caught in a Given can't say what B would make of it, and is counted apart.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from lictor.domain.interface_proof import (
    ASSERTION_KIND,
    IMPOSTOR_KIND,
    InterfaceProof,
    ProofOutcome,
)
from lictor.domain.model import STEPS, InterfaceCount, Step, StepCounts, TestCaseResult
from lictor.domain.rules import NOT_IMPLEMENTED, path_within

Caught = Literal[
    "entonces",  # an assertion of the driver, in a Then
    "otro_paso",  # an assertion of the driver, in another step or outside one
    "no_aserta",  # it failed, but not by an assertion of the driver
    "sin_implementar",  # a stub was still there
    "pasa",  # it passed
    "sin_resultado",  # the run has no result for it
]

ModeCaught = Literal[
    "entonces",  # what B accepts: a Then's assertion, or the impostor's error from the When on
    "dado",  # it failed in a Given: B wouldn't have altered what made it fail
    "otro",  # it failed some other way B doesn't accept
    "pasa",
    "sin_resultado",
]

RuleB = Literal[
    "probado",  # proved today, and every mode caught it as B asks: at most what B would prove
    "fuera_del_entonces",  # proved today, but some mode caught it where B doesn't look
    "sin_alterar",  # nothing the interface returns from the When on can be altered
    "no_probado",  # not proved today
]

RuleC = Literal[
    "entonces",  # some constant made a Then's assertion of the driver fail
    "otro_paso",  # some constant made an assertion of the driver fail, never in a Then
    "no_aserta",  # it failed with some constant, never by an assertion of the driver
    "pasa",  # it passed with every constant
]


@dataclass(frozen=True)
class RedMeasure:
    """How a pending scenario's test failed at the red."""

    step: Step | None
    caught: Caught


@dataclass(frozen=True)
class StubMeasure:
    """How a pending scenario's test fared with each constant in place of the stubs."""

    by_constant: Mapping[str, Caught]

    @property
    def rule_c(self) -> RuleC:
        found = set(self.by_constant.values())
        if "entonces" in found:
            return "entonces"
        if "otro_paso" in found:
            return "otro_paso"
        failed = found - {"pasa", "sin_resultado"}
        return "no_aserta" if failed else "pasa"


@dataclass(frozen=True)
class InterfaceMeasure:
    """What the impostor of the interface saw of one scenario, step by step, and what B says."""

    today: ProofOutcome
    rule_b: RuleB
    calls: StepCounts  # in the control
    alterable: StepCounts  # in the control
    altered: Mapping[str, StepCounts]  # by mode
    failed_step: Mapping[str, Step | None]  # by mode
    caught: Mapping[str, ModeCaught]  # by mode


@dataclass(frozen=True)
class DriverMeasures:
    """Every measure of one red check of a fresh driver; None where its impostor didn't run."""

    red: Mapping[str, RedMeasure]
    stubs: Mapping[str, StubMeasure] | None = None
    interface: Mapping[str, InterfaceMeasure] | None = None


def caught(case: TestCaseResult | None, driver: Sequence[str]) -> Caught:
    """How a test ended, as the stricter rules of the red and the stubs read it."""
    if case is None or case.status == "skipped":
        return "sin_resultado"
    if case.status == "passed":
        return "pasa"
    kind = case.failure_kind
    if kind == NOT_IMPLEMENTED:
        return "sin_implementar"
    if kind == ASSERTION_KIND and path_within(case.frame, driver):
        return "entonces" if case.failed_step == "then" else "otro_paso"
    return "no_aserta"


def mode_caught(case: TestCaseResult | None, driver: Sequence[str]) -> ModeCaught:
    """How one mode of the impostor caught a test, as B reads it."""
    if case is None or case.status == "skipped":
        return "sin_resultado"
    if case.status == "passed":
        return "pasa"
    step, kind = case.failed_step, case.failure_kind
    if step == "given":
        return "dado"
    own = kind == ASSERTION_KIND and path_within(case.frame, driver)
    if (own and step == "then") or (kind == IMPOSTOR_KIND and step in ("when", "then")):
        return "entonces"
    return "otro"


def red_measures(
    pending: Sequence[str], mapped: Mapping[str, TestCaseResult], driver: Sequence[str]
) -> dict[str, RedMeasure]:
    """How each pending scenario's test failed at the red."""
    return {
        sid: RedMeasure(
            mapped[sid].failed_step if sid in mapped else None, caught(mapped.get(sid), driver)
        )
        for sid in pending
    }


def stub_measures(
    pending: Sequence[str],
    runs: Sequence[tuple[str, Mapping[str, TestCaseResult]]],
    driver: Sequence[str],
) -> dict[str, StubMeasure]:
    """How each pending scenario's test fared with each constant (`runs`: constant, results)."""
    return {
        sid: StubMeasure({constant: caught(cases.get(sid), driver) for constant, cases in runs})
        for sid in pending
    }


def interface_measures(
    proofs: Mapping[str, InterfaceProof],
    control: Mapping[str, TestCaseResult],
    modes: Mapping[str, Mapping[str, TestCaseResult]],
    driver: Sequence[str],
) -> dict[str, InterfaceMeasure]:
    """What the impostor of the interface saw of each scenario it judged, and what B says."""
    measures: dict[str, InterfaceMeasure] = {}
    for sid, proof in proofs.items():
        ctrl = control.get(sid)
        cases = {mode: run.get(sid) for mode, run in modes.items()}
        caught_by = {mode: mode_caught(case, driver) for mode, case in cases.items()}
        measures[sid] = InterfaceMeasure(
            today=proof.outcome,
            rule_b=rule_b(proof, ctrl, caught_by),
            calls=_by_step(ctrl, "calls"),
            alterable=_by_step(ctrl, "alterable"),
            altered={mode: _by_step(case, "altered") for mode, case in cases.items()},
            failed_step={
                mode: case.failed_step if case is not None else None for mode, case in cases.items()
            },
            caught=caught_by,
        )
    return measures


def rule_b(
    today: InterfaceProof, control: TestCaseResult | None, caught_by: Mapping[str, ModeCaught]
) -> RuleB:
    """What B would make of a scenario, as far as the runs of today can tell."""
    called = control is not None and control.status == "passed" and control.interface_calls > 0
    if called and control is not None and control.interface_by_step("alterable").from_when == 0:
        return "sin_alterar"
    if not today.proved:
        return "no_probado"
    if caught_by and all(c == "entonces" for c in caught_by.values()):
        return "probado"
    return "fuera_del_entonces"


def _by_step(case: TestCaseResult | None, count: InterfaceCount) -> StepCounts:
    return case.interface_by_step(count) if case is not None else StepCounts()


# The journal's record: what `measures_record` writes and `measures_from_record` reads back.


def _counts_record(counts: StepCounts) -> dict[str, int]:
    return {
        "given": counts.given,
        "when": counts.when,
        "then": counts.then,
        "outside": counts.outside,
    }


def measures_record(measures: DriverMeasures) -> dict[str, object]:
    """The measures as the journal keeps them."""
    return {
        "red": {
            sid: {"step": m.step, "caught": m.caught} for sid, m in sorted(measures.red.items())
        },
        "stubs": None
        if measures.stubs is None
        else {
            sid: {"by_constant": dict(m.by_constant), "rule_c": m.rule_c}
            for sid, m in sorted(measures.stubs.items())
        },
        "interface": None
        if measures.interface is None
        else {sid: _interface_record(m) for sid, m in sorted(measures.interface.items())},
    }


def _interface_record(m: InterfaceMeasure) -> dict[str, object]:
    return {
        "today": m.today,
        "rule_b": m.rule_b,
        "calls": _counts_record(m.calls),
        "alterable": _counts_record(m.alterable),
        "altered": {mode: _counts_record(c) for mode, c in m.altered.items()},
        "failed_step": dict(m.failed_step),
        "caught": dict(m.caught),
    }


def measures_from_record(raw: object) -> DriverMeasures | None:
    """Read back what `measures_record` wrote, or None if it doesn't follow its schema."""
    if not isinstance(raw, dict):
        return None
    red = _each(raw.get("red"), _red_from)
    stubs = _optional(raw.get("stubs"), _stub_from)
    interface = _optional(raw.get("interface"), _interface_from)
    if red is None or stubs is False or interface is False:
        return None
    return DriverMeasures(red, stubs, interface)


def _optional[T](
    raw: object, read: Callable[[Mapping[str, object]], T | None]
) -> dict[str, T] | Literal[False] | None:
    """A section that may be absent (None), or False if it is there and doesn't follow."""
    if raw is None:
        return None
    read_back = _each(raw, read)
    return False if read_back is None else read_back


def _each[T](raw: object, read: Callable[[Mapping[str, object]], T | None]) -> dict[str, T] | None:
    """Every entry read by `read`, or None if any of them doesn't follow the schema."""
    return _by_key(raw, lambda value: read(value) if isinstance(value, dict) else None)


def _by_key[T](raw: object, read: Callable[[object], T | None]) -> dict[str, T] | None:
    if not isinstance(raw, dict):
        return None
    read_back: dict[str, T] = {}
    for key, value in raw.items():
        entry = read(value)
        if not isinstance(key, str) or entry is None:
            return None
        read_back[key] = entry
    return read_back


_CAUGHT: tuple[Caught, ...] = get_args(Caught)
_MODE_CAUGHT: tuple[ModeCaught, ...] = get_args(ModeCaught)
_PROOF_OUTCOMES: tuple[ProofOutcome, ...] = get_args(ProofOutcome)
_RULE_B: tuple[RuleB, ...] = get_args(RuleB)


def _one_of[T](value: object, values: tuple[T, ...]) -> T | None:
    """`value` as one of `values`, or None if it is none of them."""
    return next((known for known in values if known == value), None)


def _caught(value: object) -> Caught | None:
    return _one_of(value, _CAUGHT)


def _mode_caught(value: object) -> ModeCaught | None:
    return _one_of(value, _MODE_CAUGHT)


def _step(value: object) -> Step | None:
    return _one_of(value, STEPS)


def _red_from(raw: Mapping[str, object]) -> RedMeasure | None:
    found = _caught(raw.get("caught"))
    return None if found is None else RedMeasure(_step(raw.get("step")), found)


def _stub_from(raw: Mapping[str, object]) -> StubMeasure | None:
    by_constant: dict[str, Caught] | None = _by_key(raw.get("by_constant"), _caught)
    return None if by_constant is None else StubMeasure(by_constant)


def _counts_from(raw: object) -> StepCounts | None:
    if not isinstance(raw, dict):
        return None
    values = [raw.get(name) for name in ("given", "when", "then", "outside")]
    counts = [v for v in values if isinstance(v, int) and not isinstance(v, bool) and v >= 0]
    return StepCounts(*counts) if len(counts) == len(values) else None


def _interface_from(raw: Mapping[str, object]) -> InterfaceMeasure | None:
    today, rule = _one_of(raw.get("today"), _PROOF_OUTCOMES), _one_of(raw.get("rule_b"), _RULE_B)
    calls, alterable = _counts_from(raw.get("calls")), _counts_from(raw.get("alterable"))
    altered = _by_key(raw.get("altered"), _counts_from)
    caught_by: dict[str, ModeCaught] | None = _by_key(raw.get("caught"), _mode_caught)
    steps = _by_key(
        raw.get("failed_step"), lambda v: (_step(v),) if v is None or _step(v) else None
    )
    if today is None or rule is None or calls is None or alterable is None:
        return None
    if altered is None or caught_by is None or steps is None:
        return None
    failed = {mode: step for mode, (step,) in steps.items()}
    return InterfaceMeasure(today, rule, calls, alterable, altered, failed, caught_by)
