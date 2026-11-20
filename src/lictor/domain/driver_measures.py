"""What a fresh driver's red check saw, scenario by scenario, for the journal and the report.

The red, the stubs' impostors and the interface's report in which kind of Gherkin step each test
failed, and the interface's how many calls each step made and how many it altered. These functions
keep those facts, with what each rule decided of them: C for the stubs (`stub_proof`, ADR-0012)
and the proof of the interface. They decide nothing themselves.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from lictor.domain.interface_proof import InterfaceProof, ProofOutcome
from lictor.domain.model import STEPS, InterfaceCount, Step, StepCounts, TestCaseResult
from lictor.domain.stub_proof import (
    Caught,
    StubOutcome,
    StubProof,
    StubVerdict,
    caught,
    stub_verdict,
)


@dataclass(frozen=True)
class RedMeasure:
    """How a pending scenario's test failed at the red."""

    step: Step | None
    caught: Caught


@dataclass(frozen=True)
class StubMeasure:
    """How a pending scenario's test fared with each constant in place of the stubs, what C made
    of it and, if it went there, what the interface proved of it."""

    by_constant: Mapping[str, Caught]
    outcome: StubOutcome
    interface: ProofOutcome | None = None

    @property
    def verdict(self) -> StubVerdict:
        proof = None if self.interface is None else InterfaceProof(self.interface)
        return stub_verdict(StubProof(self.outcome), proof)


@dataclass(frozen=True)
class InterfaceMeasure:
    """What the impostor of the interface proved of one scenario, and what it saw step by step."""

    outcome: ProofOutcome
    calls: StepCounts  # in the control
    altered: Mapping[str, StepCounts]  # by mode
    failed_step: Mapping[str, Step | None]  # by mode


@dataclass(frozen=True)
class DriverMeasures:
    """Every measure of one red check of a fresh driver; None where its impostor didn't run."""

    red: Mapping[str, RedMeasure]
    stubs: Mapping[str, StubMeasure] | None = None
    interface: Mapping[str, InterfaceMeasure] | None = None


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
    proofs: Mapping[str, StubProof],
    interface: Mapping[str, InterfaceProof],
) -> dict[str, StubMeasure]:
    """How each pending scenario's test fared with each constant (`runs`: name, results), with
    what C and the interface decided of it."""
    return {
        sid: StubMeasure(
            {name: caught(cases.get(sid), driver) for name, cases in runs},
            proofs[sid].outcome,
            interface[sid].outcome if sid in interface else None,
        )
        for sid in pending
        if sid in proofs
    }


def interface_measures(
    proofs: Mapping[str, InterfaceProof],
    control: Mapping[str, TestCaseResult],
    modes: Mapping[str, Mapping[str, TestCaseResult]],
) -> dict[str, InterfaceMeasure]:
    """What the impostor of the interface proved of each scenario it judged, and what it saw."""
    measures: dict[str, InterfaceMeasure] = {}
    for sid, proof in proofs.items():
        cases = {mode: run.get(sid) for mode, run in modes.items()}
        measures[sid] = InterfaceMeasure(
            outcome=proof.outcome,
            calls=_by_step(control.get(sid), "calls"),
            altered={mode: _by_step(case, "altered") for mode, case in cases.items()},
            failed_step={
                mode: case.failed_step if case is not None else None for mode, case in cases.items()
            },
        )
    return measures


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
            sid: {
                "by_constant": dict(m.by_constant),
                "outcome": m.outcome,
                "interface": m.interface,
                "verdict": m.verdict,
            }
            for sid, m in sorted(measures.stubs.items())
        },
        "interface": None
        if measures.interface is None
        else {sid: _interface_record(m) for sid, m in sorted(measures.interface.items())},
    }


def _interface_record(m: InterfaceMeasure) -> dict[str, object]:
    return {
        "outcome": m.outcome,
        "calls": _counts_record(m.calls),
        "altered": {mode: _counts_record(c) for mode, c in m.altered.items()},
        "failed_step": dict(m.failed_step),
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
_PROOF_OUTCOMES: tuple[ProofOutcome, ...] = get_args(ProofOutcome)
_STUB_OUTCOMES: tuple[StubOutcome, ...] = get_args(StubOutcome)


def _one_of[T](value: object, values: tuple[T, ...]) -> T | None:
    """`value` as one of `values`, or None if it is none of them."""
    return next((known for known in values if known == value), None)


def _caught(value: object) -> Caught | None:
    return _one_of(value, _CAUGHT)


def _step(value: object) -> Step | None:
    return _one_of(value, STEPS)


def _red_from(raw: Mapping[str, object]) -> RedMeasure | None:
    found = _caught(raw.get("caught"))
    return None if found is None else RedMeasure(_step(raw.get("step")), found)


def _stub_from(raw: Mapping[str, object]) -> StubMeasure | None:
    by_constant: dict[str, Caught] | None = _by_key(raw.get("by_constant"), _caught)
    outcome = _one_of(raw.get("outcome"), _STUB_OUTCOMES)
    interface = raw.get("interface")
    proof = None if interface is None else _one_of(interface, _PROOF_OUTCOMES)
    if by_constant is None or outcome is None or (interface is not None and proof is None):
        return None
    return StubMeasure(by_constant, outcome, proof)


def _counts_from(raw: object) -> StepCounts | None:
    if not isinstance(raw, dict):
        return None
    values = [raw.get(name) for name in ("given", "when", "then", "outside")]
    counts = [v for v in values if isinstance(v, int) and not isinstance(v, bool) and v >= 0]
    return StepCounts(*counts) if len(counts) == len(values) else None


def _interface_from(raw: Mapping[str, object]) -> InterfaceMeasure | None:
    outcome = _one_of(raw.get("outcome"), _PROOF_OUTCOMES)
    calls = _counts_from(raw.get("calls"))
    altered = _by_key(raw.get("altered"), _counts_from)
    steps = _by_key(
        raw.get("failed_step"), lambda v: (_step(v),) if v is None or _step(v) else None
    )
    if outcome is None or calls is None or altered is None or steps is None:
        return None
    failed = {mode: step for mode, (step,) in steps.items()}
    return InterfaceMeasure(outcome, calls, altered, failed)
