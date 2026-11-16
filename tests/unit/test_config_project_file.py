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
# 0.2.0 computed b029641d…; `stage` (0.4.0, ADR-0007) enters every control, so the hash moved.
FINGERPRINT_IN_0_2_0 = "44435574ce0278abd2d4173cddb9a925159cfcc2fb46001a9e4d8f809cd54775"
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
    """A full copy still means what it meant: it resolves to itself, every control in `attempt`.

    The hash was the one `v0.2.0` computed for this same file until `stage` entered every
    control (ADR-0007, no compatibility kept). Otherwise it changes only if the copy stops
    resolving to itself, or if a defence of the profile changes.
    """
    copied = _python_project(tmp_path) / "lictor.json"
    copied.write_text(WRITTEN_BY_0_2_0.read_text(encoding="utf-8"), encoding="utf-8")
    written = json.loads(WRITTEN_BY_0_2_0.read_text(encoding="utf-8"))

    resolved = load_config(copied).model_dump(mode="json")

    controls = [{**control, "stage": "attempt"} for control in written["controls"]]
    assert resolved == {**written, "controls": controls}
    assert _fingerprint(copied) == FINGERPRINT_IN_0_2_0


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


def test_every_control_of_the_profile_runs_in_each_attempt(tmp_path: Path) -> None:
    root = _python_project(tmp_path)
    init_config_file(root)

    assert {c.stage for c in load_config(root).controls} == {"attempt"}


def test_the_stage_of_a_profile_control_is_written_by_id(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "unit", "stage": "check"}])

    controls = {c.id: c for c in load_config(path).controls}

    profile_unit = next(c for c in load_profile("python-uv").controls if c.id == "unit")
    assert controls["unit"].stage == "check"
    assert controls["unit"].mode == profile_unit.mode
    assert controls["unit"].run == profile_unit.run
    assert controls["lint"].stage == "attempt"
    assert _ids(path) == PROFILE_IDS


def test_an_added_control_may_declare_its_stage(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{**ADDED, "stage": "check"}])

    audit = next(c for c in load_config(path).controls if c.id == "audit")

    assert audit.stage == "check"


def test_an_added_control_runs_in_each_attempt_unless_it_says_otherwise(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [ADDED])

    audit = next(c for c in load_config(path).controls if c.id == "audit")

    assert audit.stage == "attempt"


def test_a_stage_that_doesnt_exist_is_refused(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "unit", "stage": "run_end"}])

    with pytest.raises(ConfigError, match="stage"):
        load_config(path)


def test_the_stage_of_a_control_enters_the_fingerprint(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "c").mkdir()
    attempt = _with_controls(tmp_path / "a", [{"id": "unit"}])
    check = _with_controls(tmp_path / "c", [{"id": "unit", "stage": "check"}])

    assert _fingerprint(attempt) != _fingerprint(check)


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
