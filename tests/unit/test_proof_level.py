"""The level of proof a run's defences give, and the gaps Lictor knows how to compute (ADR-0020)."""

from dataclasses import replace

import pytest

from lictor.domain.proof_level import (
    BASIC,
    CONTROL_WITHOUT_PLUGIN,
    PLUGINS_UNCHECKED,
    PROVED,
    READ,
    Gap,
    ProofDefences,
    ProofLevel,
    gap_text,
    level_limit,
    level_name,
    proof_from_record,
    proof_level,
    proof_record,
    status_line,
    unchecked_lines,
    warning_lines,
)

PYTHON_UV = ProofDefences(
    link="scenario",
    red_kinds=True,
    stub_rule_c=True,
    declared_stubs=False,
    interface_impostor=True,
    gherkin=True,
    env_hashed=True,
    suppressions=True,
    blocking_controls=True,
    survey=True,
    plugin_controls=(("coverage", "pytest_cov.plugin"),),
)
GENERIC = ProofDefences(
    link="name",
    red_kinds=False,
    stub_rule_c=False,
    declared_stubs=False,
    interface_impostor=False,
    gherkin=False,
    env_hashed=False,
    suppressions=False,
    blocking_controls=False,
    survey=False,
)
INSTALLED = ("pytest_bdd.plugin", "pytest_cov.plugin")


def _codes(proof: ProofLevel) -> list[str]:
    return [gap.code for gap in proof.gaps]


def test_python_uv_is_proved_with_no_gap() -> None:
    assert proof_level(PYTHON_UV, INSTALLED) == ProofLevel(PROVED)


def test_generic_is_basic_with_every_gap_it_computes() -> None:
    proof = proof_level(GENERIC, ())

    assert proof.level == BASIC
    assert _codes(proof) == [
        "rojo_sin_clase",
        "enlace_por_nombre",
        "ya_cumplido_sin_prueba",
        "entorno_sin_hash",
        "sin_supresiones",
        "sin_controles",
        "sin_lectura_de_controles",
    ]


def test_python_uv_without_its_coverage_plugin_stays_proved_with_the_control_as_a_gap() -> None:
    proof = proof_level(PYTHON_UV, ("pytest_bdd.plugin",))

    assert proof.level == PROVED
    assert proof.gaps == (Gap(CONTROL_WITHOUT_PLUGIN, "coverage", "pytest_cov.plugin"),)
    assert gap_text(proof.gaps[0]) == "coverage no corre: falta pytest_cov.plugin"


def test_without_the_environment_the_plugins_are_unchecked_not_assumed() -> None:
    assert _codes(proof_level(PYTHON_UV, None)) == [PLUGINS_UNCHECKED]
    assert _codes(proof_level(GENERIC, None)) == _codes(proof_level(GENERIC, ()))


def test_without_rule_c_nor_the_drivers_constants_it_is_basic() -> None:
    proof = proof_level(replace(PYTHON_UV, stub_rule_c=False), INSTALLED)

    assert proof == ProofLevel(BASIC)


@pytest.mark.parametrize(
    ("change", "gap"),
    [
        ({"red_kinds": False}, "rojo_sin_clase"),
        ({"link": "name"}, "enlace_por_nombre"),
        ({"interface_impostor": False, "gherkin": False}, "ya_cumplido_sin_prueba"),
    ],
)
def test_rule_c_without_the_rest_of_the_oracle_reads_the_result(
    change: dict[str, object], gap: str
) -> None:
    proof = proof_level(replace(PYTHON_UV, **change), INSTALLED)

    assert proof == ProofLevel(READ, (Gap(gap),))


def test_generic_with_the_drivers_constants_reads_the_result_with_the_same_gaps() -> None:
    proof = proof_level(replace(GENERIC, declared_stubs=True), ())

    assert proof.level == READ
    assert proof.gaps == proof_level(GENERIC, ()).gaps


def test_the_drivers_constants_never_prove_the_result_without_rule_c() -> None:
    """A core with the whole oracle but the basic rule: no class of the red judged its tests."""
    defences = replace(PYTHON_UV, stub_rule_c=False, declared_stubs=True)

    assert proof_level(defences, INSTALLED).level == READ


@pytest.mark.parametrize(
    ("change", "gap"),
    [
        ({"gherkin": False}, "valor_sin_peso"),
        ({"env_hashed": False}, "entorno_sin_hash"),
        ({"suppressions": False}, "sin_supresiones"),
        ({"blocking_controls": False}, "sin_controles"),
        ({"survey": False}, "sin_lectura_de_controles"),
    ],
)
def test_what_doesnt_touch_the_oracle_is_a_gap_and_keeps_the_level(
    change: dict[str, object], gap: str
) -> None:
    proof = proof_level(replace(PYTHON_UV, **change), INSTALLED)

    assert proof == ProofLevel(PROVED, (Gap(gap),))


def test_the_gherkin_without_the_interface_is_no_gap_of_its_own() -> None:
    defences = replace(GENERIC, gherkin=True)

    assert "valor_sin_peso" not in _codes(proof_level(defences, ()))


def test_names_and_limits_of_each_level() -> None:
    assert level_name(BASIC) == "básico"
    assert level_name(READ) == "resultado leído"
    assert level_name(PROVED) == "resultado probado"
    assert level_name("otro") == "otro"
    assert level_limit(BASIC).startswith("No se comprueba que el test compare nada: uno sin")
    assert level_limit(READ).startswith("No se comprueba que el test compare con el valor")
    assert "las dos constantes rompen" in level_limit(READ)
    assert level_limit(PROVED).startswith("No se comprueba que el test compare con el valor")
    assert level_limit("otro") == ""


def test_every_gap_has_its_own_sentence() -> None:
    texts = [gap_text(gap) for gap in proof_level(GENERIC, ()).gaps]

    assert len(set(texts)) == len(texts)
    assert texts[0] == (
        "Cualquier fallo cuenta como rojo, también uno de compilación o de importación"
    )
    assert "suppressions y banned_patterns" in texts[4]
    assert gap_text(Gap("desconocido")) == "desconocido"
    assert gap_text(Gap(PLUGINS_UNCHECKED)).startswith("No se comprobó qué controles")


def test_what_is_unchecked_starts_with_the_level_and_warnings_below_proved() -> None:
    basic = proof_level(GENERIC, ())
    proved = proof_level(PYTHON_UV, ("pytest_bdd.plugin",))

    assert unchecked_lines(basic)[0] == level_limit(BASIC)
    assert len(unchecked_lines(basic)) == 1 + len(basic.gaps)
    assert warning_lines(basic) == unchecked_lines(basic)
    assert unchecked_lines(proved) == (
        level_limit(PROVED),
        "coverage no corre: falta pytest_cov.plugin",
    )
    assert warning_lines(proved) == ("coverage no corre: falta pytest_cov.plugin",)
    assert warning_lines(ProofLevel(PROVED)) == ()
    assert warning_lines(ProofLevel(READ)) == (level_limit(READ),)


def test_status_says_the_level_and_how_many_gaps() -> None:
    assert status_line(ProofLevel(PROVED)) == "Nivel de prueba: resultado probado"
    assert status_line(ProofLevel(BASIC, (Gap("sin_controles"),))) == (
        "Nivel de prueba: básico (1 hueco; ver report.md)"
    )
    assert status_line(proof_level(GENERIC, ())) == (
        "Nivel de prueba: básico (7 huecos; ver report.md)"
    )


def test_the_record_keeps_codes_and_reads_back() -> None:
    proof = ProofLevel(
        BASIC, (Gap("sin_controles"), Gap(CONTROL_WITHOUT_PLUGIN, "coverage", "pytest_cov.plugin"))
    )
    record = proof_record(proof)

    assert record == {
        "level": "basico",
        "gaps": [
            {"code": "sin_controles"},
            {"code": "control_sin_plugin", "control": "coverage", "plugin": "pytest_cov.plugin"},
        ],
    }
    assert proof_from_record(record) == proof
    assert proof_from_record(proof_record(ProofLevel(PROVED))) == ProofLevel(PROVED)
    assert proof_from_record(proof_record(ProofLevel(READ))) == ProofLevel(READ)


@pytest.mark.parametrize(
    "record",
    [
        None,
        "basico",
        {"gaps": []},
        {"level": "otro", "gaps": []},
        {"level": "basico"},
        {"level": "basico", "gaps": "sin_controles"},
        {"level": "basico", "gaps": [{"control": "x"}]},
        {"level": "basico", "gaps": ["sin_controles"]},
        {"level": "basico", "gaps": [{"code": 1}]},
        {"level": "basico", "gaps": [{"code": "control_sin_plugin", "control": 1}]},
        {"level": "basico", "gaps": [{"code": "control_sin_plugin", "plugin": 1}]},
    ],
)
def test_a_record_in_another_form_reads_as_none(record: object) -> None:
    assert proof_from_record(record) is None
