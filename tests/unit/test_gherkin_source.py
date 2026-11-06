import json
from pathlib import Path

import pytest

from lictor.adapters.spec.gherkin_source import (
    GherkinSource,
    compute_seal,
    read_seal,
    verify_seal,
    write_seal,
)
from lictor.application.seal import hash_file
from lictor.domain.errors import SpecError
from lictor.domain.model import Scenario, ScenarioId


def test_load_valid_feature_spanish_and_background(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    feature_file = features_dir / "iva.feature"
    feature_file.write_text(
        """# language: es
Característica: IVA de una factura

  Antecedentes:
    Dado una tasa base del 21%

  @IVA-001
  Escenario: IVA general
    Dado una factura con importe 100
    Cuando calculo el IVA general
    Entonces el total es 121

  @IVA-002
  Escenario: IVA reducido
    Dado una factura con importe 100
    Cuando calculo el IVA reducido
    Entonces el total es 110
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    scenarios = source.load(features_dir)

    assert len(scenarios) == 2
    assert scenarios[0].id == ScenarioId("IVA-001")
    assert scenarios[0].name == "IVA general"
    assert scenarios[0].line == 8
    assert scenarios[1].id == ScenarioId("IVA-002")
    assert scenarios[1].name == "IVA reducido"
    assert scenarios[1].line == 14


def test_load_deterministic_ordering(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "b.feature").write_text(
        """
Feature: Feature B
  @B-001
  Scenario: Scenario B
    Given something
""",
        encoding="utf-8",
    )
    (features_dir / "a.feature").write_text(
        """
Feature: Feature A
  @A-001
  Scenario: Scenario A
    Given something
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    scenarios = source.load(features_dir)

    assert [s.id.value for s in scenarios] == ["A-001", "B-001"]


def test_reject_scenario_without_tags(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "test.feature").write_text(
        """
Feature: No tags
  Scenario: Untagged
    Given something
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert exc_info.value.exit_code == 10
    assert "features/test.feature:3: el escenario «Untagged» no tiene ID" in str(exc_info.value)


def test_reject_scenario_with_multiple_tags(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "test.feature").write_text(
        """
Feature: Multiple tags
  @IVA-001 @SLOW
  Scenario: Two tags
    Given something
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert exc_info.value.exit_code == 10
    assert "tiene varias etiquetas: @IVA-001, @SLOW" in str(exc_info.value)


def test_reject_scenario_with_invalid_tag_format(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "test.feature").write_text(
        """
Feature: Bad tag
  @invalid_tag
  Scenario: Bad tag
    Given something
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert exc_info.value.exit_code == 10


def test_reject_duplicate_scenario_ids(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "a.feature").write_text(
        """
Feature: Dup 1
  @IVA-001
  Scenario: One
    Given something
""",
        encoding="utf-8",
    )
    (features_dir / "b.feature").write_text(
        """
Feature: Dup 2
  @IVA-001
  Scenario: Duplicate
    Given something
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert exc_info.value.exit_code == 10
    assert "usa el ID @IVA-001, que ya tiene features/a.feature:4" in str(exc_info.value)


def test_reject_scenario_outline_english(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "outline.feature").write_text(
        """
Feature: Outline
  @IVA-001
  Scenario Outline: Outline test
    Given <val>
    Examples:
      | val |
      | 1   |
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert exc_info.value.exit_code == 10
    assert "es un Esquema del escenario (Scenario Outline)" in str(exc_info.value)
    assert "outline.feature" in str(exc_info.value)


def test_reject_scenario_outline_spanish(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "outline_es.feature").write_text(
        """# language: es
Característica: Esquema
  @IVA-001
  Esquema del escenario: Esquema prueba
    Dado <val>
    Ejemplos:
      | val |
      | 1   |
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert exc_info.value.exit_code == 10
    assert "es un Esquema del escenario (Scenario Outline)" in str(exc_info.value)


def test_reject_scenario_template_without_examples(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "template.feature").write_text(
        """
Feature: Template without examples
  @IVA-001
  Scenario Template: Template test
    Given something
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert exc_info.value.exit_code == 10
    assert "es un Esquema del escenario (Scenario Outline)" in str(exc_info.value)


def test_reject_scenario_outline_french(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "french.feature").write_text(
        """# language: fr
Fonctionnalité: Plan
  @IVA-001
  Plan du scénario: Test plan
    Soit <val>
    Exemples:
      | val |
      | 1   |
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert exc_info.value.exit_code == 10
    assert "es un Esquema del escenario (Scenario Outline)" in str(exc_info.value)


def test_reject_tag_id_on_feature_level(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "feature_tag.feature").write_text(
        """
@IVA-001
Feature: Feature with scenario id tag
  Scenario: Scenario without tag
    Given something
""",
        encoding="utf-8",
    )

    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert exc_info.value.exit_code == 10
    assert "la etiqueta @IVA-001 está en la característica" in str(exc_info.value)


def test_seal_computation_write_and_verification(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "iva.feature").write_text("Feature: IVA\n", encoding="utf-8")

    driver_dir = tmp_path / "tests" / "acceptance"
    driver_dir.mkdir(parents=True)
    driver_file = driver_dir / "test_driver.py"
    driver_file.write_text("def test_something(): pass\n", encoding="utf-8")

    seal = compute_seal(
        project_root=tmp_path,
        run_id="20261030-120000-abcd",
        driver_paths=["tests/acceptance/"],
        created="2026-10-30T12:00:00Z",
    )

    assert seal["created"] == "2026-10-30T12:00:00Z"
    assert seal["run_id"] == "20261030-120000-abcd"
    assert "features/iva.feature" in seal["files"]
    assert "tests/acceptance/test_driver.py" in seal["files"]
    assert seal["files"]["features/iva.feature"].startswith("sha256:")

    seal_path = write_seal(tmp_path, seal)
    assert seal_path.is_file()
    assert read_seal(tmp_path) == seal
    assert json.loads(seal_path.read_text(encoding="utf-8")) == seal

    # Verify seal passes
    assert verify_seal(tmp_path, ["tests/acceptance/"]) is True

    # Mutate feature file -> seal fails
    (features_dir / "iva.feature").write_text("Feature: Modificado\n", encoding="utf-8")
    assert verify_seal(tmp_path, ["tests/acceptance/"]) is False

    # Restore feature file, mutate driver file -> seal fails
    (features_dir / "iva.feature").write_text("Feature: IVA\n", encoding="utf-8")
    assert verify_seal(tmp_path, ["tests/acceptance/"]) is True
    driver_file.write_text("def test_modified(): pass\n", encoding="utf-8")
    assert verify_seal(tmp_path, ["tests/acceptance/"]) is False

    # Restore driver file -> seal passes
    driver_file.write_text("def test_something(): pass\n", encoding="utf-8")
    assert verify_seal(tmp_path, ["tests/acceptance/"]) is True

    # Untracked/new file in driver path -> seal fails
    extra_driver = driver_dir / "extra_driver.py"
    extra_driver.write_text("def test_extra(): pass\n", encoding="utf-8")
    assert verify_seal(tmp_path, ["tests/acceptance/"]) is False
    extra_driver.unlink()

    # Deleted file from seal -> seal fails
    driver_file.unlink()
    assert verify_seal(tmp_path, ["tests/acceptance/"]) is False


def test_seal_ignores_pycache_and_pyc(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    driver_dir = tmp_path / "tests" / "acceptance"
    features_dir.mkdir(parents=True)
    driver_dir.mkdir(parents=True)
    (features_dir / "iva.feature").write_text("Feature: IVA\n", encoding="utf-8")
    (driver_dir / "test_driver.py").write_text("def test_iva_001(): pass\n", encoding="utf-8")

    # Add pycache and pyc file
    pycache_dir = driver_dir / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "test_driver.cpython-312.pyc").write_bytes(b"bytecode")

    seal = compute_seal(tmp_path, "run-1", ["tests/acceptance/"])
    # pycache and pyc should not be in seal
    assert "tests/acceptance/__pycache__/test_driver.cpython-312.pyc" not in seal["files"]
    assert "tests/acceptance/test_driver.py" in seal["files"]

    write_seal(tmp_path, seal)
    assert verify_seal(tmp_path, ["tests/acceptance/"]) is True


def test_scenario_file_is_relative_posix(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "iva.feature").write_text(
        """Feature: Test
  @IVA-001
  Scenario: General
    Given step
""",
        encoding="utf-8",
    )
    source = GherkinSource()
    scenarios = source.load(features_dir.resolve())
    assert len(scenarios) == 1
    assert not Path(scenarios[0].file).is_absolute()
    assert "\\" not in scenarios[0].file
    assert scenarios[0].file.endswith("features/iva.feature")


def test_load_scenarios_inside_rule(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "rule.feature").write_text(
        """# language: es
Característica: Con reglas
  Regla: Regla de cálculo
    Antecedentes:
      Dado un contexto previo

    @RUL-001
    Escenario: Escenario dentro de regla
      Dado un paso
      Entonces resultado
""",
        encoding="utf-8",
    )
    source = GherkinSource()
    scenarios = source.load(features_dir)
    assert len(scenarios) == 1
    assert scenarios[0].id == ScenarioId("RUL-001")
    assert scenarios[0].name == "Escenario dentro de regla"


def test_reject_rule_level_scenario_id_tag(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "rule.feature").write_text(
        """Feature: Con reglas
  @RUL-001
  Rule: Regla con tag ID
    @RUL-002
    Scenario: Escenario
      Given step
""",
        encoding="utf-8",
    )
    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert "la etiqueta @RUL-001 está en una regla" in str(exc_info.value)


def test_reject_scenario_outline_inside_rule(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "rule.feature").write_text(
        """Feature: Con reglas
  Rule: Regla
    @RUL-001
    Scenario Outline: Esquema en regla
      Given <val>
      Examples:
        | val |
        | 1   |
""",
        encoding="utf-8",
    )
    source = GherkinSource()
    with pytest.raises(SpecError) as exc_info:
        source.load(features_dir)
    assert "es un Esquema del escenario (Scenario Outline)" in str(exc_info.value)


def test_reject_symlink_pointing_outside_repo(tmp_path: Path) -> None:
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside content\n", encoding="utf-8")

    project_root = tmp_path / "project"
    features_dir = project_root / "features"
    features_dir.mkdir(parents=True)
    symlink_feat = features_dir / "external.feature"
    symlink_feat.symlink_to(outside_file)

    with pytest.raises(SpecError) as exc_info:
        compute_seal(project_root, "run-1", [])
    assert "Enlace simbólico fuera del repositorio no permitido" in str(exc_info.value)


def test_write_seal_hashes_the_given_tree_but_stores_in_the_project(tmp_path: Path) -> None:
    project = tmp_path / "proyecto"
    verified = tmp_path / "verificado"
    for root, text in ((project, "cambiado después"), (verified, "lo verificado")):
        (root / "features").mkdir(parents=True)
        (root / "features" / "a.feature").write_text(text, encoding="utf-8")

    data = GherkinSource().write_seal(project, "R", [], source_root=verified)

    assert read_seal(project) == data
    assert data["files"]["features/a.feature"] == hash_file(verified / "features" / "a.feature")


_BASE_FEATURE = """# language: es
Característica: IVA

  Antecedentes:
    Dado una tasa base del 21%

  @IVA-001
  Escenario: IVA general
    Dado una factura con importe 100
    Y otra factura
    Cuando calculo el IVA general
    Entonces el total es 121
      | a | b |
      | 1 | 2 |

  @IVA-002
  Escenario: IVA reducido
    Dado una factura con importe 100
    Entonces el total es 110
"""


def _hashes(text: str) -> dict[str, str]:
    return GherkinSource().spec_hashes(text)


def test_spec_hashes_name_every_scenario_by_its_id() -> None:
    hashes = _hashes(_BASE_FEATURE)

    assert set(hashes) == {"IVA-001", "IVA-002"}
    assert all(value.startswith("sha256:") for value in hashes.values())
    assert hashes["IVA-001"] != hashes["IVA-002"]


def test_load_fills_the_same_hash_as_spec_hashes(tmp_path: Path) -> None:
    (tmp_path / "features").mkdir()
    (tmp_path / "features" / "iva.feature").write_text(_BASE_FEATURE, encoding="utf-8")

    loaded = {s.id.value: s.spec_hash for s in GherkinSource().load(tmp_path / "features")}

    assert loaded == _hashes(_BASE_FEATURE)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("Escenario: IVA general", "Escenario: IVA general renombrado"),
        ("Característica: IVA", "Característica: Otro nombre"),
        ("  @IVA-001\n", "  # un comentario\n  @IVA-001\n"),
        ("    Cuando calculo", "        Cuando   calculo"),
        ("    Y otra factura", "    Dado otra factura"),
        (
            "# language: es\nCaracterística: IVA",
            "# language: es\nCaracterística: IVA\n  Texto libre",
        ),
    ],
)
def test_spec_hash_ignores_what_demands_nothing(old: str, new: str) -> None:
    assert old in _BASE_FEATURE
    changed = _BASE_FEATURE.replace(old, new, 1)
    assert _hashes(changed)["IVA-001"] == _hashes(_BASE_FEATURE)["IVA-001"]


def test_spec_hash_ignores_the_language_of_the_keywords() -> None:
    spanish = "# language: es\nCaracterística: X\n  @A-1\n  Escenario: S\n    Dado algo\n"
    english = "Feature: X\n  @A-1\n  Scenario: S\n    Given algo\n"
    assert _hashes(spanish) == _hashes(english)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("el total es 121", "el total es 122"),
        ("      | 1 | 2 |", "      | 1 | 3 |"),
        ("Dado una tasa base del 21%", "Dado una tasa base del 10%"),
        ("    Cuando calculo el IVA general", "    Entonces calculo el IVA general"),
        ("    Y otra factura", "    Entonces otra factura"),
        ("    Entonces el total es 121\n", "    Entonces el total es 121\n    Y nada más\n"),
    ],
)
def test_spec_hash_changes_with_what_the_scenario_demands(old: str, new: str) -> None:
    assert old in _BASE_FEATURE
    changed = _BASE_FEATURE.replace(old, new, 1)
    assert _hashes(changed)["IVA-001"] != _hashes(_BASE_FEATURE)["IVA-001"]


def test_spec_hash_includes_the_doc_string() -> None:
    def with_doc(body: str) -> str:
        doc = f'      """\n      {body}\n      """\n'
        return f"Feature: X\n  @A-1\n  Scenario: S\n    Given algo\n{doc}"

    assert _hashes(with_doc("uno")) != _hashes(with_doc("dos"))


def test_spec_hash_includes_the_background_of_the_rule() -> None:
    def with_rule(step: str) -> str:
        return (
            "Feature: X\n  Rule: R\n    Background:\n"
            f"      Given {step}\n    @A-1\n    Scenario: S\n      Given algo\n"
        )

    assert _hashes(with_rule("uno")) != _hashes(with_rule("dos"))
    assert set(_hashes(with_rule("uno"))) == {"A-1"}


def test_spec_hashes_of_an_old_tree_tolerate_what_load_would_reject() -> None:
    """An old commit's features are read only to compare; a broken one just isn't found."""
    assert _hashes("esto no es gherkin @@@\n  Scenario") == {}
    untagged = "Feature: X\n  Scenario: S\n    Given algo\n  @A-1\n  Scenario: T\n    Given b\n"
    assert set(_hashes(untagged)) == {"A-1"}
    outline = (
        "Feature: X\n  @A-1\n  Scenario Outline: S\n    Given <n>\n"
        "    Examples:\n      | n |\n      | 1 |\n"
    )
    other = outline.replace("| 1 |", "| 2 |")
    assert _hashes(outline)["A-1"] != _hashes(other)["A-1"]


def test_a_rule_without_scenarios_still_cannot_carry_a_scenario_id(tmp_path: Path) -> None:
    (tmp_path / "features").mkdir()
    (tmp_path / "features" / "r.feature").write_text(
        "Feature: X\n  @A-1\n  Rule: R\n", encoding="utf-8"
    )
    with pytest.raises(SpecError, match="está en una regla"):
        GherkinSource().load(tmp_path / "features")


def _text_of(tmp_path: Path, content: str, sid: str, name: str = "N") -> str:
    feature = tmp_path / "features" / "iva.feature"
    feature.parent.mkdir(parents=True, exist_ok=True)
    feature.write_text(content, encoding="utf-8")
    scenario = Scenario(ScenarioId(sid), name, "features/iva.feature", 1)
    return GherkinSource().scenario_text(tmp_path, scenario)


FEATURE_WITH_COMMENTS = """# language: es
Característica: Facturación
  Antecedentes:
    Dado una tasa de IVA del 21%
    Y un emisor registrado

  # Comentario importante 1
  # Comentario importante 2
  @IVA-001
  # Comentario entre tag y escenario
  Escenario: IVA general
    Dado una factura con importe 100
    Cuando calculo el total
    Entonces el total es 121

  @IVA-002
  Escenario: IVA reducido
    Dado una factura con importe 100
"""


def test_scenario_text_keeps_background_comments_and_its_own_lines(tmp_path: Path) -> None:
    text = _text_of(tmp_path, FEATURE_WITH_COMMENTS, "IVA-001")

    assert text == (
        "Antecedentes:\n"
        "    Dado una tasa de IVA del 21%\n"
        "    Y un emisor registrado\n\n"
        "# Comentario importante 1\n"
        "  # Comentario importante 2\n"
        "  @IVA-001\n"
        "  # Comentario entre tag y escenario\n"
        "  Escenario: IVA general\n"
        "    Dado una factura con importe 100\n"
        "    Cuando calculo el total\n"
        "    Entonces el total es 121"
    )


def test_scenario_text_of_the_last_scenario_runs_to_the_end(tmp_path: Path) -> None:
    text = _text_of(tmp_path, FEATURE_WITH_COMMENTS, "IVA-002")

    assert text.startswith("Antecedentes:")
    assert text.endswith(
        "@IVA-002\n  Escenario: IVA reducido\n    Dado una factura con importe 100"
    )
    assert "IVA general" not in text


def test_scenario_text_finds_the_exact_id_not_a_prefix(tmp_path: Path) -> None:
    content = """Feature: X
  @IVA-10
  Scenario: diez
    Given secreto

  @IVA-1
  Scenario: uno
    Given a
"""
    assert _text_of(tmp_path, content, "IVA-1") == "@IVA-1\n  Scenario: uno\n    Given a"


def test_scenario_text_takes_the_rule_background_too(tmp_path: Path) -> None:
    content = """Feature: X
  Background:
    Given general

  Rule: R
    Background:
      Given de la regla

    @IVA-1
    Scenario: uno
      Given a

  Rule: S
    Background:
      Given de otra regla

    @IVA-2
    Scenario: dos
      Given b
"""
    text = _text_of(tmp_path, content, "IVA-1")

    assert text == (
        "Background:\n    Given general\n\n"
        "Background:\n      Given de la regla\n\n"
        "@IVA-1\n    Scenario: uno\n      Given a"
    )


def test_scenario_text_with_its_tag_lines_away_from_the_keyword(tmp_path: Path) -> None:
    content = """Feature: X
  @IVA-1

  # nota
  Scenario: uno
    Given a
  @IVA-2
  Scenario: dos
    Given b
"""
    assert _text_of(tmp_path, content, "IVA-1") == (
        "@IVA-1\n\n  # nota\n  Scenario: uno\n    Given a"
    )


@pytest.mark.parametrize(
    "content",
    ["Feature: X\n  @IVA-2\n  Scenario: dos\n    Given b\n", "no es gherkin: [", ""],
)
def test_scenario_text_without_the_scenario_is_only_its_header(
    tmp_path: Path, content: str
) -> None:
    assert _text_of(tmp_path, content, "IVA-1", "uno") == "Scenario: uno (@IVA-1)"


def test_scenario_text_without_the_file_is_only_its_header(tmp_path: Path) -> None:
    scenario = Scenario(ScenarioId("IVA-1"), "uno", "features/no.feature", 1)

    assert GherkinSource().scenario_text(tmp_path, scenario) == "Scenario: uno (@IVA-1)"


def test_scenario_text_reads_an_absolute_path(tmp_path: Path) -> None:
    feature = tmp_path / "a.feature"
    feature.write_text("Feature: X\n  @IVA-1\n  Scenario: uno\n    Given a\n", encoding="utf-8")
    scenario = Scenario(ScenarioId("IVA-1"), "uno", str(feature), 1)

    assert GherkinSource().scenario_text(Path("/no/existe"), scenario).endswith("Given a")


def test_problems_lists_everything_load_would_refuse_without_raising(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "a.feature").write_text(
        "Feature: A\n  Scenario: Uno\n    Given algo\n  oops\n", encoding="utf-8"
    )
    (features_dir / "b.feature").write_text("# language: es\n# pendiente\n", encoding="utf-8")

    problems = GherkinSource().problems(features_dir)

    assert [(p.file, p.line) for p in problems] == [
        ("features/a.feature", 4),
        ("features/b.feature", None),
    ]
    assert problems[0].message.startswith("no se puede leer: expected:")
    assert "got 'oops'" in problems[0].message
    assert "vacío" in problems[1].message
    assert [p.blocking for p in problems] == [True, False]


def test_load_skips_an_empty_feature_file(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "a.feature").write_text(
        "Feature: A\n  @A-001\n  Scenario: Uno\n    Given algo\n", encoding="utf-8"
    )
    (features_dir / "b.feature").write_text("Feature: Pendiente\n", encoding="utf-8")

    assert [s.id.value for s in GherkinSource().load(features_dir)] == ["A-001"]


def test_load_without_a_features_directory_says_there_is_no_scenario(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="features/: no hay ningún escenario"):
        GherkinSource().load(tmp_path / "features")


def test_load_lists_every_problem_not_only_the_first(tmp_path: Path) -> None:
    features_dir = tmp_path / "features"
    features_dir.mkdir()
    (features_dir / "a.feature").write_text(
        "Feature: A\n  Scenario: Uno\n    Given algo\n\n  Scenario: Dos\n    Given algo\n",
        encoding="utf-8",
    )

    with pytest.raises(SpecError) as exc_info:
        GherkinSource().load(features_dir)

    assert "features/a.feature:2: el escenario «Uno» no tiene ID" in str(exc_info.value)
    assert "features/a.feature:5: el escenario «Dos» no tiene ID" in str(exc_info.value)
