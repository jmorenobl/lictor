"""Whether a fresh driver's new pending test reads what its stubs return (ADR-0021).

The red check runs the acceptance once per variant, with every declared stub replaced by its
constant (`declared_stubs`). Without the plugin's classes nothing says how a test failed, only
whether it passed and whether its failure still carries Lictor's mark (`lictor: no implementado`).
That is enough, judged in this order:

- a variant where it still carries the mark: it reaches a stub nobody replaced;
- it passes with every constant: what the stubs return doesn't decide it;
- it passes with one and fails with another: the result decides it;
- it fails with every one: proved if its red carried the mark (it reached a stub, and no constant
  is what it expects), unchecked if not (nothing says it reached one).

A variant that doesn't count (no trusted report) leaves a test that passes with the other one
unjudged: with the missing one it might have failed. These rules judge facts; computing them from
the reports is the application's.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from lictor.domain.declared_stubs import IMPOSTOR_MARK, STUB_MARK
from lictor.domain.model import TestCaseResult

BasicOutcome = Literal[
    "probado",  # the constants decide it
    "sobrevive",  # it passes with every constant
    "sigue_sin_implementar",  # some variant still reached the mark
    "sin_stub",  # it fails with every constant, and nothing says it reached a stub
    "sin_juzgar",  # it passes with the only variant that counts, or none counts
]

BasicVerdict = Literal["aceptado", "sin_comprobar", "rechazado"]

# What rejects one test: every outcome whose verdict is `rechazado`.
TestRejection = Literal["sobrevive", "sigue_sin_implementar", "sin_juzgar"]


@dataclass(frozen=True)
class VariantFact:
    """What one variant's trusted report said of a test."""

    passed: bool
    marked: bool  # its failure still carries the stub's mark


def carries_mark(case: TestCaseResult) -> bool:
    """Whether a failed case's text, as the report gives it, carries the stub's mark."""
    if case.status == "passed":
        return False
    return any(STUB_MARK in text for text in (case.message, case.details) if text)


def variant_fact(case: TestCaseResult | None) -> VariantFact | None:
    """The fact of one variant that counts; a test it has no case for tells nothing.

    A test that skips itself with a constant doesn't fail with it: what it does with the result
    doesn't decide it, whatever the runner calls it (Fable's B1 of the 0.24.0's PR).
    """
    if case is None:
        return None
    return VariantFact(passed=case.status in _NOT_FAILED, marked=carries_mark(case))


_NOT_FAILED: Final = ("passed", "skipped")


def basic_proof(red_marked: bool, variants: Sequence[VariantFact | None]) -> BasicOutcome:
    """Judge one test by its red and what each variant said (None: that variant doesn't count)."""
    counted = _counted(variants)
    if not counted:
        return "sin_juzgar"
    if any(v.marked for v in counted):
        return "sigue_sin_implementar"
    passed = {v.passed for v in counted}
    if len(passed) > 1:
        return "probado"
    if True in passed:
        return _passing_every_one(every_one_counted=len(counted) == len(variants))
    return "probado" if red_marked else "sin_stub"


def _counted(variants: Sequence[VariantFact | None]) -> list[VariantFact]:
    return [v for v in variants if v is not None]


def _passing_every_one(*, every_one_counted: bool) -> BasicOutcome:
    """A test that passes with every variant that counts: with one missing it might have failed."""
    return "sobrevive" if every_one_counted else "sin_juzgar"


def basic_verdict(outcome: BasicOutcome) -> BasicVerdict:
    """Accept, accept unchecked, or reject."""
    if outcome == "probado":
        return "aceptado"
    if outcome == "sin_stub":
        return "sin_comprobar"
    return "rechazado"


_CONSTANTS = f"las constantes de «{IMPOSTOR_MARK}»"

# What each rejection tells the driver's agent. None names a value: told one, the real agent
# checked the type of every result to tell Lictor's apart (ADR-0012, point 5).
_REASONS: Final[dict[TestRejection, str]] = {
    "sobrevive": (
        "{head} pasa también cuando sus stubs devuelven " + _CONSTANTS + " en lugar de fallar: "
        "compara el resultado con el valor que da su escenario. Esos valores los pone Lictor para "
        "comprobar el test: no los trates aparte."
    ),
    "sigue_sin_implementar": (
        "{head} sigue fallando con «"
        + STUB_MARK
        + "» con sus stubs sustituidos por "
        + _CONSTANTS
        + ": esa marca va solo en la línea de un stub de {writable}, con sus constantes al final."
    ),
    # Which variant told nothing, and why, follows apart: the same for every test it left.
    "sin_juzgar": (
        "{head} pasa con las constantes que se pudieron ejecutar, y sin las demás no se puede "
        "juzgar."
    ),
}


def basic_reason(sid: str, test_name: str, outcome: TestRejection, writable: Sequence[str]) -> str:
    """Why a test was rejected, for the driver's agent."""
    return _REASONS[outcome].format(
        head=f"El test {test_name} del escenario {sid}", writable=", ".join(writable)
    )


def unjudged_reason(variant: int, why: str) -> str:
    """Why a variant left tests unjudged: which one (never its value) and what happened."""
    return (
        f"{_with(variant)} no se pudo juzgar la aceptación: cada stub va solo en su línea, y sus "
        f"constantes usan solo lo que ya usa su firma.\n{why}"
    )


def ambiguous_variant_reason(variant: int, why: str) -> str:
    """Why a variant whose report maps a scenario to several cases rejects the driver (G1)."""
    return f"{_with(variant)} la aceptación da varios casos de un escenario. {why}"


def _with(variant: int) -> str:
    return f"Con la {'primera' if variant == 0 else 'segunda'} constante de cada stub"


# For the person, once the driver's attempts run out: what these constants can't tell.
BASIC_LIMITS_NOTE: Final = (
    "Si alguno de estos tests ya es correcto, Lictor no puede comprobarlo con dos constantes: un "
    "escenario que solo pide que haya un resultado (que exista, que no esté vacío, que sea "
    "positivo) pasa con las dos. Pon en su escenario un valor con el que comparar, o que su "
    "Entonces lo consulte por la interfaz."
)


def unchecked_basic_note(ids: Sequence[str]) -> str:
    """Why these were sealed without a proof, for the person: the agent never reads it."""
    return (
        f"Lictor aceptó {', '.join(ids)} sin comprobarlos: fallan con las dos constantes de sus "
        f"stubs, y su rojo no llevaba «{STUB_MARK}», así que nada dice que llegaran a un stub "
        "(un rojo por código que ya existe, o un helper que pierde el mensaje). Revisa que su "
        "Entonces compare con el valor del escenario."
    )


def unmarked_driver_note(count: int, wrote_code: bool) -> str:
    """What the seal warns of a driver that declared no stub: its new tests weren't checked."""
    note = f"{count} tests aceptados sin comprobar: el driver no declaró ningún stub"
    if wrote_code:
        note += f"; escribió código sin la marca «{STUB_MARK}»"
    return note


# Why the seal accepted some tests unchecked, as `driver.sealed` keeps it (`unchecked_why`).
UNCHECKED_BY_INTERFACE: Final = "interfaz"
UNCHECKED_WITHOUT_STUB: Final = "sin_stub"
UNMARKED: Final = "sin_marca"
UNMARKED_WITH_CODE: Final = "sin_marca_con_codigo"


def seal_warning(why: object, ids: Sequence[str]) -> str | None:
    """The line the seal warns with, when the driver's own constants left tests unchecked."""
    if not ids:
        return None
    if why == UNCHECKED_WITHOUT_STUB:
        return (
            f"{len(ids)} tests aceptados sin comprobar ({', '.join(ids)}): fallan con todas las "
            f"constantes de sus stubs, y su rojo no llevaba «{STUB_MARK}»"
        )
    if why in (UNMARKED, UNMARKED_WITH_CODE):
        return unmarked_driver_note(len(ids), why == UNMARKED_WITH_CODE)
    return None


def unchecked_text(why: str, ids: Sequence[str]) -> str:
    """What the report says of the tests a seal accepted unchecked, by why it couldn't check."""
    if why == UNCHECKED_WITHOUT_STUB:
        return unchecked_basic_note(ids)
    return (
        f"Lictor aceptó {', '.join(ids)} sin comprobarlos: el driver no declaró ningún stub con "
        f"«{STUB_MARK}» y sus constantes, así que nada los puso a prueba. Revisa que su Entonces "
        "compare con el valor del escenario."
    )


# For the person, beside each quarantine: what the stubs left in the branch look like.
QUARANTINED_STUBS_NOTE: Final = (
    f"Los stubs de un escenario en cuarentena siguen en la rama con la marca «{STUB_MARK}» y sus "
    f"constantes («{IMPOSTOR_MARK}»): se quitan al implementarlo."
)


def driver_exempt_note(control_ids: Sequence[str]) -> str:
    """The controls a person exempted from the driver's commit (G3): the report names them."""
    names = ", ".join(f"`{cid}`" for cid in control_ids)
    return (
        f"El commit del driver no pasó {names} antes del sello: lictor.json los marca con "
        '"driver": false.'
    )
