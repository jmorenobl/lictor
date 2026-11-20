"""The rule that judges what the impostor of the interface reported (ADR-0011, with B)."""

from dataclasses import replace
from typing import Literal

import pytest

from lictor.domain.interface_proof import (
    ALTERED_STEPS,
    InterfaceProof,
    interface_proof,
    interface_proofs,
    unalterable_note,
    unproved_reason,
    without_impostor_reason,
)
from lictor.domain.model import TestCaseResult

DRIVER = ("tests/acceptance/",)
OWN = "tests/acceptance/test_calc.py"

Status = Literal["passed", "failure", "error", "skipped"]


_FAILURE_PROPERTIES = {"kind": "lictor.failure", "frame": "lictor.frame", "step": "lictor.step"}


def _case(
    status: Status = "passed",
    *,
    calls: dict[str, int] | None = None,
    altered: dict[str, int] | None = None,
    altered_none: int = 0,
    **failure: str,
) -> TestCaseResult:
    """A test as the plugins report it; by default it calls and gets altered in its Then.

    `failure` gives its `kind`, `frame` and `step`; a count `fuera` is one outside any step.
    """
    calls = {"then": 1} if calls is None else calls
    altered = {"then": 1} if altered is None else altered
    properties = {
        "lictor.interface.calls": str(sum(calls.values())),
        "lictor.interface.altered": str(sum(altered.values())),
        "lictor.interface.altered_none": str(altered_none),
    }
    for name, counts in (("calls", calls), ("altered", altered)):
        properties |= {
            f"lictor.interface.{name}.{s}": str(n) for s, n in counts.items() if s != "fuera"
        }
    properties |= {_FAILURE_PROPERTIES[key]: value for key, value in failure.items() if value}
    return TestCaseResult("test_calc_001", status=status, properties=properties)


def _failed(kind: str, step: str, frame: str = OWN, message: str | None = None) -> TestCaseResult:
    case = _case("failure", kind=kind, frame=frame, step=step)
    return replace(case, message=message)


ASSERTED = _failed("assertion", "then")
IMPOSTOR = _failed("impostor", "when", frame="src/calc/__init__.py")
PASSED = _case("passed")


def _modes(
    more: TestCaseResult | None, less: TestCaseResult | None
) -> dict[str, TestCaseResult | None]:
    return {"mas": more, "menos": less}


def test_the_impostor_alters_only_what_the_when_does_and_the_then_reads() -> None:
    assert ALTERED_STEPS == ("when", "then")


def test_a_test_whose_own_assertion_in_a_then_fails_in_every_mode_is_proved() -> None:
    proof = interface_proof(PASSED, _modes(ASSERTED, ASSERTED), DRIVER)

    assert proof == InterfaceProof("probado")
    assert proof.proved is True


@pytest.mark.parametrize("step", ["when", "then"])
def test_the_impostor_s_own_error_counts_from_the_when_on(step: str) -> None:
    """A rejection kept in the When, or `pytest.raises` of the right class in the Then."""
    raised = _failed("impostor", step, frame="src/calc/__init__.py")

    assert interface_proof(PASSED, _modes(ASSERTED, raised), DRIVER).proved is True


# Every kind of failure, in every place it can be raised: what a mode with a result altered says.
@pytest.mark.parametrize(
    ("kind", "step", "frame", "outcome", "detail"),
    [
        ("assertion", "then", OWN, None, ""),
        ("assertion", "when", OWN, "fuera_del_entonces", "una aserción de un Cuando (modo mas)"),
        ("assertion", "given", OWN, "fuera_del_entonces", "una aserción de un Dado (modo mas)"),
        ("assertion", "", OWN, "fuera_del_entonces", "una aserción fuera de los pasos (modo mas)"),
        (
            "assertion",
            "then",
            "src/calc/__init__.py",
            "no_aserta",
            "una aserción en src/calc/__init__.py, que no es del driver",
        ),
        (
            "assertion",
            "then",
            "",
            "no_aserta",
            "una aserción fuera del proyecto, que no es del driver",
        ),
        ("impostor", "when", "src/c.py", None, ""),
        ("impostor", "then", "src/c.py", None, ""),
        ("impostor", "given", "src/c.py", "no_aserta", "el error del impostor de un Dado"),
        ("impostor", "", "src/c.py", "no_aserta", "el error del impostor fuera de los pasos"),
        ("error", "then", OWN, "no_aserta", f"error en {OWN}"),
        ("error", "when", OWN, "no_aserta", f"error en {OWN}"),
        ("", "then", OWN, "no_aserta", f"un fallo sin clasificar en {OWN}"),
    ],
)
def test_what_each_failure_says_wherever_it_is_raised(
    kind: str, step: str, frame: str, outcome: str | None, detail: str
) -> None:
    failed = _failed(kind, step, frame)

    proof = interface_proof(PASSED, _modes(failed, ASSERTED), DRIVER)

    assert proof == (
        InterfaceProof("probado") if outcome is None else InterfaceProof(outcome, detail)
    )


def test_an_assertion_outside_the_then_says_what_it_said_and_can_be_fixed() -> None:
    """`pytest.raises` in the When while the Then reads the exception: no mode reaches the Then."""
    failed = _failed("assertion", "when", message="DID NOT RAISE <class 'calc.ErrorDeCalculo'>")

    proof = interface_proof(PASSED, _modes(failed, failed), DRIVER)

    assert proof == InterfaceProof(
        "fuera_del_entonces",
        "una aserción de un Cuando (modo mas: DID NOT RAISE <class 'calc.ErrorDeCalculo'>)",
    )
    assert proof.fixable is True


def test_what_an_assertion_said_goes_on_one_short_line() -> None:
    failed = _failed("assertion", "when", message="assert 7\n  where 7 = f()\n" + "x" * 300)

    detail = interface_proof(PASSED, _modes(failed, failed), DRIVER).detail

    assert "\n" not in detail
    assert detail.startswith("una aserción de un Cuando (modo mas: assert 7 where 7 = f() xxx")
    assert detail.endswith("…)")
    assert len(detail) < 240


@pytest.mark.parametrize(
    ("more", "less"),
    [
        # No assert, or a weak one: `assert x`, `is not None`, `isinstance(x, float)`.
        (PASSED, PASSED),
        # `assert x != 5` or `assert x < 7`: one mode is enough to show it isn't checked.
        (ASSERTED, PASSED),
        # `pytest.raises(Exception)` catches the impostor's error and passes.
        (PASSED, ASSERTED),
    ],
)
def test_a_test_that_passes_in_some_mode_survives(
    more: TestCaseResult, less: TestCaseResult
) -> None:
    proof = interface_proof(PASSED, _modes(more, less), DRIVER)

    assert proof.outcome == "sobrevive"
    assert proof.fixable is True


def test_surviving_names_the_mode_it_passed_in() -> None:
    assert interface_proof(PASSED, _modes(ASSERTED, PASSED), DRIVER).detail == "menos"


def test_a_test_that_never_called_the_interface_is_told_so() -> None:
    proof = interface_proof(_case(calls={}), _modes(PASSED, PASSED), DRIVER)

    assert proof == InterfaceProof("no_llama")
    assert proof.fixable is True


def test_a_test_that_calls_only_to_prepare_is_told_to_call_from_the_when_or_the_then() -> None:
    """The Given computes the result and the Then compares it: nothing the scenario does is seen."""
    control = _case(calls={"given": 2, "fuera": 1})

    proof = interface_proof(control, _modes(PASSED, PASSED), DRIVER)

    assert proof == InterfaceProof("no_llama", "dado")
    assert proof.fixable is True


def test_a_call_count_that_isn_t_one_counts_as_no_call() -> None:
    forged = TestCaseResult("t", properties={"lictor.interface.calls": "dos"})

    assert interface_proof(forged, _modes(ASSERTED, ASSERTED), DRIVER).outcome == "no_llama"


def test_nothing_altered_from_the_when_on_is_no_proof_and_no_retry_changes_it() -> None:
    """`assert r.vence == date(...)` on an object: the impostor can't alter it."""
    untouched = _case("passed", altered={})

    proof = interface_proof(PASSED, _modes(untouched, untouched), DRIVER)

    assert proof == InterfaceProof("sin_alterar")
    assert proof.fixable is False


def test_an_order_whose_none_was_all_it_altered_is_no_proof_either() -> None:
    """`Cuando ana paga` returns None: a test that ignores it can't be told from a good one."""
    order = _case("passed", altered={"when": 1}, altered_none=1)

    proof = interface_proof(PASSED, _modes(order, order), DRIVER)

    assert proof == InterfaceProof("sin_alterar")


def test_an_order_beside_a_result_it_ignores_survives() -> None:
    both = _case("passed", altered={"when": 1, "then": 1}, altered_none=1)

    assert interface_proof(PASSED, _modes(both, both), DRIVER) == InterfaceProof("sobrevive", "mas")


def test_an_order_s_none_checked_in_the_then_proves_it() -> None:
    """`assert resultado is None` in a Then is a check: the sentinel isn't None."""
    caught = _case(
        "failure", altered={"then": 1}, altered_none=1, kind="assertion", frame=OWN, step="then"
    )

    assert interface_proof(PASSED, _modes(caught, caught), DRIVER).proved is True


@pytest.mark.parametrize("where", ["given", "fuera"])
def test_a_mode_that_altered_what_a_given_prepares_tells_nothing(where: str) -> None:
    """The rule takes for granted that a Given stays real: a plugin that broke it proves nothing."""
    broken = _case("failure", altered={where: 1}, kind="assertion", frame=OWN, step="then")

    proof = interface_proof(PASSED, _modes(broken, ASSERTED), DRIVER)

    assert proof == InterfaceProof(
        "no_concluyente", "en el modo mas el impostor alteró lo que devuelve un Dado o un fixture"
    )
    assert proof.fixable is False


def test_a_mode_that_altered_nothing_doesn_t_count_as_failing_either() -> None:
    failed_unaltered = _case("failure", altered={}, kind="assertion", frame=OWN, step="then")

    assert interface_proof(PASSED, _modes(ASSERTED, failed_unaltered), DRIVER).proved is False


@pytest.mark.parametrize(
    ("control", "detail"),
    [
        (None, "su test no tiene resultado"),
        (_case("failure", kind="error"), "su test acaba en failure"),
    ],
)
def test_a_control_that_doesn_t_pass_proves_nothing(
    control: TestCaseResult | None, detail: str
) -> None:
    proof = interface_proof(control, _modes(ASSERTED, ASSERTED), DRIVER)

    assert proof == InterfaceProof(
        "no_concluyente", f"con la interfaz envuelta y sin alterar, {detail}"
    )
    assert proof.fixable is False


@pytest.mark.parametrize("missing", [None, _case("skipped")])
def test_a_mode_without_a_result_proves_nothing(missing: TestCaseResult | None) -> None:
    proof = interface_proof(PASSED, _modes(ASSERTED, missing), DRIVER)

    assert proof == InterfaceProof("no_concluyente", "en el modo menos su test no tiene resultado")


def test_without_modes_nothing_is_proved() -> None:
    assert interface_proof(PASSED, {}, DRIVER) == InterfaceProof(
        "no_concluyente", "el perfil no declara ningún modo"
    )


def test_the_worst_reason_wins_when_the_modes_disagree() -> None:
    untouched = _case("passed", altered={})
    attribute = _failed("error", "then")
    when = _failed("assertion", "when")

    def outcome(more: TestCaseResult | None, less: TestCaseResult | None) -> str:
        return interface_proof(PASSED, _modes(more, less), DRIVER).outcome

    assert outcome(untouched, PASSED) == "sobrevive"
    assert outcome(attribute, PASSED) == "sobrevive"
    assert outcome(when, attribute) == "no_aserta"
    assert outcome(untouched, when) == "fuera_del_entonces"
    assert outcome(None, untouched) == "sin_alterar"


def test_every_scenario_is_judged_by_its_own_case() -> None:
    proofs = interface_proofs(
        ["A", "B"],
        {"A": PASSED, "B": PASSED},
        {"mas": {"A": ASSERTED, "B": PASSED}, "menos": {"A": IMPOSTOR, "B": ASSERTED}},
        DRIVER,
    )

    assert proofs["A"].proved is True
    assert proofs["B"].outcome == "sobrevive"


@pytest.mark.parametrize(
    ("control", "modes"),
    [
        ("la suite cayó", {"mas": {"A": ASSERTED}, "menos": {"A": ASSERTED}}),
        ({"A": PASSED}, {"mas": {"A": ASSERTED}, "menos": "la suite cayó"}),
    ],
)
def test_a_run_that_crashed_proves_none_of_them(
    control: dict[str, TestCaseResult] | str, modes: dict[str, dict[str, TestCaseResult] | str]
) -> None:
    proofs = interface_proofs(["A", "B"], control, modes, DRIVER)

    assert {proof.outcome for proof in proofs.values()} == {"no_concluyente"}
    assert proofs["B"].detail == "la suite cayó"


_HEAD = "El test test_calc_001 del escenario CALC-001 pasa con un driver nuevo"


@pytest.mark.parametrize(
    ("proof", "expected"),
    [
        (
            InterfaceProof("no_llama"),
            f"{_HEAD}, pero no llama a la interfaz en src/, lib/: tiene que llamarla y comprobar "
            "con assert lo que devuelve, con el valor que da su escenario.",
        ),
        (
            InterfaceProof("no_llama", "dado"),
            f"{_HEAD}, pero solo llama a la interfaz en src/, lib/ para preparar el escenario, "
            "desde un Dado o un fixture: el Cuando o el Entonces tienen que llamarla, y el "
            "Entonces comprobar con assert lo que devuelve, con el valor que da su escenario.",
        ),
        (
            InterfaceProof("sobrevive", "mas"),
            f"{_HEAD} y sigue pasando cuando la interfaz devuelve otro valor (modo mas): no "
            "comprueba el resultado. Compáralo con assert con el valor que da su escenario, "
            "tomado del Gherkin.",
        ),
        (
            InterfaceProof("no_aserta", "error en t.py"),
            f"{_HEAD}. Cuando la interfaz devuelve otro valor falla, pero con error en t.py, no "
            "con una aserción del test: compara con assert el resultado con el valor que da su "
            "escenario.",
        ),
        (
            InterfaceProof("fuera_del_entonces", "una aserción de un Cuando (modo mas)"),
            f"{_HEAD}. Cuando la interfaz devuelve otro valor falla, pero por una aserción de un "
            "Cuando (modo mas), no de un Entonces: la comprobación del resultado va en un paso "
            "Entonces, con el valor que da su escenario. Si el escenario espera un error, "
            "guárdalo en el Cuando y compáralo en el Entonces.",
        ),
        (
            InterfaceProof("sin_alterar"),
            f"{_HEAD}. Este test no lo cambies: Lictor no puede comprobarlo, y lo decidirá una "
            "persona.",
        ),
        (
            InterfaceProof("no_concluyente", "la suite cayó"),
            f"{_HEAD}, y no se pudo comprobar que su test mire el resultado: la suite cayó.",
        ),
    ],
)
def test_each_reason_says_only_what_was_seen(proof: InterfaceProof, expected: str) -> None:
    assert unproved_reason("CALC-001", "test_calc_001", proof, ["src/", "lib/"]) == expected


def test_what_the_agent_reads_of_a_test_it_mustn_t_change_says_nothing_of_the_impostor() -> None:
    """Told what can't be altered, an agent that obeys adds checks for it to alter (F64 → F63)."""
    reason = unproved_reason("A", "t", InterfaceProof("sin_alterar"), ["src/"])

    for word in ("alterar", "Dado", "número", "assert"):
        assert word not in reason


def test_the_person_is_told_why_the_impostor_couldn_t_judge_them() -> None:
    assert unalterable_note(["PRE-004", "REN-001"]) == (
        "Lictor no pudo comprobar PRE-004, REN-001: lo que la interfaz les devuelve desde el "
        "Cuando y el Entonces no es un número, un texto, una colección, un booleano ni una "
        "excepción, o solo es el None de una orden. Lo que devuelve en los Dados no se altera."
    )


def test_without_writable_paths_the_reason_names_the_project_s_code() -> None:
    reason = unproved_reason("A", "t", InterfaceProof("no_llama"), [])

    assert "no llama a la interfaz en el código del proyecto:" in reason


def test_a_detail_with_braces_is_written_as_it_came() -> None:
    reason = unproved_reason("A", "t", InterfaceProof("no_aserta", "error en {x}.py"), [])

    assert "con error en {x}.py, no" in reason


def test_a_profile_without_the_impostor_claims_nothing() -> None:
    assert without_impostor_reason("CALC-001", "test_calc_001") == (
        "El test test_calc_001 del escenario CALC-001 pasa con un driver nuevo, y este perfil no "
        "puede comprobar que mire el resultado: puede que el código ya cumpla el escenario, o que "
        "el test no compruebe nada. Asegúrate de que compara con assert el resultado con el valor "
        "que da su escenario."
    )


@pytest.mark.parametrize(("length", "kept"), [(200, "x" * 200), (201, "x" * 199 + "…")])
def test_what_an_assertion_said_is_cut_at_200_characters(length: int, kept: str) -> None:
    failed = _failed("assertion", "when", message="x" * length)

    detail = interface_proof(PASSED, _modes(failed, failed), DRIVER).detail

    assert detail == f"una aserción de un Cuando (modo mas: {kept})"
