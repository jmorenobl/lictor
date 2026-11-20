"""What a fresh driver's red check saw, and what C would say of the stubs' runs."""

import pytest

from lictor.domain.driver_measures import (
    DriverMeasures,
    InterfaceMeasure,
    RedMeasure,
    StubMeasure,
    caught,
    interface_measures,
    measures_from_record,
    measures_record,
    red_measures,
    stub_measures,
)
from lictor.domain.interface_proof import InterfaceProof
from lictor.domain.model import StepCounts, TestCaseResult

DRIVER = ["tests/acceptance/"]
OWN = "tests/acceptance/test_x.py"


def _failed(kind: str, step: str = "then", frame: str = OWN) -> TestCaseResult:
    properties = {"lictor.failure": kind, "lictor.frame": frame, "lictor.step": step}
    return TestCaseResult("test_x", status="failure", properties=properties)


def _control(calls: dict[str, int]) -> TestCaseResult:
    properties = {"lictor.interface.calls": str(sum(calls.values()))}
    properties |= {f"lictor.interface.calls.{step}": str(n) for step, n in calls.items()}
    return TestCaseResult("test_x", properties=properties)


PASSED = TestCaseResult("test_x")


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (None, "sin_resultado"),
        (TestCaseResult("test_x", status="skipped"), "sin_resultado"),
        (PASSED, "pasa"),
        (_failed("not_implemented"), "sin_implementar"),
        (_failed("assertion"), "entonces"),
        (_failed("assertion", "when"), "otro_paso"),
        (_failed("assertion", ""), "otro_paso"),
        # An assertion raised outside the driver, as in a helper of the code, isn't the test's.
        (_failed("assertion", frame="src/x.py"), "no_aserta"),
        (_failed("error"), "no_aserta"),
        (TestCaseResult("test_x", status="failure"), "no_aserta"),
    ],
)
def test_how_the_red_and_the_stubs_read_a_test(case: TestCaseResult | None, expected: str) -> None:
    assert caught(case, DRIVER) == expected


def test_the_red_says_where_each_pending_test_failed() -> None:
    mapped = {"A-1": _failed("not_implemented", "when"), "A-2": _failed("assertion")}

    assert red_measures(["A-1", "A-2", "A-3"], mapped, DRIVER) == {
        "A-1": RedMeasure("when", "sin_implementar"),
        "A-2": RedMeasure("then", "entonces"),
        "A-3": RedMeasure(None, "sin_resultado"),
    }


def test_the_stubs_say_how_each_constant_fared() -> None:
    runs = [
        ("return None", {"A-1": _failed("assertion"), "A-2": _failed("error")}),
        ("return True", {"A-1": PASSED, "A-2": PASSED}),
    ]

    assert stub_measures(["A-1", "A-2"], runs, DRIVER) == {
        "A-1": StubMeasure({"return None": "entonces", "return True": "pasa"}),
        "A-2": StubMeasure({"return None": "no_aserta", "return True": "pasa"}),
    }


@pytest.mark.parametrize(
    ("by_constant", "expected"),
    [
        ({"a": "entonces", "b": "pasa"}, "entonces"),
        ({"a": "otro_paso", "b": "entonces"}, "entonces"),
        ({"a": "otro_paso", "b": "no_aserta"}, "otro_paso"),
        ({"a": "no_aserta", "b": "pasa"}, "no_aserta"),
        ({"a": "sin_implementar", "b": "pasa"}, "no_aserta"),
        ({"a": "pasa", "b": "pasa"}, "pasa"),
        ({"a": "pasa", "b": "sin_resultado"}, "pasa"),
        ({}, "pasa"),
    ],
)
def test_c_asks_some_constant_to_fail_a_then_s_assertion(
    by_constant: dict[str, str], expected: str
) -> None:
    assert StubMeasure(by_constant).rule_c == expected


PROVED = InterfaceProof("probado")
THEN_ONLY = _control({"given": 2, "when": 1, "then": 1})


def test_the_interface_measure_keeps_what_was_proved_and_the_facts_of_every_run() -> None:
    then = _failed("assertion", "then")
    altered = {"lictor.interface.altered": "2", "lictor.interface.altered.when": "1"}
    mas = TestCaseResult("test_x", status="failure", properties={**then.properties, **altered})
    proofs = {"A-1": PROVED, "A-2": InterfaceProof("no_concluyente", "roto")}
    control = {"A-1": THEN_ONLY}
    modes = {"mas": {"A-1": mas}}

    found = interface_measures(proofs, control, modes)

    assert found["A-1"] == InterfaceMeasure(
        outcome="probado",
        calls=StepCounts(given=2, when=1, then=1),
        altered={"mas": StepCounts(when=1, outside=1)},
        failed_step={"mas": "then"},
    )
    assert found["A-2"] == InterfaceMeasure(
        outcome="no_concluyente",
        calls=StepCounts(),
        altered={"mas": StepCounts()},
        failed_step={"mas": None},
    )


FULL = DriverMeasures(
    red={"A-1": RedMeasure("then", "entonces"), "A-2": RedMeasure(None, "no_aserta")},
    stubs={"A-1": StubMeasure({"return None": "entonces", "return True": "pasa"})},
    interface={
        "A-3": InterfaceMeasure(
            outcome="sin_alterar",
            calls=StepCounts(1, 2, 3, 4),
            altered={"mas": StepCounts(0, 1, 0, 0), "menos": StepCounts(0, 0, 1, 0)},
            failed_step={"mas": "then", "menos": None},
        )
    },
)


@pytest.mark.parametrize("measures", [FULL, DriverMeasures(red={})])
def test_the_journal_record_reads_back_as_it_was_written(measures: DriverMeasures) -> None:
    assert measures_from_record(measures_record(measures)) == measures


def test_the_record_of_a_stub_carries_what_c_says() -> None:
    record = measures_record(FULL)

    assert record["stubs"] == {
        "A-1": {
            "by_constant": {"return None": "entonces", "return True": "pasa"},
            "rule_c": "entonces",
        }
    }
    assert record["red"] == {
        "A-1": {"step": "then", "caught": "entonces"},
        "A-2": {"step": None, "caught": "no_aserta"},
    }


def _broken(path: tuple[str, ...], value: object) -> dict[str, object]:
    record = measures_record(FULL)
    target: dict[str, object] = record
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return record


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"stubs": None, "interface": None},
        _broken(("red",), []),
        _broken(("red", "A-1"), "entonces"),
        _broken(("red", "A-1", "caught"), "cazado"),
        _broken(("stubs",), "x"),
        _broken(("stubs", "A-1", "by_constant"), []),
        _broken(("stubs", "A-1", "by_constant", "return None"), "cazado"),
        _broken(("interface",), 3),
        _broken(("interface", "A-3", "outcome"), "cazado"),
        _broken(("interface", "A-3", "calls"), None),
        _broken(("interface", "A-3", "calls", "then"), -1),
        _broken(("interface", "A-3", "calls", "then"), True),
        _broken(("interface", "A-3", "calls", "outside"), "4"),
        _broken(("interface", "A-3", "calls"), {}),
        _broken(("interface", "A-3", "altered"), None),
        _broken(("interface", "A-3", "altered", "mas"), None),
        _broken(("interface", "A-3", "failed_step"), None),
        _broken(("interface", "A-3", "failed_step", "mas"), "luego"),
    ],
)
def test_a_record_that_doesn_t_follow_the_schema_reads_as_none(raw: object) -> None:
    assert measures_from_record(raw) is None


def test_a_record_keyed_by_something_else_than_text_reads_as_none() -> None:
    record = measures_record(FULL)
    record["red"] = {1: {"step": None, "caught": "pasa"}}

    assert measures_from_record(record) is None
