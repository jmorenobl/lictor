"""Rule C of the stubs' impostors (ADR-0012): what each constant's run says of a pending test."""

import pytest

from lictor.domain.interface_proof import InterfaceProof, ProofOutcome
from lictor.domain.model import TestCaseResult
from lictor.domain.rules import StubConstant
from lictor.domain.stub_proof import (
    STUB_LIMITS_NOTE,
    StubOutcome,
    StubProof,
    caught,
    stub_proof,
    stub_reason,
    stub_verdict,
    unchecked_note,
)

DRIVER = ["tests/acceptance/"]
OWN = "tests/acceptance/test_x.py"
NONE = StubConstant(r"\1return None", "None")
TRUE = StubConstant(r"\1return True", "True")
ONE = StubConstant(r"\1return ONE", "un doble de Lictor", double=True)
EMPTY = StubConstant(r"\1return EMPTY", "un doble vacío de Lictor", double=True)
CONSTANTS = (NONE, TRUE, ONE, EMPTY)
PASSED = TestCaseResult("test_x")

Cases = dict[str, TestCaseResult | None]


def _failed(
    kind: str, step: str = "then", frame: str = OWN, message: str = "m", line: str = "7"
) -> TestCaseResult:
    properties = {
        "lictor.failure": kind,
        "lictor.frame": frame,
        "lictor.step": step,
        "lictor.line": line,
    }
    return TestCaseResult("test_x", status="failure", message=message, properties=properties)


THEN = _failed("assertion")
GIVEN = _failed("assertion", "given", message="assert True is None")
ERROR = _failed("error", message="TypeError: cannot unpack")
NONE_ERROR = _failed("error", message="AttributeError: 'NoneType' object")


def _by(
    none: TestCaseResult | None,
    true: TestCaseResult | None,
    one: TestCaseResult | None = ERROR,
    empty: TestCaseResult | None = ERROR,
) -> Cases:
    return {NONE.name: none, TRUE.name: true, ONE.name: one, EMPTY.name: empty}


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (None, "sin_resultado"),
        (TestCaseResult("test_x", status="skipped"), "sin_resultado"),
        (PASSED, "pasa"),
        (_failed("not_implemented"), "sin_implementar"),
        (THEN, "entonces"),
        (_failed("assertion", "when"), "otro_paso"),
        (_failed("assertion", ""), "otro_paso"),
        # An assertion raised outside the driver, as in a helper of the code, isn't the test's.
        (_failed("assertion", frame="src/x.py"), "no_aserta"),
        (ERROR, "no_aserta"),
        (TestCaseResult("test_x", status="failure"), "no_aserta"),
    ],
)
def test_how_the_red_and_the_stubs_read_a_test(case: TestCaseResult | None, expected: str) -> None:
    assert caught(case, DRIVER) == expected


@pytest.mark.parametrize(
    ("cases", "outcome"),
    [
        # Some constant makes a Then's assertion fail: the test depends on what they return.
        (_by(THEN, THEN, THEN, THEN), "probado"),
        (_by(NONE_ERROR, NONE_ERROR, THEN), "probado"),  # `r.vence`: only a double reaches it
        (_by(PASSED, THEN), "probado"),  # `r is None`
        (_by(NONE_ERROR, NONE_ERROR, THEN, PASSED), "probado"),  # `list(r) == []`
        # It passes with every plain constant: no double tells it from `r in (None, True)`.
        (_by(PASSED, PASSED, THEN, THEN), "a_la_interfaz"),
        (_by(PASSED, PASSED, PASSED, PASSED), "a_la_interfaz"),
        # It passes with the stubs as a no-op: what they return doesn't matter (an order).
        (_by(PASSED, GIVEN, GIVEN, GIVEN), "a_la_interfaz"),
        # Rejected, with what the agent can change.
        (_by(GIVEN, GIVEN, GIVEN, GIVEN), "fuera_del_entonces"),
        (_by(NONE_ERROR, NONE_ERROR, PASSED, PASSED), "sobrevive"),  # no assert, or `x not in r`
        (_by(NONE_ERROR, NONE_ERROR, ERROR, ERROR), "no_aserta"),  # `Decimal(r.total)`
        (_by(_failed("assertion", frame="src/x.py"), ERROR), "no_aserta"),
        # A run that crashed counts for nothing; with no run left, nothing is known.
        (_by(None, THEN, None, None), "probado"),
        (_by(None, None, None, None), "no_concluyente"),
        (_by(_failed("not_implemented"), None, None, None), "no_concluyente"),
    ],
)
def test_c_accepts_a_test_some_constant_catches_in_a_then(cases: Cases, outcome: str) -> None:
    assert stub_proof(cases, CONSTANTS, DRIVER).outcome == outcome


def test_without_doubles_c_is_the_same_rule() -> None:
    assert stub_proof(_by(NONE_ERROR, THEN), (NONE, TRUE), DRIVER).outcome == "probado"
    assert stub_proof(_by(NONE_ERROR, ERROR), (NONE, TRUE), DRIVER).outcome == "no_aserta"
    assert stub_proof(_by(PASSED, PASSED), (NONE, TRUE), DRIVER).outcome == "a_la_interfaz"
    assert stub_proof({}, (), DRIVER).outcome == "no_concluyente"


def test_what_c_tells_comes_from_a_double_before_a_plain_constant() -> None:
    """What trips a double is the way of checking that never reaches the assertion."""
    proof = stub_proof(_by(NONE_ERROR, NONE_ERROR, ERROR, ERROR), CONSTANTS, DRIVER)

    assert proof.detail == (
        "error de un Entonces, en tests/acceptance/test_x.py:7 "
        "(devolviendo un doble de Lictor: TypeError: cannot unpack)"
    )


def test_the_detail_names_where_and_with_what_each_rejection_was_seen() -> None:
    outside = stub_proof(_by(GIVEN, GIVEN, GIVEN, GIVEN), CONSTANTS, DRIVER)
    survives = stub_proof(_by(NONE_ERROR, NONE_ERROR, PASSED, ERROR), CONSTANTS, DRIVER)
    foreign = stub_proof(
        _by(_failed("assertion", frame="src/x.py", message="m"), ERROR, ERROR, ERROR),
        (NONE, TRUE),
        DRIVER,
    )

    assert outside.detail == (
        "una aserción de un Dado (devolviendo un doble de Lictor: assert True is None)"
    )
    assert survives.detail == "un doble de Lictor"
    assert foreign.detail == (
        "una aserción en src/x.py:7, que no es del driver (devolviendo None: m)"
    )


def test_an_unclassified_failure_is_named_as_such() -> None:
    unclassified = TestCaseResult("test_x", status="failure")

    proof = stub_proof(_by(unclassified, unclassified), (NONE, TRUE), DRIVER)

    assert (
        proof.detail
        == "un fallo sin clasificar fuera de los pasos, fuera del proyecto (devolviendo None)"
    )


@pytest.mark.parametrize(
    ("outcome", "interface", "verdict"),
    [
        ("probado", None, "aceptado"),
        ("a_la_interfaz", "probado", "aceptado"),
        ("a_la_interfaz", "sin_alterar", "sin_comprobar"),
        ("a_la_interfaz", "sobrevive", "rechazado"),
        ("a_la_interfaz", "no_llama", "rechazado"),
        # A command that passes without the stubs: never accepted unchecked.
        ("a_la_interfaz", "a_traves", "rechazado"),
        ("a_la_interfaz", "no_concluyente", "rechazado"),
        ("a_la_interfaz", None, "rechazado"),  # a profile without an impostor of the interface
        ("fuera_del_entonces", None, "rechazado"),
        ("sobrevive", None, "rechazado"),
        ("no_aserta", None, "rechazado"),
        ("no_concluyente", None, "rechazado"),
    ],
)
def test_the_interface_decides_what_went_to_it(
    outcome: StubOutcome, interface: ProofOutcome | None, verdict: str
) -> None:
    found = None if interface is None else InterfaceProof(interface)
    assert stub_verdict(StubProof(outcome), found) == verdict


def _reason(proof: StubProof, interface: InterfaceProof | None = None) -> str:
    return stub_reason("IVA-002", "test_iva_002", proof, interface, ["src/"])


def test_no_message_asks_for_a_check_in_a_given_nor_says_a_test_checks_nothing() -> None:
    """F64 → F63: the agent follows what it reads to the letter."""
    for outcome in ("fuera_del_entonces", "sobrevive", "no_aserta", "no_concluyente"):
        text = _reason(StubProof(outcome, "d"))
        assert text.startswith("El test test_iva_002 del escenario IVA-002")
        assert "en los Dados" not in text
        assert "no comprueba" not in text


def test_what_each_rejection_tells_the_agent() -> None:
    outside = _reason(StubProof("fuera_del_entonces", "una aserción de un Dado (devolviendo True)"))
    survives = _reason(StubProof("sobrevive", "un doble vacío de Lictor"))
    no_assert = _reason(StubProof("no_aserta", "error de un Entonces, en t.py:7 (devolviendo X)"))

    assert outside == (
        "El test test_iva_002 del escenario IVA-002, cuando los stubs devuelven un valor en lugar "
        "de lanzar NotImplementedError, falla por una aserción de un Dado (devolviendo True), no "
        "de un Entonces: la comprobación del resultado va en un paso Entonces, con el valor que da "
        "su escenario. Si el escenario espera un error, guárdalo en el Cuando y compáralo en el "
        "Entonces."
    )
    assert survives.startswith(
        "El test test_iva_002 del escenario IVA-002 pasa también cuando los stubs devuelven otro "
        "valor en lugar de lanzar NotImplementedError"
    )
    assert "doble" not in survives
    assert survives.endswith("no lo trates aparte ni compruebes su tipo.")
    assert "comprueba también lo que sí está" in survives
    assert "falla, pero con error de un Entonces, en t.py:7 (devolviendo X)" in no_assert
    assert "tal cual, sin convertirlo a otro tipo" in no_assert
    assert "con esos valores la interfaz no lo lanza" in no_assert
    assert no_assert.endswith("no los trates aparte.")


def test_what_went_to_the_interface_is_told_as_the_interface_says_it() -> None:
    told = _reason(StubProof("a_la_interfaz"), InterfaceProof("sobrevive", "mas"))
    alone = _reason(StubProof("a_la_interfaz"))

    assert told.startswith(
        "El test test_iva_002 del escenario IVA-002 pasa aunque los stubs no estén implementados "
        "y sigue pasando cuando la interfaz devuelve otro valor (modo mas)"
    )
    assert "este perfil no puede comprobar que mire el resultado" in alone


@pytest.mark.parametrize(
    "proof",
    [
        InterfaceProof("no_llama"),
        InterfaceProof("no_llama", "dado"),
        InterfaceProof("no_llama", "entrega", ("anyio._core",)),
        InterfaceProof("a_traves", libraries=("typer.testing",)),
        InterfaceProof("solo_el_valor", "tests/acceptance/test_iva.py:9"),
    ],
)
def test_what_checks_nothing_of_the_code_that_exists_is_told_as_such(
    proof: InterfaceProof,
) -> None:
    """The stubs aren't wrapped there: «no llama» would deny a When that calls one."""
    text = _reason(StubProof("a_la_interfaz"), proof)

    assert text == (
        "El test test_iva_002 del escenario IVA-002 pasa aunque los stubs no estén "
        "implementados, y lo que comprueban su Cuando y su Entonces no depende de lo que "
        "devuelve la interfaz: compara con assert, en un Entonces, el resultado con el valor "
        "que da su escenario."
    )


@pytest.mark.parametrize(
    ("interface", "verdict"),
    [("ignora_el_valor", "rechazado"), ("solo_el_valor", "rechazado"), ("provocada", "rechazado")],
)
def test_what_the_scenario_s_value_decides_rejects_over_the_stubs(
    interface: ProofOutcome, verdict: str
) -> None:
    assert stub_verdict(StubProof("a_la_interfaz"), InterfaceProof(interface)) == verdict


def test_a_value_the_then_ignores_is_told_as_the_interface_tells_it() -> None:
    proof = InterfaceProof(
        "ignora_el_valor", "el Entonces «el IVA es 21» recibe «21» del escenario"
    )

    assert _reason(StubProof("a_la_interfaz"), proof) == (
        "El test test_iva_002 del escenario IVA-002 pasa aunque los stubs no estén implementados, "
        "pero el Entonces «el IVA es 21» recibe «21» del escenario y no lo usa: compara con "
        "assert lo que devuelve la interfaz con ese valor."
    )


def test_an_exception_an_altered_value_raised_asks_for_a_test_that_can_be_judged() -> None:
    text = _reason(StubProof("a_la_interfaz"), InterfaceProof("provocada", "x"))

    assert "el valor que alteró volvió a la interfaz y la hizo lanzar" in text
    assert text.endswith("con el valor que da su escenario.")


def test_the_person_reads_the_known_limits_and_what_was_sealed_unchecked() -> None:
    assert "x not in r" in STUB_LIMITS_NOTE
    assert unchecked_note(["MUL-006", "MUL-007"]).startswith(
        "Lictor aceptó MUL-006, MUL-007 sin comprobarlos"
    )
