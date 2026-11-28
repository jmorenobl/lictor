"""What a fresh driver's red check saw, scenario by scenario, for the journal and the report.

The red, the stubs' impostors and the interface's report in which kind of Gherkin step each test
failed, and the interface's how many calls each step made and how many it altered. These functions
keep those facts, with what each rule decided of them: C for the stubs (`stub_proof`, ADR-0012)
and the proof of the interface. They decide nothing themselves.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import Literal, TypeGuard, get_args

from lictor.domain.basic_stub_proof import (
    BasicOutcome,
    BasicVerdict,
    VariantFact,
    basic_verdict,
)
from lictor.domain.gherkin_proof import GherkinFacts
from lictor.domain.interface_proof import ALTERED_STEPS, InterfaceProof, ProofOutcome, provoked_in
from lictor.domain.model import (
    STEPS,
    InterfaceCount,
    Step,
    StepCounts,
    TestCaseResult,
    UnreadValue,
    statements_from_text,
    statements_text,
)
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
    # The case's status in the JUnit report, which is all a profile without the classes of each
    # failure can tell (ADR-0020): "failure", "error", "passed", or "" without a case.
    status: str = ""


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


VariantWord = Literal["pasa", "falla", "marca", "no_cuenta"]


@dataclass(frozen=True)
class DeclaredMeasure:
    """How a pending scenario's test fared with each variant of the driver's own constants, and
    what the basic rule made of it (ADR-0021)."""

    by_variant: tuple[VariantWord, ...]
    outcome: BasicOutcome

    @property
    def verdict(self) -> BasicVerdict:
        return basic_verdict(self.outcome)


def variant_word(fact: VariantFact | None) -> VariantWord:
    """One variant's fact, as the journal and the report say it."""
    if fact is None:
        return "no_cuenta"
    if fact.passed:
        return "pasa"
    return "marca" if fact.marked else "falla"


@dataclass(frozen=True)
class InterfaceMeasure:
    """What the impostor of the interface proved of one scenario, and what it saw step by step."""

    outcome: ProofOutcome
    calls: StepCounts  # in the control
    altered: Mapping[str, StepCounts]  # by mode
    failed_step: Mapping[str, Step | None]  # by mode
    # In the control: the calls a library made for the test, never altered, and which libraries
    # it reached the interface through from the When on.
    via: StepCounts = field(default_factory=StepCounts)
    handed: StepCounts = field(default_factory=StepCounts)
    libraries: tuple[str, ...] = ()
    # What the interface alone said, before the scenario's values weighed it (ADR-0016), the
    # modes in which an altered value made the code raise, and what the Gherkin's impostor saw.
    interface: ProofOutcome | None = None
    provoked: tuple[str, ...] = ()
    gherkin: GherkinFacts | None = None


@dataclass(frozen=True)
class DriverMeasures:
    """Every measure of one red check of a fresh driver; None where its impostor didn't run."""

    red: Mapping[str, RedMeasure]
    stubs: Mapping[str, StubMeasure] | None = None
    interface: Mapping[str, InterfaceMeasure] | None = None
    declared: Mapping[str, DeclaredMeasure] | None = None


def red_measures(
    pending: Sequence[str], mapped: Mapping[str, TestCaseResult], driver: Sequence[str]
) -> dict[str, RedMeasure]:
    """How each pending scenario's test failed at the red."""
    return {
        sid: RedMeasure(
            mapped[sid].failed_step if sid in mapped else None,
            caught(mapped.get(sid), driver),
            mapped[sid].status if sid in mapped else "",
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
    unweighed: Mapping[str, InterfaceProof] | None = None,
    gherkin: Mapping[str, GherkinFacts] | None = None,
) -> dict[str, InterfaceMeasure]:
    """What the impostor of the interface proved of each scenario it judged, and what it saw;
    with the Gherkin's, `unweighed` is what the interface alone said."""
    alone, seen = unweighed or {}, gherkin or {}
    measures: dict[str, InterfaceMeasure] = {}
    for sid, proof in proofs.items():
        cases = {mode: run.get(sid) for mode, run in modes.items()}
        own = alone.get(sid)
        measures[sid] = replace(
            _seen_by_the_interface(proof, control.get(sid), cases),
            interface=own.outcome if own is not None else None,
            gherkin=seen.get(sid),
        )
    return measures


def _seen_by_the_interface(
    proof: InterfaceProof,
    case: TestCaseResult | None,
    cases: Mapping[str, TestCaseResult | None],
) -> InterfaceMeasure:
    """What the control (`case`) and each mode's run (`cases`) told of one test."""
    return InterfaceMeasure(
        outcome=proof.outcome,
        calls=_by_step(case, "calls"),
        altered={mode: _by_step(run, "altered") for mode, run in cases.items()},
        failed_step={mode: run.failed_step if run else None for mode, run in cases.items()},
        via=_by_step(case, "via"),
        handed=_by_step(case, "handed"),
        libraries=case.interface_entries(ALTERED_STEPS) if case is not None else (),
        provoked=tuple(
            mode for mode, run in cases.items() if case and provoked_in(mode, run, case)
        ),
    )


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
            sid: {"step": m.step, "caught": m.caught, "status": m.status}
            for sid, m in sorted(measures.red.items())
        },
        "stubs": _section(measures.stubs, _stub_record),
        "interface": _section(measures.interface, _interface_record),
        "declared": _section(measures.declared, _declared_record),
    }


def _section[M](
    measured: Mapping[str, M] | None, record: Callable[[M], dict[str, object]]
) -> dict[str, dict[str, object]] | None:
    """One impostor's measures by scenario, in order; None where it didn't run."""
    if measured is None:
        return None
    return {sid: record(m) for sid, m in sorted(measured.items())}


def _stub_record(m: StubMeasure) -> dict[str, object]:
    return {
        "by_constant": dict(m.by_constant),
        "outcome": m.outcome,
        "interface": m.interface,
        "verdict": m.verdict,
    }


def _declared_record(m: DeclaredMeasure) -> dict[str, object]:
    return {"by_variant": list(m.by_variant), "outcome": m.outcome, "verdict": m.verdict}


def _interface_record(m: InterfaceMeasure) -> dict[str, object]:
    return {
        "outcome": m.outcome,
        "calls": _counts_record(m.calls),
        "altered": {mode: _counts_record(c) for mode, c in m.altered.items()},
        "failed_step": dict(m.failed_step),
        "via": _counts_record(m.via),
        "handed": _counts_record(m.handed),
        "libraries": list(m.libraries),
        "interface": m.interface,
        "provoked": list(m.provoked),
        "gherkin": None if m.gherkin is None else _gherkin_record(m.gherkin),
    }


def _gherkin_record(facts: GherkinFacts) -> dict[str, object]:
    return {
        "values": facts.values,
        "unread": [[u.step, u.parameter, u.value] for u in facts.unread],
        "unreceived": facts.unreceived,
        "read": list(facts.read),
        "inputs": list(facts.inputs),
        "survived": list(facts.survived),
        "errors": list(facts.errors),
        "place": facts.place,
        "read_at": [statements_text(chain) for chain in facts.read_at],
    }


def measures_from_record(raw: object) -> DriverMeasures | None:
    """Read back what `measures_record` wrote, or None if it doesn't follow its schema."""
    if not isinstance(raw, dict):
        return None
    red = _each(raw.get("red"), _red_from)
    stubs = _optional(raw.get("stubs"), _stub_from)
    interface = _optional(raw.get("interface"), _interface_from)
    declared = _optional(raw.get("declared"), _declared_from)
    if red is None or stubs is False or interface is False or declared is False:
        return None
    return DriverMeasures(red, stubs, interface, declared)


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
    status = raw.get("status", "")
    if found is None or not isinstance(status, str):
        return None
    return RedMeasure(_step(raw.get("step")), found, status)


def _stub_from(raw: Mapping[str, object]) -> StubMeasure | None:
    by_constant: dict[str, Caught] | None = _by_key(raw.get("by_constant"), _caught)
    outcome = _one_of(raw.get("outcome"), _STUB_OUTCOMES)
    interface = raw.get("interface")
    proof = None if interface is None else _one_of(interface, _PROOF_OUTCOMES)
    if by_constant is None or outcome is None or (interface is not None and proof is None):
        return None
    return StubMeasure(by_constant, outcome, proof)


_VARIANT_WORDS: tuple[VariantWord, ...] = get_args(VariantWord)
_BASIC_OUTCOMES: tuple[BasicOutcome, ...] = get_args(BasicOutcome)


def _declared_from(raw: Mapping[str, object]) -> DeclaredMeasure | None:
    words = raw.get("by_variant")
    outcome = _one_of(raw.get("outcome"), _BASIC_OUTCOMES)
    if not isinstance(words, list) or outcome is None:
        return None
    read = [_one_of(word, _VARIANT_WORDS) for word in words]
    if any(word is None for word in read):
        return None
    return DeclaredMeasure(tuple(w for w in read if w is not None), outcome)


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
    through = _through_from(raw)
    weighed = _weighed_from(raw)
    if outcome is None or calls is None or altered is None or steps is None or through is None:
        return None
    if weighed is None:
        return None
    failed = {mode: step for mode, (step,) in steps.items()}
    return InterfaceMeasure(outcome, calls, altered, failed, *through, *weighed)


def _weighed_from(
    raw: Mapping[str, object],
) -> tuple[ProofOutcome | None, tuple[str, ...], GherkinFacts | None] | None:
    """What weighed the interface's verdict: its own outcome, the provoked modes, the Gherkin."""
    alone, provoked, gherkin = raw.get("interface"), raw.get("provoked"), raw.get("gherkin")
    outcome = None if alone is None else _one_of(alone, _PROOF_OUTCOMES)
    facts = None if gherkin is None else _gherkin_from(gherkin)
    if (alone is not None and outcome is None) or not _names(provoked):
        return None
    if gherkin is not None and facts is None:
        return None
    return outcome, tuple(provoked), facts


_GHERKIN_COUNTS = ("values", "unreceived")
_GHERKIN_NUMBERS = ("read", "inputs", "survived", "errors")


def _gherkin_from(raw: object) -> GherkinFacts | None:
    if not isinstance(raw, dict):
        return None
    counts = _every(raw, _GHERKIN_COUNTS, _count)
    numbers = _every(raw, _GHERKIN_NUMBERS, _numbers)
    unread, place = _unread_from(raw.get("unread")), raw.get("place")
    read_at = raw.get("read_at", [])
    if counts is None or numbers is None or unread is None or not isinstance(place, str):
        return None
    if not _names(read_at):
        return None
    values, unreceived = counts
    read, inputs, survived, errors = (tuple(n) for n in numbers)
    chains = tuple(statements_from_text(chain) for chain in read_at)
    return GherkinFacts(values, unread, unreceived, read, inputs, survived, errors, place, chains)


def _every[T](
    raw: Mapping[str, object], names: Sequence[str], check: Callable[[object], TypeGuard[T]]
) -> list[T] | None:
    """The fields `names` of `raw`, in order, if each passes `check`; else None."""
    found = [raw.get(name) for name in names]
    passed = [value for value in found if check(value)]
    return passed if len(passed) == len(found) else None


def _unread_from(raw: object) -> tuple[UnreadValue, ...] | None:
    if not isinstance(raw, list):
        return None
    if not all(_names(u) and len(u) == len(fields(UnreadValue)) for u in raw):
        return None
    return tuple(UnreadValue(*u) for u in raw)


def _count(raw: object) -> TypeGuard[int]:
    return isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0


def _numbers(raw: object) -> TypeGuard[list[int]]:
    return isinstance(raw, list) and all(_count(n) for n in raw)


def _through_from(
    raw: Mapping[str, object],
) -> tuple[StepCounts, StepCounts, tuple[str, ...]] | None:
    """What reached the interface through a library in the control: `via`, `handed`, which."""
    via = _counts_from(raw.get("via"))
    handed = _counts_from(raw.get("handed"))
    libraries = raw.get("libraries")
    if via is None or handed is None or not _names(libraries):
        return None
    return via, handed, tuple(libraries)


def _names(raw: object) -> TypeGuard[list[str]]:
    return isinstance(raw, list) and all(isinstance(name, str) for name in raw)


# The phases of a red check, as `report.md` and the journal name them, in the order they run.
RED_PHASES = ("red", "stubs", "interface", "gherkin", "controls")


def red_check_phase(run_dir_name: str, step_dir_name: str) -> str:
    """Tell which phase of the red check a run of the acceptance belongs to, by its evidence.

    The red itself writes in its step's own directory; every other run has one inside it:
    `impostor-<n>` for the constants that replace the stubs, `interfaz-<mode>` and
    `stubs-interfaz-<mode>` for the interface's impostor, and the Gherkin's ones (ADR-0016).
    """
    if run_dir_name == step_dir_name:
        return "red"
    if "gherkin" in run_dir_name:
        return "gherkin"
    if "interfaz" in run_dir_name:
        return "interface"
    return "stubs"


def red_check_timing(runs: Sequence[tuple[str, int]], duration_ms: int) -> dict[str, object]:
    """Record how long a red check took, whole and by phase, with how many runs each had (F52)."""
    phases: dict[str, dict[str, int]] = {}
    for phase, took in runs:
        entry = phases.setdefault(phase, {"runs": 0, "duration_ms": 0})
        entry["runs"] += 1
        entry["duration_ms"] += took
    ordered = {phase: phases[phase] for phase in RED_PHASES if phase in phases}
    return {"duration_ms": duration_ms, "phases": ordered}
