"""Whether a Then compares the value its scenario gives it (ADR-0016).

The impostor of the interface proves a test reads what the code returns; this proves it reads the
value of the scenario. The profile's impostor of the Gherkin numbers, in each test, the values the
Then steps receive from the Gherkin, and says which the step's code never reads: a fact of its
code, from the control, that needs no run. For a test the interface couldn't prove, each value it
can change is changed alone, once per mode, the interface wrapped and tracing the driver's calls:
the value is read when the test then fails by an assertion of the driver in a Then in every mode,
and it is an input when the driver's calls to the interface change with it (the scenario's value
went into the code, not into the check). Two controls tell which arguments of a call change by
themselves (the time), and those don't count. These rules judge what the impostor reported; the
plugin decides nothing.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lictor.domain.model import TestCaseResult, UnreadValue
from lictor.domain.rules import path_within

ASSERTION_KIND = "assertion"
THEN: Literal["then"] = "then"

ValueOutcome = Literal[
    "cazado",  # the test failed by an assertion of the driver in a Then
    "pasa",  # it passed with the value changed
    "entrada",  # the changed value reached a call of the driver's to the interface
    "error",  # it failed otherwise
    "sin_cambiar",  # the run didn't change it
    "sin_resultado",  # the run told nothing of the test
]


@dataclass(frozen=True)
class GherkinFacts:
    """What the impostor of the Gherkin saw of one test's Then steps.

    `read`, `inputs`, `survived` and `errors` are numbers of values, and empty when no value was
    changed: the interface proved the test, or nothing could be changed.
    """

    values: int
    unread: tuple[UnreadValue, ...] = ()
    unreceived: int = 0
    read: tuple[int, ...] = ()
    inputs: tuple[int, ...] = ()
    survived: tuple[int, ...] = ()
    errors: tuple[int, ...] = ()
    place: str = ""  # where the test's assertion caught a changed value: `file:line`

    @property
    def every_value_read(self) -> bool:
        """Each value that isn't an input is read, and at least one is."""
        return bool(self.read) and not self.survived


def static_facts(control: TestCaseResult) -> GherkinFacts:
    """What the control alone says: how many values, and which the step never reads."""
    return GherkinFacts(control.gherkin_values, control.gherkin_unread, control.gherkin_unreceived)


def to_change(control: TestCaseResult) -> tuple[int, ...]:
    """The values of a test the impostor of the Gherkin can change, by number."""
    return control.gherkin_alterable


def gherkin_facts(
    control: TestCaseResult,
    again: TestCaseResult | None,
    runs: Mapping[tuple[str, int], TestCaseResult | None],
    modes: Sequence[str],
    driver: Sequence[str],
) -> GherkinFacts:
    """Judge each value of one test by the runs that changed it (`runs`: by mode and number).

    `again` is a second control, which tells what changes in the driver's calls by itself.
    """
    by_value = {
        number: [value_outcome(runs.get((mode, number)), control, again, driver) for mode in modes]
        for number in to_change(control)
    }
    read, inputs, survived, errors = _sorted_values(by_value)
    found = (runs.get((mode, number)) for number in read for mode in modes)
    place = next((_place(case) for case in found if case is not None), "")
    return GherkinFacts(
        control.gherkin_values,
        control.gherkin_unread,
        control.gherkin_unreceived,
        read,
        inputs,
        survived,
        errors,
        place,
    )


def _sorted_values(
    by_value: Mapping[int, Sequence[ValueOutcome]],
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    """The values read (caught in every mode), the inputs, those that survived a change and the
    rest, which failed otherwise or told nothing."""
    kinds: dict[str, list[int]] = {"read": [], "inputs": [], "survived": [], "errors": []}
    for number, outcomes in by_value.items():
        kinds[_kind_of_value(outcomes)].append(number)
    return (
        tuple(kinds["read"]),
        tuple(kinds["inputs"]),
        tuple(kinds["survived"]),
        tuple(kinds["errors"]),
    )


def _kind_of_value(outcomes: Sequence[ValueOutcome]) -> str:
    if "entrada" in outcomes:
        return "inputs"
    if "pasa" in outcomes:
        return "survived"
    return "read" if all(o == "cazado" for o in outcomes) else "errors"


def value_outcome(
    case: TestCaseResult | None,
    control: TestCaseResult,
    again: TestCaseResult | None,
    driver: Sequence[str],
) -> ValueOutcome:
    """What one run that changed a value of the Gherkin says of it."""
    if case is None or case.status == "skipped":
        return "sin_resultado"
    if not case.gherkin_altered:
        return "sin_cambiar"
    if reached_the_interface(case, control, again):
        return "entrada"
    if case.status == "passed":
        return "pasa"
    own = case.failure_kind == ASSERTION_KIND and path_within(case.frame, driver)
    return "cazado" if own and case.failed_step == THEN else "error"


def reached_the_interface(
    case: TestCaseResult, control: TestCaseResult, again: TestCaseResult | None
) -> bool:
    """Whether the driver's calls to the interface aren't the control's, in what doesn't change
    between two controls: then the changed value went into the code.

    A test that failed may have stopped before some calls, never made more of them.
    """
    mine, theirs = case.interface_trace, control.interface_trace
    other = again.interface_trace if again is not None else ()
    if len(mine) > len(theirs) or (case.status == "passed" and len(mine) != len(theirs)):
        return True
    return any(
        not _same_call(entry, theirs[i], other[i] if i < len(other) else None)
        for i, entry in enumerate(mine)
    )


def _same_call(mine: str, theirs: str, again: str | None) -> bool:
    """The same call as the control's, but in the arguments that differ between two controls."""
    (where, args), (their_where, their_args) = _call(mine), _call(theirs)
    if where != their_where or len(args) != len(their_args):
        return False
    masks = _call(again)[1] if again is not None else their_args
    if len(masks) != len(their_args):
        masks = their_args
    return all(a == b or b != c for a, b, c in zip(args, their_args, masks, strict=True))


def _call(entry: str) -> tuple[tuple[str, str], list[str]]:
    step, _, rest = entry.partition(":")
    name, _, args = rest.partition(":")
    return (step, name), args.split(".") if args else []


def _place(case: TestCaseResult) -> str:
    if not case.frame:
        return ""
    return f"{case.frame}:{case.frame_line}" if case.frame_line is not None else case.frame
