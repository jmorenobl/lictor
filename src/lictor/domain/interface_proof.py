"""Whether a new test that passes with a fresh driver checks what the interface returns (ADR-0011).

With a fresh driver, a new scenario that already passes can't be told from a test that checks
nothing. The profile's impostor runs the acceptance again with the interface wrapped: once without
altering anything (the control) and once per mode, altering what it returns to the driver from the
steps that act and check (`ALTERED_STEPS`), never what a Given prepares. A test whose own
assertion in a Then fails in every mode, or that the impostor's error stops from the When on,
depends on that value, so the code already met its scenario. These rules judge what the impostor
reported; the plugin decides nothing.

A mode whose only failure is an exception the impostor swallowed or replaced, at a call that didn't
raise in the control, proves nothing: the value it altered went back to the interface and made it
raise (`provocada`). With the impostor of the Gherkin (`gherkin_proof`, ADR-0016) the verdict also
weighs the scenario's value: a Then that receives it and never reads it is refused first, and a
test the interface can't prove that reads every value goes to the person (`solo_el_valor`).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from lictor.domain.gherkin_proof import GherkinFacts, driver_statements, reads_what_broke
from lictor.domain.model import Statement, Step, TestCaseResult, UnreadValue
from lictor.domain.rules import path_within

ASSERTION_KIND = "assertion"
ERROR_KIND = "error"  # any other exception: the test's code couldn't go on with what it got
IMPOSTOR_KIND = "impostor"  # the impostor raised its own error in place of the interface's

# What the impostor alters, told to the plugin: what the When does and what the Then reads. What a
# Given or a fixture gets stays real, so a check there proves nothing of the scenario's result.
ALTERED_STEPS: tuple[Step, ...] = ("when", "then")
CHECKING_STEP: Step = "then"  # the only step whose assertion proves the test checks the result
STEP_PLACE: Mapping[Step | None, str] = {
    "given": "de un Dado",
    "when": "de un Cuando",
    "then": "de un Entonces",
    None: "fuera de los pasos",
}
_MESSAGE_LIMIT = 200

ProofOutcome = Literal[
    "probado",  # its own assertion failed in every mode: the code already met the scenario
    "no_llama",  # it never called the interface from a When or a Then
    "a_traves",  # it reached it from a When or a Then only through a library, never altered
    "sobrevive",  # it passed with an altered result: it doesn't check it
    "no_aserta",  # it failed with an altered result, but not by an assertion of the test
    "fuera_del_entonces",  # it failed by an assertion of the driver, but outside a Then
    "sin_alterar",  # nothing the interface returned to it from the When on could be altered
    "no_concluyente",  # an impostor run told nothing about it
    "provocada",  # a value it altered went back to the interface and made it raise
    "ignora_el_valor",  # a Then receives a value of the scenario and never reads it (ADR-0016)
    "solo_el_valor",  # it reads each value of the scenario, but nothing it checks is the code's
    "ilegible",  # a Then couldn't read the altered result, and the scenario gives it no value
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
    {"no_llama", "sobrevive", "no_aserta", "fuera_del_entonces", "ignora_el_valor"}
)
# What a provoked exception overrides: a mode that failed, or passed, only because of it.
_PROVOKABLE: frozenset[ProofOutcome] = frozenset({"probado", "no_aserta", "sobrevive"})
# What the Gherkin may turn into `solo_el_valor`: a test whose Then calls the interface and reads
# every value, but that the interface couldn't prove. A provoked exception, or a run that told
# nothing, leaves nothing to weigh; an assertion of the driver outside the Then caught the result,
# and moving it to the Then proves the test, so that one stays as the agent's to fix.
_ONLY_THE_VALUE: frozenset[ProofOutcome] = frozenset(
    {"sobrevive", "no_aserta", "sin_alterar", "a_traves"}
)
# The detail of a test proved because the statement that compares its scenario's value couldn't
# read the altered result (F85): the same assertion depends on both.
SAME_READING = "misma_lectura"
ONLY_FROM_A_GIVEN = "dado"  # the detail of a test that calls the interface only to prepare
HANDED = "entrega"  # the detail of one that hands the interface to a library, which calls it


@dataclass(frozen=True)
class InterfaceImpostor:
    """How the profile's impostor is told what to do: one variable, a control and its modes."""

    variable: str
    control: str
    modes: tuple[str, ...]
    gherkin: str | None = None  # the variable of the impostor of the Gherkin, told the same modes


@dataclass(frozen=True)
class InterfaceProof:
    """What the impostor proved of one scenario, and why when it proved nothing."""

    outcome: ProofOutcome
    detail: str = ""
    libraries: tuple[str, ...] = ()  # those the test reached the interface through, if any
    # With `no_aserta`, when every mode that objected failed with an error raised in a Then of
    # the driver: the statements it was raised in, by mode (F85). Empty otherwise.
    broke_at: tuple[tuple[Statement, ...], ...] = ()

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
    if control.interface_by_step("calls").from_when == 0:
        return _not_called(control)
    if not modes:
        return InterfaceProof("no_concluyente", "el perfil no declara ningún modo")
    judged = _worst_objection(modes, driver)
    if judged.outcome not in _PROVOKABLE:
        return judged
    provoked = (provoked_in(mode, case, control) for mode, case in modes.items())
    where = next((found for found in provoked if found), "")
    return InterfaceProof("provocada", where) if where else judged


def _worst_objection(
    modes: Mapping[str, TestCaseResult | None], driver: Sequence[str]
) -> InterfaceProof:
    """What the modes say against the test, the worst first; none that object proves it."""
    objections = [
        objection
        for mode, case in modes.items()
        if (objection := _objection(mode, case, driver)) is not None
    ]
    worst = next(
        (found for outcome in _PRIORITY for found in objections if found.outcome == outcome),
        InterfaceProof("probado"),
    )
    return _with_statements(worst, objections) if worst.outcome == "no_aserta" else worst


def _with_statements(worst: InterfaceProof, objections: Sequence[InterfaceProof]) -> InterfaceProof:
    """Only a test every objecting mode stopped reading in a Then can be weighed by its
    statements (F85): it keeps them all, by mode; any other objection leaves none."""
    every = all(objection.broke_at for objection in objections)
    chains = tuple(chain for objection in objections for chain in objection.broke_at)
    return replace(worst, broke_at=chains if every else ())


def provoked_in(mode: str, case: TestCaseResult | None, control: TestCaseResult) -> str:
    """Where a mode swallowed or replaced an exception the control never raised, or "".

    The call raised only with something altered: a value the impostor gave the driver went back to
    the interface, which a weak test can't tell apart from a check.
    """
    if case is None:
        return ""
    raised = control.interface_raised_at
    found = next((call for call in case.interface_swallowed_at if call[0] not in raised), None)
    return "" if found is None else f"{STEP_PLACE[found[1]]} (modo {mode})"


def weighed_proof(
    proof: InterfaceProof, facts: GherkinFacts | None, control: TestCaseResult | None
) -> InterfaceProof:
    """The interface's verdict weighed with what the impostor of the Gherkin saw (ADR-0016).

    A value of the scenario a Then receives and never reads is refused before anything else,
    since the test can be fixed; a test the interface couldn't prove that reads every value that
    isn't an input, and calls the interface from the When or the Then, goes to the person.
    """
    if facts is None:
        return proof
    if facts.unread:
        return InterfaceProof("ignora_el_valor", unread_text(facts.unread[0]))
    if (by_statements := _weighed_by_statements(proof, facts)) is not None:
        return by_statements
    if (
        proof.outcome in _ONLY_THE_VALUE
        and facts.every_value_read
        and control is not None
        and _calls_from_when(control)
    ):
        return InterfaceProof("solo_el_valor", facts.place, proof.libraries)
    return proof


def _weighed_by_statements(proof: InterfaceProof, facts: GherkinFacts) -> InterfaceProof | None:
    """What a test whose Then couldn't read the altered result is, by where it reads its
    scenario's values (F85); None when that decides nothing.

    Proved when every value is read and each mode broke on the statement that compares one, or
    on one that feeds it: the same assertion depends on both. With nothing of the scenario to
    weigh, it can't be proved, nor told to change.
    """
    if not proof.broke_at:
        return None
    if facts.every_value_read and all(
        any(reads_what_broke(broke, read) for read in facts.read_at) for broke in proof.broke_at
    ):
        return InterfaceProof("probado", SAME_READING)
    if facts.read or facts.inputs or facts.survived or facts.errors:
        return None
    return InterfaceProof("ilegible", proof.detail)


def weighs_the_gherkin(proof: InterfaceProof) -> bool:
    """Whether the scenario's values can change what the interface said of a test."""
    return proof.outcome in _ONLY_THE_VALUE


def _calls_from_when(control: TestCaseResult) -> bool:
    """Whether the When or the Then reached the interface: itself, through a library or handed."""
    return any(control.interface_by_step(kind).from_when for kind in ("calls", "via", "handed"))


def unread_text(unread: UnreadValue) -> str:
    """The step that ignores a value of its scenario, and the value."""
    return f"el Entonces «{unread.step}» recibe «{unread.value}» del escenario"


def _not_called(control: TestCaseResult) -> InterfaceProof:
    """What a test that never called the interface itself from a When or a Then did instead.

    A library may have called it for the test, which the impostor never alters: one the test
    handed the function to can be avoided by calling it from the step, so it is retried; one the
    test entered the project through (a runner that invokes a command) can't, so it is final.
    """
    libraries = control.interface_entries(ALTERED_STEPS)
    if control.interface_by_step("handed").from_when:
        return InterfaceProof("no_llama", HANDED, libraries)
    if control.interface_by_step("via").from_when:
        return InterfaceProof("a_traves", libraries=libraries)
    if control.interface_calls == 0:
        return InterfaceProof("no_llama")
    return InterfaceProof("no_llama", ONLY_FROM_A_GIVEN)


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
    seen = said(f"modo {mode}", case.message)
    if kind == IMPOSTOR_KIND:
        return InterfaceProof("no_aserta", f"el error del impostor {STEP_PLACE[step]} {seen}")
    place = failure_place(case)
    if kind == ASSERTION_KIND:
        return InterfaceProof("no_aserta", f"una aserción {place}, que no es del driver {seen}")
    what = kind or "un fallo sin clasificar"
    detail = f"{what} {STEP_PLACE[step]}, {place} {seen}"
    return InterfaceProof("no_aserta", detail, broke_at=_broke_reading(case, driver))


def _broke_reading(
    case: TestCaseResult, driver: Sequence[str]
) -> tuple[tuple[Statement, ...], ...]:
    """The statements of the driver a Then stopped on with an error, not an assertion (F85)."""
    if case.failure_kind != ERROR_KIND or case.failed_step != CHECKING_STEP:
        return ()
    chain = driver_statements(case, driver)
    return (chain,) if chain else ()


def failure_place(case: TestCaseResult) -> str:
    """Where a failure was raised: the test's own line when the project holds it."""
    if case.frame:
        line = case.frame_line
        return f"en {case.frame}:{line}" if line is not None else f"en {case.frame}"
    return f"en {case.origin}" if case.origin else "fuera del proyecto"


def said(where: str, message: str | None) -> str:
    """Where a failure was seen (`modo mas`), and what it said, cut to one short line."""
    text = " ".join((message or "").split())
    if len(text) > _MESSAGE_LIMIT:
        text = text[: _MESSAGE_LIMIT - 1] + "…"
    return f"({where}" + (f": {text})" if text else ")")


def _outside_the_then(mode: str, step: Step | None, message: str | None) -> str:
    """Where the test's own assertion failed, and what it said."""
    return f"una aserción {STEP_PLACE[step]} {said(f'modo {mode}', message)}"


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
    HANDED: (
        "{head}, pero no llama a la interfaz en {interface} desde sus pasos: se la pasa a "
        "{libraries}, que es quien la llama. Llámala desde el Cuando o el Entonces y comprueba "
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
    # The other value is Lictor's, which a test that trips on it may want to handle apart: it
    # mustn't, or it survives. What it said and where tell which line of the test to change.
    "no_aserta": (
        "{head}. Cuando la interfaz devuelve otro valor falla, pero con {detail}, no con una "
        "aserción del test: compara con assert el resultado con el valor que da su escenario. "
        "Ese otro valor lo pone Lictor para comprobar el test: no lo trates aparte."
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
    # As sin_alterar: the agent reads it when another scenario is retried, and why goes to the
    # person (`through_note`).
    "a_traves": (
        "{head}. Este test no lo cambies: Lictor no puede comprobarlo, y lo decidirá una persona."
    ),
    "no_concluyente": "{head}, y no se pudo comprobar que su test mire el resultado: {detail}.",
    # The value is the scenario's, and the step has it: comparing it is the fix (ADR-0016).
    "ignora_el_valor": (
        "{head}, pero {detail} y no lo usa: compara con assert lo que devuelve la interfaz con "
        "ese valor."
    ),
    # As sin_alterar: the agent reads these when another scenario is retried, and why goes to
    # the person (`only_the_value_note`, `provoked_note`).
    "solo_el_valor": (
        "{head}. Este test no lo cambies: Lictor no puede comprobarlo, y lo decidirá una persona."
    ),
    "provocada": (
        "{head}. Este test no lo cambies: Lictor no puede comprobarlo, y lo decidirá una persona."
    ),
    "ilegible": (
        "{head}. Este test no lo cambies: Lictor no puede comprobarlo, y lo decidirá una persona."
    ),
}


def unproved_reason(
    sid: str,
    test_name: str,
    proof: InterfaceProof,
    writable: Sequence[str],
    passes: str = "pasa con un driver nuevo",
) -> str:
    """Say what the impostor saw of a test it couldn't prove, without claiming more.

    `passes` says when the test passed: with a fresh driver, or with the stubs not implemented.
    """
    key = proof.detail if proof.detail in (ONLY_FROM_A_GIVEN, HANDED) else proof.outcome
    return _REASONS[key].format(
        head=f"El test {test_name} del escenario {sid} {passes}",
        interface=", ".join(writable) or "el código del proyecto",
        detail=proof.detail,
        libraries=libraries_text(proof.libraries),
    )


def libraries_text(libraries: Sequence[str]) -> str:
    """The libraries a test reached the interface through, as the impostor named them."""
    return ", ".join(libraries) or "una librería"


def unalterable_note(ids: Sequence[str]) -> str:
    """Why the impostor couldn't judge these, for the person who decides: never for the agent."""
    return (
        f"Lictor no pudo comprobar {', '.join(ids)}: lo que la interfaz les devuelve desde el "
        "Cuando y el Entonces no es un número, un texto, una fecha, una colección, un booleano "
        "ni una excepción, o solo es el None de una orden. Lo que devuelve en los Dados no se "
        "altera."
    )


def through_note(proofs: Mapping[str, InterfaceProof]) -> str:
    """Why the impostor couldn't judge tests that only reach the interface through a library,
    for the person who decides: never for the agent."""
    each = "; ".join(
        f"{sid}, a través de {libraries_text(proof.libraries)}" for sid, proof in proofs.items()
    )
    return (
        f"Lictor no pudo comprobar {each}: desde el Cuando y el Entonces su test solo llega a la "
        "interfaz a través de esa librería, que es la que llama al código (una orden que ejecuta "
        "un runner, por ejemplo), y Lictor solo altera lo que la interfaz le devuelve al driver."
    )


def only_the_value_note(proofs: Mapping[str, InterfaceProof]) -> str:
    """What the impostors saw of tests that read their scenario's value but not, as far as Lictor
    can tell, the code's: only facts, for the person. A test that compares the value with what a
    Given stored looks the same, so nothing here says the test is right."""
    each = "; ".join(
        sid
        + (f", en {proof.detail}" if proof.detail else "")
        + (f", a través de {libraries_text(proof.libraries)}" if proof.libraries else "")
        for sid, proof in proofs.items()
    )
    return (
        f"Lictor no pudo comprobar {each}: con lo que devuelve la interfaz alterado, su test sigue "
        "pasando o falla sin una aserción de un Entonces; con otro valor del escenario en su "
        "lugar, falla. Lictor no puede probar que lo que compara venga del código: léelo ahí."
    )


def unreadable_result_note(proofs: Mapping[str, InterfaceProof]) -> str:
    """Why the impostor couldn't judge tests whose Then couldn't read the altered result and has
    no value of its scenario to weigh (F85): only facts, for the person."""
    each = "; ".join(f"{sid} ({proof.detail})" for sid, proof in proofs.items())
    return (
        f"Lictor no pudo comprobar {each}: con lo que devuelve la interfaz alterado, su Entonces "
        "no pudo leerlo, y el escenario no le da ningún valor que Lictor pueda cambiar para ver "
        "qué compara. Léelo."
    )


def provoked_note(proofs: Mapping[str, InterfaceProof]) -> str:
    """Why the impostor couldn't judge tests in which a value it altered made the code raise."""
    each = "; ".join(f"{sid}, {proof.detail}" for sid, proof in proofs.items())
    return (
        f"Lictor no pudo comprobar {each}: el valor que alteró volvió a la interfaz y la hizo "
        "lanzar, así que lo que hizo el test después no dice si compara el resultado."
    )


def person_notes(proofs: Mapping[str, InterfaceProof]) -> list[str]:
    """Why the impostor couldn't judge these tests, one note per reason, for the person only."""

    def having(outcome: ProofOutcome) -> dict[str, InterfaceProof]:
        return {sid: proof for sid, proof in proofs.items() if proof.outcome == outcome}

    notes = [unalterable_note(list(found))] if (found := having("sin_alterar")) else []
    notes += [through_note(found)] if (found := having("a_traves")) else []
    notes += [only_the_value_note(found)] if (found := having("solo_el_valor")) else []
    notes += [unreadable_result_note(found)] if (found := having("ilegible")) else []
    return notes + ([provoked_note(found)] if (found := having("provocada")) else [])


def unreadable(proofs: Mapping[str, InterfaceProof]) -> bool:
    """Whether any of these goes to the person because only its scenario's value was proved, or
    an altered value made the code raise: then no hint invites to register them unread."""
    return any(proof.outcome in ("solo_el_valor", "provocada") for proof in proofs.values())


def without_impostor_reason(sid: str, test_name: str) -> str:
    """Say what a profile without an interface impostor knows of a new test that passes."""
    return (
        f"El test {test_name} del escenario {sid} pasa con un driver nuevo, y este perfil no "
        "puede comprobar que mire el resultado: puede que el código ya cumpla el escenario, o "
        "que el test no compruebe nada. Asegúrate de que compara con assert el resultado con el "
        "valor que da su escenario."
    )
