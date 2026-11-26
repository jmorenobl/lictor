"""The rule that weighs the scenario's values (ADR-0016), and the provoked exception (ADR-0011).

What the impostor of the Gherkin reported of each test is judged here as the plugins report it:
JUnit properties, read by `TestCaseResult`.
"""

import json
from dataclasses import replace
from typing import Any, Literal

import pytest

from lictor.domain.gherkin_proof import (
    GherkinFacts,
    driver_statements,
    gherkin_facts,
    reached_the_interface,
    reads_what_broke,
    static_facts,
    to_change,
    value_outcome,
)
from lictor.domain.interface_proof import (
    SAME_READING,
    InterfaceProof,
    interface_proof,
    only_the_value_note,
    person_notes,
    provoked_note,
    unproved_reason,
    unreadable,
    unreadable_result_note,
    weighed_proof,
    weighs_the_gherkin,
)
from lictor.domain.model import Statement, TestCaseResult, UnreadValue

DRIVER = ("tests/acceptance/",)
OWN = "tests/acceptance/test_pedidos.py"
MODES = ("mas", "menos")

Status = Literal["passed", "failure", "error", "skipped"]


def _case(status: Status = "passed", **given: Any) -> TestCaseResult:
    """A test as the plugins report it: `trace`, `altered`, `alterable`, `unread`, the failure's
    `kind`, `step`, `frame` and `line`, the calls of its Then (`calls_then`, 1 by default), and
    any other property with `__` for its dots."""
    keys = ("trace", "altered", "alterable", "unread", "kind", "step", "frame", "line")
    trace, altered, alterable, unread, kind, step, frame, line = (given.pop(k, "") for k in keys)
    calls = str(given.pop("calls_then", 1))
    properties = {
        "lictor.interface.calls": calls,
        "lictor.interface.calls.then": calls,
        "lictor.interface.trace": trace,
        "lictor.gherkin.values": "2",
        "lictor.gherkin.alterable": alterable,
        "lictor.gherkin.unread": json.dumps(unread or []),
        "lictor.gherkin.unreceived": "0",
        "lictor.gherkin.altered": "1" if altered else "0",
        "lictor.failure": kind,
        "lictor.step": step,
        "lictor.frame": frame,
        "lictor.line": line,
        **{key.replace("__", "."): value for key, value in given.items()},
    }
    return TestCaseResult("test_ped_001", status=status, properties=properties)


TRACE = "1:aa:x1.y1,2:bb:z1"
CONTROL = _case(trace=TRACE, alterable="0,1")
CAUGHT = _case(
    "failure", trace=TRACE, altered=True, kind="assertion", step="then", frame=OWN, line="12"
)


def test_what_the_control_alone_says_of_a_test() -> None:
    control = _case(alterable="0,1", unread=[["el total es 12", "total", "12"]])

    facts = static_facts(control)

    assert facts.values == 2
    assert facts.unread == (UnreadValue("el total es 12", "total", "12"),)
    assert to_change(control) == (0, 1)
    assert not facts.every_value_read


def test_an_unread_list_that_isn_t_one_is_nothing_unread() -> None:
    broken = replace(CONTROL, properties={**CONTROL.properties, "lictor.gherkin.unread": "{x"})

    assert static_facts(broken).unread == ()


@pytest.mark.parametrize(
    ("case", "outcome"),
    [
        (None, "sin_resultado"),
        (_case("skipped", altered=True), "sin_resultado"),
        (_case(trace=TRACE), "sin_cambiar"),
        (_case(trace=TRACE, altered=True), "pasa"),
        (CAUGHT, "cazado"),
        (replace(CAUGHT, properties={**CAUGHT.properties, "lictor.step": "when"}), "error"),
        (_case("failure", trace=TRACE, altered=True, kind="error", step="then"), "error"),
        (_case("failure", trace="1:aa:x2.y1,2:bb:z1", altered=True), "entrada"),
    ],
)
def test_what_one_changed_value_says(case: TestCaseResult | None, outcome: str) -> None:
    assert value_outcome(case, CONTROL, None, DRIVER) == outcome


def test_a_call_with_another_argument_is_an_input() -> None:
    changed = _case(trace="1:aa:x2.y1,2:bb:z1")

    assert reached_the_interface(changed, CONTROL, None)


def test_what_differs_between_two_controls_isn_t_an_input() -> None:
    """The time, or anything else the driver gets anew in each run: two controls tell it."""
    again = _case(trace="1:aa:x1.y9,2:bb:z1")
    changed = _case(trace="1:aa:x1.y5,2:bb:z1")

    assert not reached_the_interface(changed, CONTROL, again)
    assert reached_the_interface(changed, CONTROL, None)


@pytest.mark.parametrize(
    ("trace", "status", "reached"),
    [
        ("1:aa:x1.y1,2:bb:z1,3:cc:", "failure", True),  # more calls than the control
        ("1:aa:x1.y1", "passed", True),  # it passed with fewer: one call went elsewhere
        ("1:aa:x1.y1", "failure", False),  # it failed before the rest: nothing went in
        ("1:zz:x1.y1,2:bb:z1", "failure", True),  # another function
        ("1:aa:x1,2:bb:z1", "failure", True),  # another number of arguments
    ],
)
def test_the_calls_are_compared_one_by_one(trace: str, status: Status, reached: bool) -> None:
    assert reached_the_interface(_case(status, trace=trace), CONTROL, None) is reached


def _facts(**runs: TestCaseResult | None) -> GherkinFacts:
    """Each run by `<mode>_<number>`."""
    by_key = {(key.split("_")[0], int(key.split("_")[1])): case for key, case in runs.items()}
    return gherkin_facts(CONTROL, None, by_key, MODES, DRIVER)


def test_a_value_caught_in_every_mode_is_read_and_says_where() -> None:
    facts = _facts(mas_0=CAUGHT, menos_0=CAUGHT, mas_1=CAUGHT, menos_1=CAUGHT)

    assert facts.read == (0, 1)
    assert facts.every_value_read
    assert facts.place == f"{OWN}:12"


def test_an_input_doesn_t_count_against_the_values_read() -> None:
    entered = _case(trace="1:aa:x9.y1,2:bb:z1", altered=True)
    facts = _facts(mas_0=CAUGHT, menos_0=CAUGHT, mas_1=entered, menos_1=entered)

    assert (facts.read, facts.inputs) == ((0,), (1,))
    assert facts.every_value_read


def test_a_value_that_survives_its_change_isn_t_read() -> None:
    passes = _case(trace=TRACE, altered=True)
    facts = _facts(mas_0=CAUGHT, menos_0=CAUGHT, mas_1=passes, menos_1=CAUGHT)

    assert facts.survived == (1,)
    assert not facts.every_value_read


def test_only_inputs_or_errors_read_nothing() -> None:
    failed = _case("failure", trace=TRACE, altered=True, kind="error", step="then")
    facts = _facts(mas_0=failed, menos_0=failed, mas_1=None, menos_1=None)

    assert facts.errors == (0, 1)
    assert not facts.every_value_read


def test_the_scenario_s_value_weighs_only_what_the_interface_couldn_t_prove() -> None:
    for outcome in ("sobrevive", "no_aserta", "sin_alterar", "a_traves"):
        assert weighs_the_gherkin(InterfaceProof(outcome))
    # An assertion of the driver outside the Then caught the result: moving it proves the test.
    for outcome in ("probado", "provocada", "no_llama", "no_concluyente", "fuera_del_entonces"):
        assert not weighs_the_gherkin(InterfaceProof(outcome))


READ = GherkinFacts(2, read=(0, 1), place=f"{OWN}:12")
UNREAD = GherkinFacts(2, unread=(UnreadValue("el tipo es alta", "tipo", "alta"),))


@pytest.mark.parametrize(
    ("proof", "facts", "outcome"),
    [
        # A value its Then never reads comes first, even over a proof: the test can be fixed.
        (InterfaceProof("probado"), UNREAD, "ignora_el_valor"),
        (InterfaceProof("provocada", "de un Entonces (modo mas)"), UNREAD, "ignora_el_valor"),
        (InterfaceProof("probado"), READ, "probado"),
        (InterfaceProof("provocada", "x"), READ, "provocada"),
        (InterfaceProof("sobrevive", "mas"), READ, "solo_el_valor"),
        (InterfaceProof("a_traves", libraries=("typer",)), READ, "solo_el_valor"),
        (
            InterfaceProof("sobrevive", "mas"),
            GherkinFacts(2, read=(0,), survived=(1,)),
            "sobrevive",
        ),
        (InterfaceProof("no_llama"), READ, "no_llama"),
        (InterfaceProof("fuera_del_entonces", "x"), READ, "fuera_del_entonces"),
        (InterfaceProof("sobrevive", "mas"), None, "sobrevive"),
    ],
)
def test_the_order_of_the_rule(
    proof: InterfaceProof, facts: GherkinFacts | None, outcome: str
) -> None:
    assert weighed_proof(proof, facts, CONTROL).outcome == outcome


def test_only_the_value_needs_the_when_or_the_then_to_reach_the_interface() -> None:
    from_a_given = _case(calls_then=0, **{"lictor__interface__calls__given": "1"})

    assert weighed_proof(InterfaceProof("sin_alterar"), READ, from_a_given).outcome == (
        "sin_alterar"
    )
    through = _case(calls_then=0, **{"lictor__interface__via__when": "1"})
    assert weighed_proof(InterfaceProof("a_traves"), READ, through).outcome == "solo_el_valor"


def test_only_the_value_keeps_where_it_was_read_and_the_libraries() -> None:
    proof = weighed_proof(InterfaceProof("a_traves", libraries=("typer",)), READ, CONTROL)

    assert proof == InterfaceProof("solo_el_valor", f"{OWN}:12", ("typer",))
    assert not proof.fixable


def test_a_value_the_then_ignores_is_told_to_the_agent_with_the_value() -> None:
    proof = weighed_proof(InterfaceProof("sobrevive", "mas"), UNREAD, CONTROL)
    reason = unproved_reason("VER-005", "test_alta", proof, ["src/"])

    assert proof.fixable
    assert "el Entonces «el tipo es alta» recibe «alta» del escenario y no lo usa" in reason
    assert "compara con assert" in reason


@pytest.mark.parametrize("outcome", ["solo_el_valor", "provocada"])
def test_the_agent_is_told_only_not_to_change_what_goes_to_the_person(outcome: str) -> None:
    reason = unproved_reason("ENR-005", "test_enr", InterfaceProof(outcome, "x"), ["src/"])

    assert reason.endswith(
        "Este test no lo cambies: Lictor no puede comprobarlo, y lo decidirá una persona."
    )


def test_the_person_reads_only_facts_and_no_invitation_to_register() -> None:
    proofs = {
        "ENR-005": InterfaceProof("solo_el_valor", f"{OWN}:12"),
        "ORD-009": InterfaceProof("provocada", "de un Entonces (modo mas)"),
        "CON-001": InterfaceProof("sin_alterar"),
    }

    notes = person_notes(proofs)

    assert notes[1:] == [
        only_the_value_note({"ENR-005": proofs["ENR-005"]}),
        provoked_note({"ORD-009": proofs["ORD-009"]}),
    ]
    assert f"ENR-005, en {OWN}:12" in notes[1]
    through = InterfaceProof("solo_el_valor", "", ("typer.testing",))
    assert "CLI-001, a través de typer.testing:" in only_the_value_note({"CLI-001": through})
    assert "ORD-009, de un Entonces (modo mas)" in notes[2]
    assert not any("register" in note for note in notes)
    assert unreadable(proofs)
    assert not unreadable({"CON-001": proofs["CON-001"]})


# The provoked exception: the impostor numbers the driver's calls and says which raised.


def _iface(status: Status = "passed", raised: str = "", swallowed: str = "", **failure: str):
    case = _case(status, alterable="", **failure)
    properties = {
        **case.properties,
        "lictor.interface.altered": "1",
        "lictor.interface.altered.then": "1",
        "lictor.interface.raised_at": raised,
        "lictor.interface.swallowed_at": swallowed,
    }
    return replace(case, properties=properties)


def test_an_exception_only_an_altered_value_raised_proves_nothing() -> None:
    """A weak test (`facturar(p) is not None`) whose altered order made the code raise."""
    control = _iface()
    more = _iface("failure", swallowed="1:then", kind="assertion", step="then", frame=OWN)
    less = _iface("failure", swallowed="1:then", kind="impostor", step="then", frame=OWN)

    proof = interface_proof(control, {"mas": more, "menos": less}, DRIVER)

    assert proof == InterfaceProof("provocada", "de un Entonces (modo mas)")
    assert not proof.fixable


def test_an_exception_the_control_raised_too_is_the_test_s_to_check() -> None:
    control = _iface(raised="1")
    more = _iface("failure", swallowed="1:then", kind="assertion", step="then", frame=OWN)
    less = _iface("failure", swallowed="1:then", kind="impostor", step="then", frame=OWN)

    assert interface_proof(control, {"mas": more, "menos": less}, DRIVER).proved


def test_a_provoked_exception_doesn_t_hide_what_says_nothing_either() -> None:
    control = _iface()
    survives = _iface("passed", swallowed="1:when")
    out = _iface("failure", swallowed="1:then", kind="assertion", step="given", frame=OWN)

    assert interface_proof(control, {"mas": survives, "menos": survives}, DRIVER).outcome == (
        "provocada"
    )
    assert interface_proof(control, {"mas": out, "menos": out}, DRIVER).outcome == (
        "fuera_del_entonces"
    )


def test_a_swallowed_entry_that_isn_t_one_is_ignored() -> None:
    case = _iface(swallowed="x:then,2:nada,,3")

    assert case.interface_swallowed_at == ((2, None), (3, None))


# F85: a Then that couldn't read the altered result, in the statement that compares the value.

STEP = "tests/acceptance/conftest.py"


def _chain(*elements: str) -> str:
    """The statements as `lictor_outcome` reports them: `line` or `line:fed,lines`, in `STEP`."""
    return ";".join(
        f"{STEP}:{element}" if ":" in element else f"{STEP}:{element}:" for element in elements
    )


def _broke(chain: str, step: str = "then", kind: str = "error") -> TestCaseResult:
    """A mode in which the Then raised something else than an assertion of the driver."""
    return _iface("failure", kind=kind, step=step, frame=STEP, **{"lictor__statement": chain})


def _read(chain: str) -> TestCaseResult:
    """A run of the Gherkin in which changing a value alone made this assertion fail."""
    case = _case(
        "failure", trace=TRACE, altered=True, kind="assertion", step="then", frame=STEP, line="1"
    )
    return replace(case, properties={**case.properties, "lictor.statement": chain})


def _weighed(modes: dict[str, TestCaseResult], read: str | dict[int, str]) -> InterfaceProof:
    """What the rule says of a test with these modes, whose two values are read where `read`
    says (one chain for both, or one per value)."""
    control = _iface()
    proof = interface_proof(control, modes, (STEP,))
    chains = read if isinstance(read, dict) else {0: read, 1: read}
    runs = {(mode, n): _read(chain) for n, chain in chains.items() for mode in MODES}
    facts = gherkin_facts(replace(CONTROL), None, runs, MODES, (STEP,))
    return weighed_proof(proof, facts, control)


# COR-006: `assert _texts(record, "Subsanacion") == [correction]` on 727, `_texts` parsing the XML
# on 713; `mas` breaks it, `menos` is caught by the next Then's assertion. Its second value is
# read on 728.
COR_006 = {
    "mas": _broke(_chain("727", "713")),
    "menos": _iface("failure", kind="assertion", step="then", frame=STEP),
}
# ENR-002: `root = _returned_document(exchange)` on 250, which feeds the `assert` on 253.
ENR_002 = _broke(_chain("250:251,253", "240"))


@pytest.mark.parametrize(
    ("modes", "read", "outcome"),
    [
        (COR_006, {0: _chain("727"), 1: _chain("728")}, "probado"),
        # ENR-001: the assertion itself calls the helper that couldn't parse, in both modes.
        ({mode: _broke(_chain("255", "242")) for mode in MODES}, _chain("255"), "probado"),
        ({mode: ENR_002 for mode in MODES}, _chain("253"), "probado"),
        # A correct helper of one line: the step calls it, and its own assertion parses.
        ({m: _broke(_chain("30", "12", "5")) for m in MODES}, _chain("30", "12"), "probado"),
        # The error, raised on the step's statement, came from deeper than the comparison.
        ({m: _broke(_chain("30")) for m in MODES}, _chain("30", "12"), "probado"),
        # A weak helper (B1): it parses on one line and compares a Given's value on another.
        ({m: _broke(_chain("30", "12")) for m in MODES}, _chain("30", "13"), "solo_el_valor"),
        # The parsed name isn't what the comparison loads: `root = parse(x)`, `assert ctx.v == v`.
        ({m: _broke(_chain("250:251")) for m in MODES}, _chain("253"), "solo_el_valor"),
        # Another statement of the step, nothing bound by one feeding the other.
        (COR_006, {0: _chain("740"), 1: _chain("741")}, "solo_el_valor"),
        # A mode that survives says the test doesn't check that result: nothing to join.
        ({"mas": _broke(_chain("727")), "menos": _iface()}, _chain("727"), "solo_el_valor"),
        # An error outside a Then, or an assertion outside the driver, isn't the Then's reading.
        ({m: _broke(_chain("727"), step="when") for m in MODES}, _chain("727"), "solo_el_valor"),
        (
            {
                m: _iface(
                    "failure",
                    kind="assertion",
                    step="then",
                    frame="src/x.py",
                    lictor__statement=_chain("727"),
                )
                for m in MODES
            },
            _chain("727"),
            "solo_el_valor",
        ),
    ],
)
def test_a_then_that_can_t_read_the_altered_result_where_it_reads_the_value_proves_it(
    modes: dict[str, TestCaseResult], read: str | dict[int, str], outcome: str
) -> None:
    assert _weighed(modes, read).outcome == outcome


def test_what_the_same_reading_proved_says_so() -> None:
    proof = _weighed(COR_006, {0: _chain("727"), 1: _chain("728")})

    assert proof == InterfaceProof("probado", SAME_READING)
    assert proof.proved


def test_a_value_read_where_the_result_broke_still_needs_every_value_read() -> None:
    proof = interface_proof(_iface(), COR_006, (STEP,))
    facts = GherkinFacts(2, read=(0,), survived=(1,), read_at=(_read(_chain("727")).statements,))

    assert weighed_proof(proof, facts, _iface()).outcome == "no_aserta"


def test_an_unread_value_comes_before_the_same_reading() -> None:
    proof = interface_proof(_iface(), COR_006, (STEP,))

    assert weighed_proof(proof, UNREAD, _iface()).outcome == "ignora_el_valor"


def test_a_then_that_couldn_t_read_it_with_no_value_to_weigh_goes_to_the_person() -> None:
    """`assert _texts(record, "RechazoPrevio") == []`: right, and nothing of the scenario in it."""
    proof = interface_proof(_iface(), COR_006, (STEP,))
    weighed = weighed_proof(proof, GherkinFacts(0), _iface())

    assert weighed.outcome == "ilegible"
    assert not weighed.fixable
    reason = unproved_reason("COR-008", "test_cor_008", weighed, ["src/"])
    assert reason.endswith(
        "Este test no lo cambies: Lictor no puede comprobarlo, y lo decidirá una persona."
    )
    note = person_notes({"COR-008": weighed})[0]
    assert note == unreadable_result_note({"COR-008": weighed})
    assert note.startswith("Lictor no pudo comprobar COR-008 (error de un Entonces, en ")


def test_without_the_gherkin_a_then_that_couldn_t_read_it_is_retried_as_before() -> None:
    proof = interface_proof(_iface(), COR_006, (STEP,))

    assert weighed_proof(proof, None, _iface()) == proof
    assert proof.outcome == "no_aserta"
    assert proof.fixable


def test_only_modes_that_all_broke_reading_in_the_driver_carry_their_statements() -> None:
    outside = _broke("src/lxml/parser.pxi:689:")
    mixed = {
        "mas": _broke(_chain("727")),
        "menos": _iface("failure", kind="assertion", step="then", frame="src/x.py"),
    }

    assert interface_proof(_iface(), COR_006, (STEP,)).broke_at == (
        _broke(_chain("727", "713")).statements,
    )
    assert interface_proof(_iface(), {m: outside for m in MODES}, (STEP,)).broke_at == ()
    assert interface_proof(_iface(), mixed, (STEP,)).broke_at == ()


@pytest.mark.parametrize(
    ("broke", "read", "joined"),
    [
        ("727;713", "727", True),
        ("727", "727;713", True),
        ("727", "727", True),
        ("30;12", "30;13", False),
        ("250:253", "253", True),
        ("250:253", "254", False),
        ("", "727", False),
        ("727", "", False),
    ],
)
def test_two_chains_are_one_reading_when_one_is_a_prefix_or_the_broken_one_feeds_the_other(
    broke: str, read: str, joined: bool
) -> None:
    def chain(text: str) -> tuple[Statement, ...]:
        return _broke(_chain(*text.split(";")) if text else "").statements

    assert reads_what_broke(chain(broke), chain(read)) is joined


def test_another_file_at_the_same_place_isn_t_the_same_reading() -> None:
    mine = (Statement(STEP, 250, frozenset({253})),)
    theirs = (Statement("tests/acceptance/otro.py", 253),)

    assert not reads_what_broke(mine, theirs)


def test_the_statements_stop_at_the_first_outside_the_driver() -> None:
    case = _broke(f"{STEP}:727:;src/pkg/mod.py:10:;{STEP}:713:")

    assert driver_statements(case, (STEP,)) == (Statement(STEP, 727),)


@pytest.mark.parametrize(
    "facts",
    [
        GherkinFacts(2, survived=(0,)),
        GherkinFacts(2, inputs=(0,)),
        GherkinFacts(2, errors=(0,)),
        GherkinFacts(2, read=(0,), survived=(1,)),
    ],
)
def test_a_then_that_couldn_t_read_it_is_ilegible_only_when_nothing_was_weighed(
    facts: GherkinFacts,
) -> None:
    """A value that survived, went in or failed otherwise was weighed: it stays as before."""
    proof = interface_proof(_iface(), COR_006, (STEP,))

    assert weighed_proof(proof, facts, _iface()).outcome == "no_aserta"


def test_the_note_of_what_couldn_t_be_read_names_each_with_why() -> None:
    proofs = {"A-1": InterfaceProof("ilegible", "x"), "B-2": InterfaceProof("ilegible", "y")}

    assert unreadable_result_note(proofs) == (
        "Lictor no pudo comprobar A-1 (x); B-2 (y): con lo que devuelve la interfaz alterado, su "
        "Entonces no pudo leerlo, y el escenario no le da ningún valor que Lictor pueda cambiar "
        "para ver qué compara. Léelo."
    )


def test_where_nothing_was_read_there_is_no_place() -> None:
    failed = _case("failure", trace=TRACE, altered=True, kind="error", step="then")
    facts = _facts(mas_0=failed, menos_0=failed, mas_1=None, menos_1=None)

    assert (facts.place, facts.read_at) == ("", ())
