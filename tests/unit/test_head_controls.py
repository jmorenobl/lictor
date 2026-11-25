"""How a blocking control that fails on HEAD is told to the person."""

from lictor.domain.controls import CommandControl, ReaderControl
from lictor.domain.head_controls import (
    blocking_on_head,
    head_control_problem,
    head_not_started,
    head_tampered,
)


def test_a_control_failing_on_head_is_named_with_its_error_and_where_its_output_is() -> None:
    message = head_control_problem("unit", "Falló unit\n\n- caso\n", ".lictor/doctor/controles/")

    assert message.startswith("El control 'unit' no pasa en HEAD")
    assert "desactiva el control 'unit' en lictor.json" in message
    assert "Su salida entera está en .lictor/doctor/controles/." in message
    assert message.endswith("Falló unit\n\n- caso")


def test_a_tampering_on_head_names_what_changed() -> None:
    assert "(seal.json, journal.jsonl)" in head_tampered(["seal.json", "journal.jsonl"])


def _command(control_id: str, mode: str = "block") -> CommandControl:
    return CommandControl(id=control_id, level="L1", run=control_id, report="exit-code", mode=mode)


def test_only_blocking_controls_that_can_start_run_on_head() -> None:
    controls = [
        _command("lint"),
        _command("unit"),
        _command("medida", "measure"),
        ReaderControl(id="cobertura", level="L4", reads="unit", adds="--cov", mode="block"),
        ReaderControl(id="otra", level="L4", reads="lint", adds="--x", mode="block"),
    ]

    assert blocking_on_head(controls, {"unit"}) == (("lint", "otra"), ("unit", "cobertura"))
    assert blocking_on_head(controls, set()) == (("lint", "unit", "cobertura", "otra"), ())


def test_the_controls_left_out_are_named() -> None:
    assert head_not_started(["unit", "cobertura"]) == (
        "No se ejecutan en HEAD 'unit', 'cobertura': su programa no arranca, como dice "
        "«Comandos de verificación»."
    )
