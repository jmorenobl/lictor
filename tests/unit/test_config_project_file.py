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
from lictor.adapters.config.models import DEFAULT_MODEL, CommandControlConfig
from lictor.domain.errors import ConfigError, ExitCode

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITTEN_BY_0_2_0 = REPO_ROOT / "tests" / "fixtures" / "lictor_0_2_0.json"
PROFILE_IDS = ["lint", "format", "types", "unit", "coverage", "complexity", "duplication", "api"]
ADDED = {"id": "audit", "level": "L4", "run": "pip-audit", "report": "exit-code"}


def _python_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nrequires-python = '>=3.12'\n", encoding="utf-8"
    )
    return root


def _with_controls(tmp_path: Path, controls: list[dict[str, object]]) -> Path:
    path = tmp_path / "lictor.json"
    path.write_text(json.dumps({"profile": "python-uv@0.2", "controls": controls}), "utf-8")
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


def test_a_lictor_json_of_0_2_0_is_refused_and_says_what_to_remove(tmp_path: Path) -> None:
    """A full copy writes `acceptance.report`, which ADR-0009 removes: no compatibility kept.

    The refusal names the field, so the person knows what to delete instead of guessing.
    """
    copied = _python_project(tmp_path) / "lictor.json"
    copied.write_text(WRITTEN_BY_0_2_0.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ConfigError, match=r"quita acceptance\.report"):
        load_config(copied)

    # Without it, the profile it asks for no longer exists (ADR-0008, point 11).
    data = json.loads(copied.read_text(encoding="utf-8"))
    del data["acceptance"]["report"]
    copied.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"python-uv@0\.1 ya no existe: usa python-uv@0\.2"):
        load_config(copied)


def test_without_controls_the_project_gets_the_profiles(tmp_path: Path) -> None:
    root = _python_project(tmp_path)
    init_config_file(root)

    assert load_config(root).controls == load_profile("python-uv").controls


def test_a_control_of_the_profile_changes_only_the_fields_written(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "unit", "mode": "measure", "timeout_s": 99}])

    unit = next(c for c in load_config(path).controls if c.id == "unit")

    profile_unit = next(c for c in load_profile("python-uv").controls if c.id == "unit")
    assert isinstance(unit, CommandControlConfig)
    assert isinstance(profile_unit, CommandControlConfig)
    assert unit.mode == "measure"
    assert unit.timeout_s == 99
    assert unit.run == profile_unit.run
    assert unit.when_exists == profile_unit.when_exists
    assert _ids(path) == PROFILE_IDS


def test_every_control_of_the_profile_runs_in_each_attempt(tmp_path: Path) -> None:
    root = _python_project(tmp_path)
    init_config_file(root)

    assert {c.stage for c in load_config(root).controls} == {"attempt"}


def test_the_stage_of_a_profile_control_is_written_by_id(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "types", "stage": "check"}])

    controls = {c.id: c for c in load_config(path).controls}

    profile_types = next(c for c in load_profile("python-uv").controls if c.id == "types")
    assert controls["types"].stage == "check"
    assert controls["types"].mode == profile_types.mode
    assert controls["types"].model_dump(exclude={"stage"}) == profile_types.model_dump(
        exclude={"stage"}
    )
    assert controls["lint"].stage == "attempt"
    assert _ids(path) == PROFILE_IDS


def test_unit_alone_in_check_leaves_coverage_without_a_run_to_read(tmp_path: Path) -> None:
    """ADR-0008, point 6: the reader can't run where its read control doesn't."""
    path = _with_controls(tmp_path, [{"id": "unit", "stage": "check"}])

    with pytest.raises(ConfigError, match=r"pon 'coverage' también en check.*run propio"):
        load_config(path)


def test_unit_and_coverage_in_check_measure_coverage_only_there(tmp_path: Path) -> None:
    path = _with_controls(
        tmp_path, [{"id": "unit", "stage": "check"}, {"id": "coverage", "stage": "check"}]
    )

    stages = {c.id: c.stage for c in load_config(path).controls}

    assert (stages["unit"], stages["coverage"]) == ("check", "check")


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
    attempt = _with_controls(tmp_path / "a", [{"id": "types"}])
    check = _with_controls(tmp_path / "c", [{"id": "types", "stage": "check"}])

    assert _fingerprint(attempt) != _fingerprint(check)


def test_enabled_false_drops_a_control_of_the_profile(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "types", "enabled": False}])

    assert _ids(path) == ["lint", "format", "unit", "coverage", "complexity", "duplication", "api"]


def test_an_id_the_profile_lacks_adds_a_control_at_the_end(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [ADDED])

    assert _ids(path) == [*PROFILE_IDS, "audit"]


def test_an_added_control_goes_after_the_one_written_before_it(tmp_path: Path) -> None:
    # Its run changes, so the profile's fix for it is refused unless written (F83).
    changed = {**ADDED, "id": "format", "agent_fix": None}
    path = _with_controls(tmp_path, [{"id": "lint"}, changed, {"id": "unit"}])

    assert _ids(path) == [
        "lint",
        "format",
        "types",
        "unit",
        "coverage",
        "complexity",
        "duplication",
        "api",
    ]


def test_the_repository_resolves_its_controls_in_its_own_order() -> None:
    raw = json.loads((REPO_ROOT / "lictor.json").read_text(encoding="utf-8"))

    assert _ids(REPO_ROOT / "lictor.json") == [control["id"] for control in raw["controls"]]


def test_an_added_control_needs_what_the_profile_cant_give(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "audit", "run": "pip-audit"}])

    with pytest.raises(ConfigError, match="audit") as raised:
        load_config(path)
    assert "level" in str(raised.value)
    assert "report" in str(raised.value)
    assert "lint, format, types, unit, coverage" in str(raised.value)
    assert raised.value.exit_code == ExitCode.PRECONDITION


def test_dropping_a_control_the_profile_lacks_is_a_typo(tmp_path: Path) -> None:
    path = _with_controls(tmp_path, [{"id": "tipos", "enabled": False}])

    with pytest.raises(ConfigError, match="tipos") as raised:
        load_config(path)
    assert "lint, format, types, unit, coverage" in str(raised.value)


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


def test_the_complexity_limit_is_the_projects_by_id(tmp_path: Path) -> None:
    """ADR-0013: the profile puts 10; a project writes another, as it writes a `min_score`."""
    path = _with_controls(tmp_path, [{"id": "complexity", "limit": 15}])
    (tmp_path / "b").mkdir()

    [complexity] = [c for c in load_config(path).controls if c.id == "complexity"]

    assert isinstance(complexity, CommandControlConfig)
    assert (complexity.limit, complexity.mode) == (15, "measure")
    assert _fingerprint(path) != _fingerprint(_with_controls(tmp_path / "b", []))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"id": "complexity", "limit": None}, "necesita limit"),
        ({"id": "complexity", "limit": 0}, "greater than 0"),
        ({"id": "lint", "limit": 10}, "solo vale con report='complexity'"),
        ({"id": "coverage", "limit": 10}, "solo vale con report=.complexity."),
        (
            {"id": "c", "level": "L1", "run": "medir {out}", "report": "complexity"},
            "necesita limit",
        ),
    ],
)
def test_a_limit_goes_with_a_complexity_report_and_nothing_else(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(_with_controls(tmp_path, [change]))


def _with_api(tmp_path: Path, api: object) -> Path:
    path = tmp_path / "lictor.json"
    path.write_text(json.dumps({"profile": "python-uv@0.2", "api": api}), "utf-8")
    return path


def test_what_the_person_accepts_to_break_is_read_and_decides_a_resume(tmp_path: Path) -> None:
    """ADR-0017: `api.accepted` changes a verdict, so it is in the fingerprint."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    accepting = _with_api(tmp_path / "a", {"accepted": ["pkg.storage.save_chain"]})
    nothing = _with_api(tmp_path / "b", {})

    assert load_config(accepting).api.accepted == ["pkg.storage.save_chain"]
    assert load_config(nothing).api.accepted == []
    assert _fingerprint(accepting) != _fingerprint(nothing)


def test_the_api_takes_nothing_but_what_is_accepted_and_its_base(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="otra"):
        load_config(_with_api(tmp_path, {"otra": "main"}))


def test_the_base_of_the_api_is_in_the_fingerprint(tmp_path: Path) -> None:
    """H4 of 0.20.0: with `api` in block, the base changes verdicts."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    based = _with_api(tmp_path / "a", {"base": "origin/main"})
    unbased = _with_api(tmp_path / "b", {})

    assert load_config(based).api.base == "origin/main"
    assert load_config(unbased).api.base is None
    assert _fingerprint(based) != _fingerprint(unbased)


@pytest.mark.parametrize("ref", ["-x", "a..b", "a b", "rama.lock", "rama/", "HEAD@{1}", ""])
def test_a_base_of_the_api_git_couldnt_name_is_refused(tmp_path: Path, ref: str) -> None:
    with pytest.raises(ConfigError, match="no vale como base de la API"):
        load_config(_with_api(tmp_path, {"base": ref}))
