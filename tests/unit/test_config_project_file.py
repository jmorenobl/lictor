"""What a project writes in `lictor.json`, and how it resolves against its profile (0.3.0).

`lictor init` writes only what the person decides; everything else comes from the profile on every
load. `controls` are changes by `id` over the profile's: one written replaces only its fields,
`enabled: false` drops it and an `id` the profile lacks adds a control.
"""

import json
from pathlib import Path

import pytest

from lictor.adapters.config.loader import (
    config_fingerprint,
    export_config_schema,
    init_config_file,
    load_config,
    load_profile,
    resolve_defences,
)
from lictor.adapters.config.models import DEFAULT_MODEL
from lictor.domain.errors import ConfigError, ExitCode

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITTEN_BY_0_2_0 = REPO_ROOT / "tests" / "fixtures" / "lictor_0_2_0.json"
PROFILE_IDS = ["lint", "types", "unit", "coverage"]
ADDED = {"id": "audit", "level": "L4", "run": "pip-audit", "report": "exit-code"}


def _python_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nrequires-python = '>=3.12'\n", encoding="utf-8"
    )
    return root


def _with_controls(tmp_path: Path, controls: list[dict[str, object]]) -> Path:
    path = tmp_path / "lictor.json"
    path.write_text(json.dumps({"profile": "python-uv@0.1", "controls": controls}), "utf-8")
    return path


def _fingerprint(path: Path) -> str:
    config = load_config(path)
    return config_fingerprint(config, resolve_defences(config))


def _ids(path: Path) -> list[str]:
    return [control.id for control in load_config(path).controls]


def test_init_writes_only_what_the_person_decides(tmp_path: Path) -> None:
    root = _python_project(tmp_path)

    init_config_file(root)

    written = json.loads((root / "lictor.json").read_text(encoding="utf-8"))
    assert set(written) == {"version", "profile", "backend", "hints"}
    assert written["backend"] == {"model": DEFAULT_MODEL}
    assert "3.12" in written["hints"]


def test_a_lictor_json_of_0_2_0_keeps_its_fingerprint(tmp_path: Path) -> None:
    """A full copy that resolves to the same thing lets a run started with 0.2.0 resume."""
    root = _python_project(tmp_path / "nuevo")
    init_config_file(root)
    copied = tmp_path / "copiado" / "lictor.json"
    _python_project(copied.parent)
    copied.write_text(WRITTEN_BY_0_2_0.read_text(encoding="utf-8"), encoding="utf-8")

    assert load_config(copied) == load_config(root)
    assert _fingerprint(copied) == _fingerprint(root / "lictor.json")


def test_without_controls_the_project_gets_the_profiles(tmp_path: Path) -> None:
    root = _python_project(tmp_path)
    init_config_file(root)

    assert load_config(root).controls == load_profile("python-uv").controls


def test_a_control_of_the_profile_changes_only_the_fields_written(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "coverage", "mode": "block", "timeout_s": 99}])

    coverage = next(c for c in load_config(path).controls if c.id == "coverage")

    profile_coverage = next(c for c in load_profile("python-uv").controls if c.id == "coverage")
    assert coverage.mode == "block"
    assert coverage.timeout_s == 99
    assert coverage.run == profile_coverage.run
    assert coverage.when_plugin == profile_coverage.when_plugin
    assert _ids(path) == PROFILE_IDS


def test_enabled_false_drops_a_control_of_the_profile(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "types", "enabled": False}])

    assert _ids(path) == ["lint", "unit", "coverage"]


def test_an_id_the_profile_lacks_adds_a_control_at_the_end(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [ADDED])

    assert _ids(path) == [*PROFILE_IDS, "audit"]


def test_an_added_control_goes_after_the_one_written_before_it(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "lint"}, {**ADDED, "id": "format"}, {"id": "unit"}])

    assert _ids(path) == ["lint", "format", "types", "unit", "coverage"]


def test_the_repository_resolves_its_controls_in_its_own_order() -> None:
    raw = json.loads((REPO_ROOT / "lictor.json").read_text(encoding="utf-8"))

    assert _ids(REPO_ROOT / "lictor.json") == [control["id"] for control in raw["controls"]]


def test_an_added_control_needs_what_the_profile_cant_give(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "audit", "run": "pip-audit"}])

    with pytest.raises(ConfigError, match="audit") as raised:
        load_config(path)
    assert "level" in str(raised.value)
    assert "report" in str(raised.value)
    assert "lint, types, unit, coverage" in str(raised.value)
    assert raised.value.exit_code == ExitCode.PRECONDITION


def test_dropping_a_control_the_profile_lacks_is_a_typo(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "tipos", "enabled": False}])

    with pytest.raises(ConfigError, match="tipos") as raised:
        load_config(path)
    assert "lint, types, unit, coverage" in str(raised.value)


def test_a_control_written_twice_is_refused(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "unit"}, {"id": "unit", "timeout_s": 5}])

    with pytest.raises(ConfigError, match="unit"):
        load_config(path)


def test_a_field_no_control_has_is_refused(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "unit", "timeot_s": 5}])

    with pytest.raises(ConfigError, match="timeot_s"):
        load_config(path)


def test_a_changed_control_is_still_checked_as_a_whole(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "unit", "report": "junit", "run": "pytest"}])

    with pytest.raises(ConfigError, match="out"):
        load_config(path)


def test_the_schema_describes_the_file_a_person_writes() -> None:
    schema = export_config_schema()
    defs = schema["$defs"]
    assert isinstance(defs, dict)

    assert schema["required"] == ["profile"]
    control = defs["ControlChange"]
    assert control["required"] == ["id"]
    assert "enabled" in control["properties"]
