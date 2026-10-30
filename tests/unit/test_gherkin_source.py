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
from lictor.domain.errors import SpecError
from lictor.domain.model import ScenarioId


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
    assert "exactamente un tag" in str(exc_info.value)


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
    assert "múltiples tags" in str(exc_info.value) or "exactamente un tag" in str(exc_info.value)


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
    assert "duplicado" in str(exc_info.value).lower()


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
    assert "Scenario Outline no admitido" in str(exc_info.value)
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
    assert "Scenario Outline no admitido" in str(exc_info.value)


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
