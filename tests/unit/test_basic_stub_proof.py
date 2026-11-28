"""The basic rule of the stubs' impostor, without a plugin (ADR-0021)."""

import pytest

from lictor.domain.basic_stub_proof import (
    BASIC_LIMITS_NOTE,
    BasicOutcome,
    VariantFact,
    ambiguous_variant_reason,
    basic_proof,
    basic_reason,
    basic_verdict,
    carries_mark,
    seal_warning,
    unchecked_basic_note,
    unchecked_text,
    unjudged_reason,
    unmarked_driver_note,
    variant_fact,
)
from lictor.domain.model import TestCaseResult

PASS = VariantFact(passed=True, marked=False)
FAIL = VariantFact(passed=False, marked=False)
MARKED = VariantFact(passed=False, marked=True)


@pytest.mark.parametrize(
    ("red_marked", "variants", "outcome"),
    [
        # Some variant still reaches the mark: whatever else it did.
        (True, [MARKED, PASS], "sigue_sin_implementar"),
        (True, [FAIL, MARKED], "sigue_sin_implementar"),
        # It passes with every constant: the vacuous test and the weak one.
        (True, [PASS, PASS], "sobrevive"),
        # Even when its red lost the mark: only stub lines changed, and it went from fail to pass.
        (False, [PASS, PASS], "sobrevive"),
        # A stub without a result runs once: passing with it is passing with every constant.
        (True, [PASS], "sobrevive"),
        # The result decides it.
        (True, [FAIL, PASS], "probado"),
        (False, [PASS, FAIL], "probado"),
        # It fails with every one: it reached a stub, and no constant is what it expects.
        (True, [FAIL, FAIL], "probado"),
        # Nothing says it reached one: a red of code that already exists.
        (False, [FAIL, FAIL], "sin_stub"),
        # One variant doesn't count: a test failing with the other is judged by it…
        (True, [FAIL, None], "probado"),
        (False, [None, FAIL], "sin_stub"),
        # …but one passing with it isn't: with the missing one it might have failed.
        (True, [PASS, None], "sin_juzgar"),
        (True, [None, None], "sin_juzgar"),
    ],
)
def test_the_basic_rule_judges_by_what_the_constants_did(
    red_marked: bool, variants: list[VariantFact | None], outcome: BasicOutcome
) -> None:
    assert basic_proof(red_marked, variants) == outcome


@pytest.mark.parametrize(
    ("outcome", "verdict"),
    [
        ("probado", "aceptado"),
        ("sin_stub", "sin_comprobar"),
        ("sobrevive", "rechazado"),
        ("sigue_sin_implementar", "rechazado"),
        ("sin_juzgar", "rechazado"),
    ],
)
def test_each_outcome_has_its_verdict(outcome: BasicOutcome, verdict: str) -> None:
    assert basic_verdict(outcome) == verdict


@pytest.mark.parametrize(
    ("case", "marked"),
    [
        (
            TestCaseResult(
                "t", status="failure", message="Failed", details="panic: lictor: no implementado"
            ),
            True,
        ),
        (TestCaseResult("t", status="error", message="Error: lictor: no implementado"), True),
        (
            TestCaseResult(
                "t", status="failure", message="Failed", details="pánico en el escenario"
            ),
            False,
        ),
        (TestCaseResult("t", status="failure"), False),
        (TestCaseResult("t", status="passed", details="lictor: no implementado"), False),
    ],
)
def test_the_mark_is_read_in_the_message_and_the_detail_of_a_failure(
    case: TestCaseResult, marked: bool
) -> None:
    assert carries_mark(case) is marked


def test_a_variant_without_a_case_for_the_test_tells_nothing() -> None:
    assert variant_fact(None) is None
    assert variant_fact(TestCaseResult("t", status="skipped")) is None
    assert variant_fact(TestCaseResult("t")) == PASS
    failed = TestCaseResult("t", status="failure", details="lictor: no implementado")
    assert variant_fact(failed) == MARKED


def test_a_test_that_survives_hears_to_compare_never_the_values() -> None:
    reason = basic_reason("NIF-002", "Test_NIF_002", "sobrevive", ["nif/"])

    assert reason.startswith("El test Test_NIF_002 del escenario NIF-002 pasa también")
    assert "compara el resultado con el valor que da su escenario" in reason
    assert "return" not in reason


def test_a_test_that_still_reaches_the_mark_hears_where_it_goes() -> None:
    reason = basic_reason("NIF-001", "Test_NIF_001", "sigue_sin_implementar", ["nif/", "pkg/"])

    assert "sigue fallando con «lictor: no implementado»" in reason
    assert "de nif/, pkg/" in reason


@pytest.mark.parametrize(("variant", "which"), [(0, "primera"), (1, "segunda")])
def test_a_variant_that_doesnt_count_is_named_by_its_place(variant: int, which: str) -> None:
    reason = unjudged_reason(variant, "La suite terminó con código 2 sin dejar informe.")

    assert reason == (
        f"Con la {which} constante de cada stub no se pudo juzgar la aceptación: cada stub va solo "
        "en su línea, y sus constantes usan solo lo que ya usa su firma.\n"
        "La suite terminó con código 2 sin dejar informe."
    )
    ambiguous = ambiguous_variant_reason(variant, "El escenario 'A-1' sale en 2 casos.")
    assert ambiguous == (
        f"Con la {which} constante de cada stub la aceptación da varios casos de un escenario. "
        "El escenario 'A-1' sale en 2 casos."
    )


def test_what_the_person_reads() -> None:
    assert "solo pide que haya un resultado" in BASIC_LIMITS_NOTE
    assert unchecked_basic_note(["A-1", "A-2"]).startswith(
        "Lictor aceptó A-1, A-2 sin comprobarlos"
    )
    assert unmarked_driver_note(3, wrote_code=False) == (
        "3 tests aceptados sin comprobar: el driver no declaró ningún stub"
    )
    assert unmarked_driver_note(1, wrote_code=True).endswith(
        "; escribió código sin la marca «lictor: no implementado»"
    )


@pytest.mark.parametrize(
    ("why", "start"),
    [
        ("sin_stub", "2 tests aceptados sin comprobar (A-1, A-2): fallan con todas las constantes"),
        ("sin_marca", "2 tests aceptados sin comprobar: el driver no declaró ningún stub"),
        ("sin_marca_con_codigo", "2 tests aceptados sin comprobar: el driver no declaró ningún"),
    ],
)
def test_the_seal_warns_of_what_the_driver_s_constants_left_unchecked(why: str, start: str) -> None:
    warning = seal_warning(why, ["A-1", "A-2"])

    assert warning is not None
    assert warning.startswith(start)
    assert ("escribió código" in warning) is (why == "sin_marca_con_codigo")


@pytest.mark.parametrize(("why", "ids"), [("interfaz", ["A-1"]), ("sin_stub", []), ("", ["A"])])
def test_the_seal_doesn_t_warn_of_what_isn_t_the_driver_s_constants(
    why: str, ids: list[str]
) -> None:
    assert seal_warning(why, ids) is None


def test_the_report_says_why_each_was_accepted_unchecked() -> None:
    assert unchecked_text("sin_stub", ["A-1"]) == unchecked_basic_note(["A-1"])
    assert unchecked_basic_note(["A-1", "A-2"]) == (
        "Lictor aceptó A-1, A-2 sin comprobarlos: fallan con las dos constantes de sus stubs, y su "
        "rojo no llevaba «lictor: no implementado», así que nada dice que llegaran a un stub (un "
        "rojo por código que ya existe, o un helper que pierde el mensaje). Revisa que su "
        "Entonces compare con el valor del escenario."
    )
    assert unchecked_text("sin_marca", ["A-1", "A-2"]) == (
        "Lictor aceptó A-1, A-2 sin comprobarlos: el driver no declaró ningún stub con «lictor: "
        "no implementado» y sus constantes, así que nada los puso a prueba. Revisa que su "
        "Entonces compare con el valor del escenario."
    )
