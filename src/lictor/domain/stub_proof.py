"""Whether a fresh driver's new pending test checks what its stubs would return (ADR-0012).

The red check runs the acceptance once per constant in place of every stub: the profile's plain
constants (the language's own values, like `None` and `True`) and its doubles, which answer what a
test does with a result with another double, so that the test reaches its assertion. A test is
accepted when some constant makes an assertion of the driver fail in a Then: it depends on that
value.

A test that passes with the stubs at their first constant (a no-op: `return None`) doesn't depend
on what they return, as one whose stub is an order whose effect only another rule will show. It
goes to the impostor of the interface, over that same tree, which doesn't wrap the stubs: their
value proves nothing, since no code that already exists returned it. So does a test that passes
with every plain constant, which no double can tell from `r in (None, True)`.

These rules judge what the runs reported; nothing here runs them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lictor.domain.interface_proof import (
    ASSERTION_KIND,
    STEP_PLACE,
    InterfaceProof,
    failure_place,
    said,
    unproved_reason,
)
from lictor.domain.model import TestCaseResult
from lictor.domain.rules import NOT_IMPLEMENTED, StubConstant, path_within

CHECKING_STEP = "then"

Caught = Literal[
    "entonces",  # an assertion of the driver, in a Then
    "otro_paso",  # an assertion of the driver, in another step or outside one
    "no_aserta",  # it failed, but not by an assertion of the driver
    "sin_implementar",  # a stub was still there
    "pasa",  # it passed
    "sin_resultado",  # the run has no result for it
]

StubOutcome = Literal[
    "probado",  # some constant made a Then's assertion of the driver fail
    "a_la_interfaz",  # it doesn't depend on what the stubs return: the interface judges it
    "fuera_del_entonces",  # some constant made an assertion of the driver fail, never in a Then
    "sobrevive",  # it passed with some constant, and no constant made its assertion fail
    "no_aserta",  # it failed with every constant, never by an assertion of the driver
    "no_concluyente",  # no run with the stubs replaced told anything of it
]

StubVerdict = Literal[
    "aceptado",
    "sin_comprobar",  # the interface found nothing it could alter: a person sees it
    "rechazado",
]

_REPLACED = "cuando los stubs devuelven un valor en lugar de lanzar NotImplementedError"
_ERROR_HINT = "Si el escenario espera un error, guárdalo en el Cuando y compáralo en el Entonces."


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
        return "entonces" if case.failed_step == CHECKING_STEP else "otro_paso"
    return "no_aserta"


@dataclass(frozen=True)
class StubProof:
    """What the constants said of one test, and what to tell the agent when they reject it."""

    outcome: StubOutcome
    detail: str = ""


# How a constant that rejects a test was caught, and what C then says: the most telling first.
_REJECTIONS: tuple[tuple[Caught, StubOutcome], ...] = (
    ("otro_paso", "fuera_del_entonces"),
    ("pasa", "sobrevive"),
    ("no_aserta", "no_aserta"),
)


def stub_proof(
    cases: Mapping[str, TestCaseResult | None],
    constants: Sequence[StubConstant],
    driver: Sequence[str],
) -> StubProof:
    """Judge one test by what each constant's run reported (`cases`, by the constant's name).

    A constant whose run crashed has no case: it counts for nothing.
    """
    found = {c.name: caught(cases.get(c.name), driver) for c in constants}
    plain = [c for c in constants if not c.double]
    if plain and all(found[c.name] == "pasa" for c in plain):
        return StubProof("a_la_interfaz")
    if "entonces" in found.values():
        return StubProof("probado")
    if constants and found[constants[0].name] == "pasa":
        return StubProof("a_la_interfaz")
    # What to tell the agent: a double's failure before a plain one's, since what trips a double
    # is a way of checking that never reaches the assertion, and what trips None anyone knows.
    ordered = [*(c for c in constants if c.double), *plain]
    for kind, outcome in _REJECTIONS:
        first = next((c for c in ordered if found[c.name] == kind), None)
        if first is not None:
            return StubProof(outcome, _detail(outcome, first, cases.get(first.name)))
    return StubProof("no_concluyente", "ninguna ejecución con los stubs sustituidos dio resultado")


def _detail(outcome: StubOutcome, constant: StubConstant, case: TestCaseResult | None) -> str:
    if outcome == "sobrevive" or case is None:
        return constant.name
    seen = said(f"devolviendo {constant.name}", case.message)
    step = STEP_PLACE[case.failed_step]
    if outcome == "fuera_del_entonces":
        return f"una aserción {step} {seen}"
    if case.failure_kind == ASSERTION_KIND:
        return f"una aserción {failure_place(case)}, que no es del driver {seen}"
    what = case.failure_kind or "un fallo sin clasificar"
    return f"{what} {step}, {failure_place(case)} {seen}"


def stub_verdict(proof: StubProof, interface: InterfaceProof | None) -> StubVerdict:
    """Accept, accept unchecked or reject: the interface decides what went to it."""
    if proof.outcome == "probado":
        return "aceptado"
    if proof.outcome != "a_la_interfaz" or interface is None:
        return "rechazado"
    if interface.proved:
        return "aceptado"
    return "sin_comprobar" if interface.outcome == "sin_alterar" else "rechazado"


# What each outcome tells the driver's agent. None asks for a check in a Given (F64 → F63), and
# none says a test checks nothing when it may compare with the scenario's value.
_REASONS: Mapping[str, str] = {
    "fuera_del_entonces": (
        "{head}, " + _REPLACED + ", falla por {detail}, no de un Entonces: la comprobación del "
        "resultado va en un paso Entonces, con el valor que da su escenario. " + _ERROR_HINT
    ),
    # It names no value: told «a double of Lictor», the real agent checked the type of every
    # result, in each Then, to tell Lictor's value apart (3 of 3), which any `isinstance` does.
    "sobrevive": (
        "{head} pasa también cuando los stubs devuelven otro valor en lugar de lanzar "
        "NotImplementedError, y con ningún valor falla una aserción de un Entonces: compara con "
        "assert el resultado con el valor que da su escenario y, si el escenario dice que algo "
        "no está, comprueba también lo que sí está. Ese otro valor lo pone Lictor para comprobar "
        "el test: no lo trates aparte ni compruebes su tipo."
    ),
    # The values are Lictor's, which a test that trips on them may want to handle apart: it
    # mustn't. Where it failed tells which line to change.
    "no_aserta": (
        "{head}, " + _REPLACED + ", falla, pero con {detail}, no con una aserción de un "
        "Entonces: compara con assert lo que devuelve la interfaz con el valor que da su "
        "escenario, tal cual, sin convertirlo a otro tipo. Si el escenario espera un error, con "
        "esos valores la interfaz no lo lanza: el Entonces tiene que fallar por su assert también "
        "entonces. Esos valores los pone Lictor para comprobar el test: no los trates aparte."
    ),
    "no_concluyente": "{head}, y no se pudo comprobar con los stubs sustituidos: {detail}.",
    # In the way to the interface the stubs aren't wrapped: no call from the When or the Then means
    # nothing it checks comes from code that already exists, not that it never calls the interface.
    "sin_codigo": (
        "{head} pasa aunque los stubs no estén implementados, y lo que comprueban su Cuando y su "
        "Entonces no depende de lo que devuelve la interfaz: compara con assert, en un Entonces, "
        "el resultado con el valor que da su escenario."
    ),
    "sin_interfaz": (
        "{head} pasa aunque los stubs devuelvan un valor en lugar de lanzar NotImplementedError, "
        "y este perfil no puede comprobar que mire el resultado: compara con assert, en un "
        "Entonces, el resultado con el valor que da su escenario."
    ),
}
_INTERFACE_PASSES = "pasa aunque los stubs no estén implementados"


def stub_reason(
    sid: str,
    test_name: str,
    proof: StubProof,
    interface: InterfaceProof | None,
    writable: Sequence[str],
) -> str:
    """Say why a test was rejected, from what the constants and the interface saw of it."""
    key: str = proof.outcome
    if proof.outcome == "a_la_interfaz":
        if interface is None:
            key = "sin_interfaz"
        elif interface.outcome == "no_llama":
            key = "sin_codigo"
        else:
            return unproved_reason(sid, test_name, interface, writable, _INTERFACE_PASSES)
    return _REASONS[key].format(
        head=f"El test {test_name} del escenario {sid}", detail=proof.detail
    )


# For the person, once the driver's attempts run out: the known ways of checking that never reach
# an assertion with any constant, though they compare with the scenario's value.
STUB_LIMITS_NOTE = (
    "Si alguno de estos tests ya es correcto, reescríbelo a mano: Lictor no puede comprobar un "
    "test que convierte lo que devuelve la interfaz a otro tipo (Decimal(), asdict, json.dumps, "
    "una expresión regular), que solo dice lo que no está (`x not in r`) o que desempaqueta el "
    "resultado. Compararlo tal cual con el valor del escenario sí se puede comprobar."
)


def unchecked_note(ids: Sequence[str]) -> str:
    """Why these were sealed without a proof, for the person: the agent never reads it."""
    return (
        f"Lictor aceptó {', '.join(ids)} sin comprobarlos: pasan aunque los stubs no estén "
        "implementados, y lo que la interfaz les devuelve desde el Cuando y el Entonces no se "
        "puede alterar. Revisa que su Entonces compare con assert el valor del escenario."
    )
