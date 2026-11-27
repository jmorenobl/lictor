"""What a fresh driver's red check saw, and what C and the interface decided of it."""

import pytest

from lictor.domain.driver_measures import (
    DriverMeasures,
    InterfaceMeasure,
    RedMeasure,
    StubMeasure,
    interface_measures,
    measures_from_record,
    measures_record,
    red_check_phase,
    red_check_timing,
    red_measures,
    stub_measures,
)
from lictor.domain.gherkin_proof import GherkinFacts
from lictor.domain.interface_proof import InterfaceProof, ProofOutcome
from lictor.domain.model import Statement, StepCounts, TestCaseResult, UnreadValue
from lictor.domain.stub_proof import StubOutcome, StubProof, caught

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
        "A-1": RedMeasure("when", "sin_implementar", "failure"),
        "A-2": RedMeasure("then", "entonces", "failure"),
        "A-3": RedMeasure(None, "sin_resultado", ""),
    }


def test_the_stubs_say_how_each_constant_fared_and_what_was_decided() -> None:
    runs = [
        ("None", {"A-1": _failed("assertion"), "A-2": PASSED}),
        ("True", {"A-1": PASSED, "A-2": PASSED}),
    ]
    proofs = {
        "A-1": StubProof("probado"),
        "A-2": StubProof("a_la_interfaz"),
        "A-3": StubProof("no_aserta"),
    }

    found = stub_measures(["A-1", "A-2", "A-4"], runs, DRIVER, proofs, {"A-2": PROVED})

    assert found == {
        "A-1": StubMeasure({"None": "entonces", "True": "pasa"}, "probado"),
        "A-2": StubMeasure({"None": "pasa", "True": "pasa"}, "a_la_interfaz", "probado"),
    }


@pytest.mark.parametrize(
    ("outcome", "interface", "verdict"),
    [
        ("probado", None, "aceptado"),
        ("a_la_interfaz", "probado", "aceptado"),
        ("a_la_interfaz", "sin_alterar", "sin_comprobar"),
        ("a_la_interfaz", "sobrevive", "rechazado"),
        ("a_la_interfaz", None, "rechazado"),
        ("no_aserta", None, "rechazado"),
    ],
)
def test_a_stub_measure_says_its_verdict(
    outcome: StubOutcome, interface: ProofOutcome | None, verdict: str
) -> None:
    assert StubMeasure({}, outcome, interface).verdict == verdict


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


def test_the_interface_measure_keeps_what_weighed_the_proof() -> None:
    swallowed = {"lictor.interface.swallowed_at": "0:then"}
    then = _failed("assertion", "then")
    mas = TestCaseResult("test_x", status="failure", properties={**then.properties, **swallowed})
    facts = GherkinFacts(1, read=(0,), place="tests/acceptance/test_x.py:3")
    weighed = {"A-1": InterfaceProof("provocada", "de un Entonces (modo mas)")}

    found = interface_measures(
        weighed,
        {"A-1": THEN_ONLY},
        {"mas": {"A-1": mas}},
        unweighed={"A-1": PROVED},
        gherkin={"A-1": facts},
    )

    assert (found["A-1"].outcome, found["A-1"].interface) == ("provocada", "probado")
    assert found["A-1"].provoked == ("mas",)
    assert found["A-1"].gherkin == facts


FULL = DriverMeasures(
    red={"A-1": RedMeasure("then", "entonces"), "A-2": RedMeasure(None, "no_aserta")},
    stubs={
        "A-1": StubMeasure({"None": "entonces", "True": "pasa"}, "probado"),
        "A-2": StubMeasure({"None": "pasa"}, "a_la_interfaz", "sin_alterar"),
    },
    interface={
        "A-3": InterfaceMeasure(
            outcome="sin_alterar",
            calls=StepCounts(1, 2, 3, 4),
            altered={"mas": StepCounts(0, 1, 0, 0), "menos": StepCounts(0, 0, 1, 0)},
            failed_step={"mas": "then", "menos": None},
        ),
        "A-4": InterfaceMeasure(
            outcome="a_traves",
            calls=StepCounts(given=1),
            altered={"mas": StepCounts()},
            failed_step={"mas": None},
            via=StepCounts(when=31),
            handed=StepCounts(then=1),
            libraries=("typer.testing",),
        ),
        "A-5": InterfaceMeasure(
            outcome="solo_el_valor",
            calls=StepCounts(then=1),
            altered={"mas": StepCounts(then=1), "menos": StepCounts(then=1)},
            failed_step={"mas": None, "menos": None},
            interface="sobrevive",
            provoked=("menos",),
            gherkin=GherkinFacts(
                3,
                (UnreadValue("el tipo es alta", "tipo", "alta"),),
                1,
                read=(0,),
                inputs=(1,),
                errors=(2,),
                place="tests/acceptance/test_x.py:12",
                read_at=(
                    (
                        Statement("tests/acceptance/test_x.py", 10, frozenset({12})),
                        Statement("tests/acceptance/test_x.py", 3),
                    ),
                ),
            ),
        ),
    },
)


@pytest.mark.parametrize("measures", [FULL, DriverMeasures(red={})])
def test_the_journal_record_reads_back_as_it_was_written(measures: DriverMeasures) -> None:
    assert measures_from_record(measures_record(measures)) == measures


def test_the_record_of_a_stub_carries_what_c_and_the_interface_decided() -> None:
    record = measures_record(FULL)

    assert record["stubs"] == {
        "A-1": {
            "by_constant": {"None": "entonces", "True": "pasa"},
            "outcome": "probado",
            "interface": None,
            "verdict": "aceptado",
        },
        "A-2": {
            "by_constant": {"None": "pasa"},
            "outcome": "a_la_interfaz",
            "interface": "sin_alterar",
            "verdict": "sin_comprobar",
        },
    }
    assert record["red"] == {
        "A-1": {"step": "then", "caught": "entonces", "status": ""},
        "A-2": {"step": None, "caught": "no_aserta", "status": ""},
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
        _broken(("red", "A-1", "status"), 1),
        _broken(("stubs",), "x"),
        _broken(("stubs", "A-1", "by_constant"), []),
        _broken(("stubs", "A-1", "by_constant", "None"), "cazado"),
        _broken(("stubs", "A-1", "outcome"), "cazado"),
        _broken(("stubs", "A-2", "interface"), "cazado"),
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
        _broken(("interface", "A-5", "interface"), "cazado"),
        _broken(("interface", "A-5", "provoked"), "menos"),
        _broken(("interface", "A-5", "gherkin"), []),
        _broken(("interface", "A-5", "gherkin", "values"), -1),
        _broken(("interface", "A-5", "gherkin", "read"), [True]),
        _broken(("interface", "A-5", "gherkin", "unread"), [["paso", "tipo"]]),
        _broken(("interface", "A-5", "gherkin", "unread"), None),
        _broken(("interface", "A-5", "gherkin", "place"), 12),
        _broken(("interface", "A-5", "gherkin", "read_at"), "x.py:3:"),
    ],
)
def test_a_record_that_doesn_t_follow_the_schema_reads_as_none(raw: object) -> None:
    assert measures_from_record(raw) is None


def test_the_record_of_the_interface_keeps_what_weighed_it() -> None:
    record = measures_record(FULL)["interface"]

    assert isinstance(record, dict)
    assert (record["A-5"]["interface"], record["A-5"]["provoked"]) == ("sobrevive", ["menos"])
    assert record["A-5"]["gherkin"] == {
        "values": 3,
        "unread": [["el tipo es alta", "tipo", "alta"]],
        "unreceived": 1,
        "read": [0],
        "inputs": [1],
        "survived": [],
        "errors": [2],
        "place": "tests/acceptance/test_x.py:12",
        "read_at": ["tests/acceptance/test_x.py:10:12;tests/acceptance/test_x.py:3:"],
    }
    assert record["A-3"]["gherkin"] is None


def test_a_record_keyed_by_something_else_than_text_reads_as_none() -> None:
    record = measures_record(FULL)
    record["red"] = {1: {"step": None, "caught": "pasa"}}

    assert measures_from_record(record) is None


def test_the_record_of_the_interface_keeps_what_a_library_called_for_the_test() -> None:
    record = measures_record(FULL)["interface"]

    assert isinstance(record, dict)
    assert record["A-4"]["via"] == {"given": 0, "when": 31, "then": 0, "outside": 0}
    assert record["A-4"]["handed"] == {"given": 0, "when": 0, "then": 1, "outside": 0}
    assert record["A-4"]["libraries"] == ["typer.testing"]


@pytest.mark.parametrize("libraries", [None, "typer.testing", [1]])
def test_a_record_without_its_libraries_doesn_t_follow_the_schema(libraries: object) -> None:
    record = measures_record(FULL)
    interface = dict(record["interface"])  # type: ignore[call-overload]
    interface["A-4"] = {**interface["A-4"], "libraries": libraries}

    assert measures_from_record({**record, "interface": interface}) is None


@pytest.mark.parametrize(
    ("run_dir", "phase"),
    [
        ("001-red", "red"),
        ("impostor-2", "stubs"),
        ("interfaz-mas", "interface"),
        ("stubs-interfaz-menos", "interface"),
        ("gherkin-control-bis", "gherkin"),
        ("stubs-gherkin-mas-1", "gherkin"),
    ],
)
def test_each_run_of_a_red_check_belongs_to_the_phase_its_evidence_names(
    run_dir: str, phase: str
) -> None:
    assert red_check_phase(run_dir, "001-red") == phase


def test_a_red_checks_timing_adds_each_phases_runs_in_the_order_they_run() -> None:
    runs = [("gherkin", 30), ("red", 10), ("stubs", 5), ("stubs", 7), ("interface", 20)]

    timing = red_check_timing(runs, 100)

    assert timing == {
        "duration_ms": 100,
        "phases": {
            "red": {"runs": 1, "duration_ms": 10},
            "stubs": {"runs": 2, "duration_ms": 12},
            "interface": {"runs": 1, "duration_ms": 20},
            "gherkin": {"runs": 1, "duration_ms": 30},
        },
    }
    phases = timing["phases"]
    assert isinstance(phases, dict)
    assert list(phases) == ["red", "stubs", "interface", "gherkin"]


def test_a_red_kept_without_its_status_reads_with_none() -> None:
    record = measures_record(DriverMeasures(red={"A-1": RedMeasure("then", "entonces")}))
    red = record["red"]
    assert isinstance(red, dict)
    del red["A-1"]["status"]

    found = measures_from_record(record)

    assert found is not None
    assert found.red == {"A-1": RedMeasure("then", "entonces", "")}


def test_the_red_keeps_the_junit_status_through_the_record() -> None:
    measures = DriverMeasures(red={"A-1": RedMeasure(None, "no_aserta", "error")})

    found = measures_from_record(measures_record(measures))

    assert found is not None
    assert found.red["A-1"].status == "error"
