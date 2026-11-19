"""The rule that judges what the impostor of the interface reported (ADR-0011)."""

from typing import Literal

import pytest

from lictor.domain.interface_proof import (
    InterfaceProof,
    interface_proof,
    interface_proofs,
    unproved_reason,
    without_impostor_reason,
)
from lictor.domain.model import TestCaseResult

DRIVER = ("tests/acceptance/",)


Status = Literal["passed", "failure", "error", "skipped"]


def _case(
    status: Status = "passed",
    *,
    calls: int | None = 1,
    altered: int | None = 1,
    kind: str = "",
    frame: str = "",
) -> TestCaseResult:
    properties = {}
    if calls is not None:
        properties["lictor.interface.calls"] = str(calls)
    if altered is not None:
        properties["lictor.interface.altered"] = str(altered)
    if kind:
        properties["lictor.failure"] = kind
    if frame:
        properties["lictor.frame"] = frame
    return TestCaseResult("test_calc_001", status=status, properties=properties)


ASSERTED = _case("failure", kind="assertion", frame="tests/acceptance/test_calc.py")
IMPOSTOR = _case("failure", kind="impostor", frame="tests/acceptance/test_calc.py")
PASSED = _case("passed")


def _modes(
    more: TestCaseResult | None, less: TestCaseResult | None
) -> dict[str, TestCaseResult | None]:
    return {"mas": more, "menos": less}


def test_a_test_whose_own_assertion_fails_in_every_mode_is_proved() -> None:
    proof = interface_proof(PASSED, _modes(ASSERTED, ASSERTED), DRIVER)

    assert proof == InterfaceProof("probado")
    assert proof.proved is True


def test_the_impostor_s_own_error_counts_wherever_it_is_raised() -> None:
    """`pytest.raises(ErrorDeCalculo)` gets another class: the expected one never came."""
    raised = _case("failure", kind="impostor", frame="src/calc/__init__.py")

    assert interface_proof(PASSED, _modes(ASSERTED, raised), DRIVER).proved is True


@pytest.mark.parametrize(
    ("more", "less", "outcome"),
    [
        # No assert, or a weak one: `assert x`, `is not None`, `isinstance(x, float)`.
        (PASSED, PASSED, "sobrevive"),
        # `assert x != 5` or `assert x < 7`: one mode is enough to show it isn't checked.
        (ASSERTED, PASSED, "sobrevive"),
        # `pytest.raises(Exception)` catches the impostor's error and passes.
        (PASSED, ASSERTED, "sobrevive"),
    ],
)
def test_a_test_that_passes_in_some_mode_survives(
    more: TestCaseResult, less: TestCaseResult, outcome: str
) -> None:
    proof = interface_proof(PASSED, _modes(more, less), DRIVER)

    assert proof.outcome == outcome
    assert proof.fixable is True


def test_surviving_names_the_mode_it_passed_in() -> None:
    assert interface_proof(PASSED, _modes(ASSERTED, PASSED), DRIVER).detail == "menos"


def test_an_error_in_the_test_is_no_assertion() -> None:
    """A test that reads `.total` of what it got fails with AttributeError: it proves nothing."""
    attribute = TestCaseResult(
        "test_calc_001",
        status="failure",
        properties={
            "lictor.interface.altered": "1",
            "lictor.failure": "error",
            "lictor.origin": "tests/acceptance/test_calc.py",
        },
    )

    proof = interface_proof(PASSED, _modes(attribute, attribute), DRIVER)

    assert proof.outcome == "no_aserta"
    assert proof.detail == "error en tests/acceptance/test_calc.py"
    assert proof.fixable is True


def test_an_assertion_raised_outside_the_driver_is_none_of_the_test_s() -> None:
    inside = _case("failure", kind="assertion", frame="src/calc/__init__.py")

    proof = interface_proof(PASSED, _modes(inside, inside), DRIVER)

    assert proof == InterfaceProof(
        "no_aserta", "una aserción en src/calc/__init__.py, que no es del driver"
    )


def test_an_assertion_without_a_frame_is_not_the_driver_s() -> None:
    nowhere = _case("failure", kind="assertion")

    proof = interface_proof(PASSED, _modes(nowhere, nowhere), DRIVER)

    assert proof == InterfaceProof(
        "no_aserta", "una aserción fuera del proyecto, que no es del driver"
    )


def test_a_failure_without_a_kind_says_so() -> None:
    unclassified = _case("failure", frame="tests/acceptance/test_calc.py")

    proof = interface_proof(PASSED, _modes(unclassified, unclassified), DRIVER)

    assert proof.detail == "un fallo sin clasificar en tests/acceptance/test_calc.py"


def test_a_test_that_never_called_the_interface_is_told_so() -> None:
    proof = interface_proof(_case(calls=0), _modes(PASSED, PASSED), DRIVER)

    assert proof.outcome == "no_llama"
    assert proof.fixable is True


def test_a_call_count_that_isn_t_one_counts_as_no_call() -> None:
    forged = TestCaseResult("t", properties={"lictor.interface.calls": "dos"})

    assert interface_proof(forged, _modes(ASSERTED, ASSERTED), DRIVER).outcome == "no_llama"


def test_nothing_altered_is_no_proof_and_no_retry_changes_it() -> None:
    """`assert r.valor == 4` on an object: the impostor can't alter it."""
    untouched = _case("passed", altered=0)

    proof = interface_proof(PASSED, _modes(untouched, untouched), DRIVER)

    assert proof == InterfaceProof("sin_alterar")
    assert proof.fixable is False


def test_a_mode_that_altered_nothing_doesn_t_count_as_failing_either() -> None:
    failed_unaltered = _case("failure", altered=0, kind="assertion", frame=DRIVER[0] + "t.py")

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


def test_surviving_weighs_more_than_any_other_reason() -> None:
    untouched = _case("passed", altered=0)
    attribute = _case("failure", kind="error", frame="tests/acceptance/t.py")

    assert interface_proof(PASSED, _modes(untouched, PASSED), DRIVER).outcome == "sobrevive"
    assert interface_proof(PASSED, _modes(attribute, PASSED), DRIVER).outcome == "sobrevive"
    assert interface_proof(PASSED, _modes(untouched, attribute), DRIVER).outcome == "no_aserta"
    assert interface_proof(PASSED, _modes(None, untouched), DRIVER).outcome == "sin_alterar"


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
            InterfaceProof("sin_alterar"),
            f"{_HEAD}, y Lictor no puede comprobar que mire el resultado: lo que le devuelve la "
            "interfaz no es un número, un texto, una colección, un booleano ni una excepción, así "
            "que no se puede alterar.",
        ),
        (
            InterfaceProof("no_concluyente", "la suite cayó"),
            f"{_HEAD}, y no se pudo comprobar que su test mire el resultado: la suite cayó.",
        ),
    ],
)
def test_each_reason_says_only_what_was_seen(proof: InterfaceProof, expected: str) -> None:
    assert unproved_reason("CALC-001", "test_calc_001", proof, ["src/", "lib/"]) == expected


def test_without_writable_paths_the_reason_names_the_project_s_code() -> None:
    reason = unproved_reason("A", "t", InterfaceProof("no_llama"), [])

    assert "no llama a la interfaz en el código del proyecto:" in reason


def test_a_profile_without_the_impostor_claims_nothing() -> None:
    assert without_impostor_reason("CALC-001", "test_calc_001") == (
        "El test test_calc_001 del escenario CALC-001 pasa con un driver nuevo, y este perfil no "
        "puede comprobar que mire el resultado: puede que el código ya cumpla el escenario, o que "
        "el test no compruebe nada. Asegúrate de que compara con assert el resultado con el valor "
        "que da su escenario."
    )
