"""How a blocking control that fails on HEAD is told to the person."""

from lictor.domain.head_controls import head_control_problem, head_tampered


def test_a_control_failing_on_head_is_named_with_its_error_and_where_its_output_is() -> None:
    message = head_control_problem("unit", "Falló unit\n\n- caso\n", ".lictor/doctor/controles/")

    assert message.startswith("El control 'unit' no pasa en HEAD")
    assert "desactiva el control 'unit' en lictor.json" in message
    assert "Su salida entera está en .lictor/doctor/controles/." in message
    assert message.endswith("Falló unit\n\n- caso")


def test_a_tampering_on_head_names_what_changed() -> None:
    assert "(seal.json, journal.jsonl)" in head_tampered(["seal.json", "journal.jsonl"])
