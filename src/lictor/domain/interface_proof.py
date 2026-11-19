"""Whether a new test that passes with a fresh driver checks what the interface returns (ADR-0011).

With a fresh driver, a new scenario that already passes can't be told from a test that checks
nothing. The profile's impostor runs the acceptance again with the interface wrapped: once without
altering anything (the control) and once per mode, altering what it returns to the driver. A test
whose own assertion fails in every mode depends on that value, so the code already met its
scenario. These rules judge what the impostor reported; the plugin decides nothing.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lictor.domain.model import TestCaseResult
from lictor.domain.rules import path_within

ASSERTION_KIND = "assertion"
IMPOSTOR_KIND = "impostor"  # the impostor raised its own error in place of the interface's

ProofOutcome = Literal[
    "probado",  # its own assertion failed in every mode: the code already met the scenario
    "no_llama",  # it never called the interface
    "sobrevive",  # it passed with an altered result: it doesn't check it
    "no_aserta",  # it failed with an altered result, but not by an assertion of the test
    "sin_alterar",  # nothing the interface returned to it could be altered
    "no_concluyente",  # an impostor run told nothing about it
]

# Worst first: what a mode says of the test, when the modes disagree.
_PRIORITY: tuple[ProofOutcome, ...] = (
    "sobrevive",
    "no_aserta",
    "sin_alterar",
    "no_concluyente",
)

# What the driver's agent can fix by writing the test again; the rest only a person decides.
_FIXABLE: frozenset[ProofOutcome] = frozenset({"no_llama", "sobrevive", "no_aserta"})


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

    It must pass in the control having called the interface, and in every mode it must have had a
    result altered and fail by an assertion raised in the driver, or by the impostor's own error.
    """
    if control is None or control.status != "passed":
        state = "no tiene resultado" if control is None else f"acaba en {control.status}"
        return InterfaceProof(
            "no_concluyente", f"con la interfaz envuelta y sin alterar, su test {state}"
        )
    if control.interface_calls == 0:
        return InterfaceProof("no_llama")
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
    if case.interface_altered == 0:
        return InterfaceProof("sin_alterar")
    if case.status == "passed":
        return InterfaceProof("sobrevive", mode)
    kind = case.failure_kind
    if kind == IMPOSTOR_KIND or (kind == ASSERTION_KIND and path_within(case.frame, driver)):
        return None
    where = case.frame or case.origin
    place = f"en {where}" if where else "fuera del proyecto"
    if kind == ASSERTION_KIND:
        return InterfaceProof("no_aserta", f"una aserción {place}, que no es del driver")
    return InterfaceProof("no_aserta", f"{kind or 'un fallo sin clasificar'} {place}")


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


def unproved_reason(
    sid: str, test_name: str, proof: InterfaceProof, writable: Sequence[str]
) -> str:
    """Say what the impostor saw of a test it couldn't prove, without claiming more."""
    head = f"El test {test_name} del escenario {sid} pasa con un driver nuevo"
    interface = ", ".join(writable) or "el código del proyecto"
    match proof.outcome:
        case "no_llama":
            return (
                f"{head}, pero no llama a la interfaz en {interface}: tiene que llamarla y "
                "comprobar con assert lo que devuelve, con el valor que da su escenario."
            )
        case "sobrevive":
            return (
                f"{head} y sigue pasando cuando la interfaz devuelve otro valor (modo "
                f"{proof.detail}): no comprueba el resultado. Compáralo con assert con el valor "
                "que da su escenario, tomado del Gherkin."
            )
        case "no_aserta":
            return (
                f"{head}. Cuando la interfaz devuelve otro valor falla, pero con {proof.detail}, "
                "no con una aserción del test: compara con assert el resultado con el valor que "
                "da su escenario."
            )
        case "sin_alterar":
            return (
                f"{head}, y Lictor no puede comprobar que mire el resultado: lo que le devuelve "
                "la interfaz no es un número, un texto, una colección, un booleano ni una "
                "excepción, así que no se puede alterar."
            )
        case _:
            return f"{head}, y no se pudo comprobar que su test mire el resultado: {proof.detail}."


def without_impostor_reason(sid: str, test_name: str) -> str:
    """Say what a profile without an interface impostor knows of a new test that passes."""
    return (
        f"El test {test_name} del escenario {sid} pasa con un driver nuevo, y este perfil no "
        "puede comprobar que mire el resultado: puede que el código ya cumpla el escenario, o "
        "que el test no compruebe nada. Asegúrate de que compara con assert el resultado con el "
        "valor que da su escenario."
    )
