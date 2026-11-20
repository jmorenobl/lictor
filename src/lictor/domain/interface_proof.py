"""Whether a new test that passes with a fresh driver checks what the interface returns (ADR-0011).

With a fresh driver, a new scenario that already passes can't be told from a test that checks
nothing. The profile's impostor runs the acceptance again with the interface wrapped: once without
altering anything (the control) and once per mode, altering what it returns to the driver from the
steps that act and check (`ALTERED_STEPS`), never what a Given prepares. A test whose own
assertion in a Then fails in every mode, or that the impostor's error stops from the When on,
depends on that value, so the code already met its scenario. These rules judge what the impostor
reported; the plugin decides nothing.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lictor.domain.model import Step, TestCaseResult
from lictor.domain.rules import path_within

ASSERTION_KIND = "assertion"
IMPOSTOR_KIND = "impostor"  # the impostor raised its own error in place of the interface's

# What the impostor alters, told to the plugin: what the When does and what the Then reads. What a
# Given or a fixture gets stays real, so a check there proves nothing of the scenario's result.
ALTERED_STEPS: tuple[Step, ...] = ("when", "then")
CHECKING_STEP: Step = "then"  # the only step whose assertion proves the test checks the result
_STEP_PLACE: Mapping[Step | None, str] = {
    "given": "de un Dado",
    "when": "de un Cuando",
    "then": "de un Entonces",
    None: "fuera de los pasos",
}
_MESSAGE_LIMIT = 200

ProofOutcome = Literal[
    "probado",  # its own assertion failed in every mode: the code already met the scenario
    "no_llama",  # it never called the interface from a When or a Then
    "sobrevive",  # it passed with an altered result: it doesn't check it
    "no_aserta",  # it failed with an altered result, but not by an assertion of the test
    "fuera_del_entonces",  # it failed by an assertion of the driver, but outside a Then
    "sin_alterar",  # nothing the interface returned to it from the When on could be altered
    "no_concluyente",  # an impostor run told nothing about it
]

# Worst first: what a mode says of the test, when the modes disagree.
_PRIORITY: tuple[ProofOutcome, ...] = (
    "sobrevive",
    "no_aserta",
    "fuera_del_entonces",
    "sin_alterar",
    "no_concluyente",
)

# What the driver's agent can fix by writing the test again; the rest only a person decides.
_FIXABLE: frozenset[ProofOutcome] = frozenset(
    {"no_llama", "sobrevive", "no_aserta", "fuera_del_entonces"}
)
ONLY_FROM_A_GIVEN = "dado"  # the detail of a test that calls the interface only to prepare


@dataclass(frozen=True)
class InterfaceImpostor:
    """How the profile's impostor is told what to do: one variable, a control and its modes."""

    variable: str
    control: str
    modes: tuple[str, ...]


@dataclass(frozen=True)
class InterfaceProof:
    """What the impostor proved of one scenario, and why when it proved nothing."""

    outcome: ProofOutcome
    detail: str = ""

    @property
    def proved(self) -> bool:
        return self.outcome == "probado"

    @property
    def fixable(self) -> bool:
        """Whether writing the test again can prove it: a test that ignores the result can."""
        return self.outcome in _FIXABLE


def interface_proof(
    control: TestCaseResult | None,
    modes: Mapping[str, TestCaseResult | None],
    driver: Sequence[str],
) -> InterfaceProof:
    """Judge one scenario's test by the control run and each mode's run.

    It must pass in the control having called the interface from a When or a Then, and in every
    mode it must have had a result altered and fail by an assertion of the driver in a Then, or by
    the impostor's own error from the When on.
    """
    if control is None or control.status != "passed":
        state = "no tiene resultado" if control is None else f"acaba en {control.status}"
        return InterfaceProof(
            "no_concluyente", f"con la interfaz envuelta y sin alterar, su test {state}"
        )
    if control.interface_calls == 0:
        return InterfaceProof("no_llama")
    if control.interface_by_step("calls").from_when == 0:
        return InterfaceProof("no_llama", ONLY_FROM_A_GIVEN)
    if not modes:
        return InterfaceProof("no_concluyente", "el perfil no declara ningún modo")
    # What each mode says against the test; a mode that caught it says nothing.
    objections = [
        objection
        for mode, case in modes.items()
        if (objection := _objection(mode, case, driver)) is not None
    ]
    for outcome in _PRIORITY:
        found = next((o for o in objections if o.outcome == outcome), None)
        if found is not None:
            return found
    return InterfaceProof("probado")


def _objection(
    mode: str, case: TestCaseResult | None, driver: Sequence[str]
) -> InterfaceProof | None:
    """Why one mode doesn't prove the test, or None when the test's own assertion caught it."""
    if case is None or case.status == "skipped":
        return InterfaceProof("no_concluyente", f"en el modo {mode} su test no tiene resultado")
    altered = case.interface_by_step("altered")
    if altered.given or altered.outside:
        # The rule assumes nothing a Given prepares was altered: a plugin that did tells nothing.
        return InterfaceProof(
            "no_concluyente",
            f"en el modo {mode} el impostor alteró lo que devuelve un Dado o un fixture",
        )
    if case.interface_altered == 0:
        return InterfaceProof("sin_alterar")
    if case.status == "passed":
        if case.interface_altered == case.interface_altered_none:
            # All it got altered was the None of an order: no test could tell, a good one neither.
            return InterfaceProof("sin_alterar")
        return InterfaceProof("sobrevive", mode)
    return _failure_objection(mode, case, driver)


def _failure_objection(
    mode: str, case: TestCaseResult, driver: Sequence[str]
) -> InterfaceProof | None:
    """Why a failure with an altered result doesn't prove the test, or None when it does."""
    kind, step = case.failure_kind, case.failed_step
    own = kind == ASSERTION_KIND and path_within(case.frame, driver)
    if (own and step == CHECKING_STEP) or (kind == IMPOSTOR_KIND and step in ALTERED_STEPS):
        return None
    if own:
        return InterfaceProof("fuera_del_entonces", _outside_the_then(mode, step, case.message))
    if kind == IMPOSTOR_KIND:
        return InterfaceProof("no_aserta", f"el error del impostor {_STEP_PLACE[step]}")
    where = case.frame or case.origin
    place = f"en {where}" if where else "fuera del proyecto"
    if kind == ASSERTION_KIND:
        return InterfaceProof("no_aserta", f"una aserción {place}, que no es del driver")
    return InterfaceProof("no_aserta", f"{kind or 'un fallo sin clasificar'} {place}")


def _outside_the_then(mode: str, step: Step | None, message: str | None) -> str:
    """Where the test's own assertion failed, and what it said, cut to one short line."""
    said = " ".join((message or "").split())
    if len(said) > _MESSAGE_LIMIT:
        said = said[: _MESSAGE_LIMIT - 1] + "…"
    return f"una aserción {_STEP_PLACE[step]} (modo {mode}" + (f": {said})" if said else ")")


def interface_proofs(
    scenario_ids: Sequence[str],
    control: Mapping[str, TestCaseResult] | str,
    modes: Mapping[str, Mapping[str, TestCaseResult] | str],
    driver: Sequence[str],
) -> dict[str, InterfaceProof]:
    """Judge every scenario; a run that crashed or didn't map (its reason) proves none of them."""
    if isinstance(control, str):
        return _inconclusive(scenario_ids, control)
    broken = next((run for run in modes.values() if isinstance(run, str)), None)
    if broken is not None:
        return _inconclusive(scenario_ids, broken)
    by_mode = {mode: run for mode, run in modes.items() if not isinstance(run, str)}
    return {
        sid: interface_proof(
            control.get(sid), {mode: run.get(sid) for mode, run in by_mode.items()}, driver
        )
        for sid in scenario_ids
    }


def _inconclusive(scenario_ids: Sequence[str], reason: str) -> dict[str, InterfaceProof]:
    return {sid: InterfaceProof("no_concluyente", reason) for sid in scenario_ids}


# What each outcome tells the driver's agent, from `{head}`, the interface and the detail.
_REASONS: Mapping[str, str] = {
    ONLY_FROM_A_GIVEN: (
        "{head}, pero solo llama a la interfaz en {interface} para preparar el escenario, desde un "
        "Dado o un fixture: el Cuando o el Entonces tienen que llamarla, y el Entonces comprobar "
        "con assert lo que devuelve, con el valor que da su escenario."
    ),
    "no_llama": (
        "{head}, pero no llama a la interfaz en {interface}: tiene que llamarla y comprobar con "
        "assert lo que devuelve, con el valor que da su escenario."
    ),
    "sobrevive": (
        "{head} y sigue pasando cuando la interfaz devuelve otro valor (modo {detail}): no "
        "comprueba el resultado. Compáralo con assert con el valor que da su escenario, tomado "
        "del Gherkin."
    ),
    "no_aserta": (
        "{head}. Cuando la interfaz devuelve otro valor falla, pero con {detail}, no con una "
        "aserción del test: compara con assert el resultado con el valor que da su escenario."
    ),
    "fuera_del_entonces": (
        "{head}. Cuando la interfaz devuelve otro valor falla, pero por {detail}, no de un "
        "Entonces: la comprobación del resultado va en un paso Entonces, con el valor que da su "
        "escenario. Si el escenario espera un error, guárdalo en el Cuando y compáralo en el "
        "Entonces."
    ),
    # The agent reads this when another scenario is retried: it says what to do and nothing of the
    # impostor, or it would add checks for the impostor to catch (F64 → F63). The person gets why.
    "sin_alterar": (
        "{head}. Este test no lo cambies: Lictor no puede comprobarlo, y lo decidirá una persona."
    ),
    "no_concluyente": "{head}, y no se pudo comprobar que su test mire el resultado: {detail}.",
}


def unproved_reason(
    sid: str, test_name: str, proof: InterfaceProof, writable: Sequence[str]
) -> str:
    """Say what the impostor saw of a test it couldn't prove, without claiming more."""
    key = ONLY_FROM_A_GIVEN if proof.detail == ONLY_FROM_A_GIVEN else proof.outcome
    return _REASONS[key].format(
        head=f"El test {test_name} del escenario {sid} pasa con un driver nuevo",
        interface=", ".join(writable) or "el código del proyecto",
        detail=proof.detail,
    )


def unalterable_note(ids: Sequence[str]) -> str:
    """Why the impostor couldn't judge these, for the person who decides: never for the agent."""
    return (
        f"Lictor no pudo comprobar {', '.join(ids)}: lo que la interfaz les devuelve desde el "
        "Cuando y el Entonces no es un número, un texto, una colección, un booleano ni una "
        "excepción, o solo es el None de una orden. Lo que devuelve en los Dados no se altera."
    )


def without_impostor_reason(sid: str, test_name: str) -> str:
    """Say what a profile without an interface impostor knows of a new test that passes."""
    return (
        f"El test {test_name} del escenario {sid} pasa con un driver nuevo, y este perfil no "
        "puede comprobar que mire el resultado: puede que el código ya cumpla el escenario, o "
        "que el test no compruebe nada. Asegúrate de que compara con assert el resultado con el "
        "valor que da su escenario."
    )
