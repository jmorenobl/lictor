"""What a control is once a change of lictor.json applies over its base (ADR-0008, point 2)."""

import json
import shutil
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from lictor.adapters.config.loader import (
    DEFAULT_PROFILES_DIR,
    build_config_from_profile,
    config_fingerprint,
    load_config,
    load_profile,
    resolve_defences,
    resolve_shape,
)
from lictor.adapters.config.models import (
    CommandControlConfig,
    ControlChange,
    ControlConfig,
    LictorConfig,
    Profile,
    ReaderControlConfig,
)
from lictor.domain.errors import ConfigError

RUNS: dict[str, object] = {
    "id": "unit",
    "level": "L2",
    "run": "pytest --junitxml {out}",
    "report": "junit",
    "mode": "block",
    "stage": "attempt",
    "empty_exit_codes": [5],
    "timeout_s": 600,
    "when_exists": "tests/unit",
    "when_plugin": None,
    "min_score": None,
}
COVERS: dict[str, object] = {**RUNS, "id": "coverage", "report": "coverage", "run": "c {out}"}
READS: dict[str, object] = {
    "id": "coverage",
    "level": "L2",
    "reads": "unit",
    "adds": "--cov {out}",
    "report": "coverage",
    "mode": "measure",
    "stage": "attempt",
    "when_plugin": "cov.plugin",
    "min_score": None,
}
_CONTROL: TypeAdapter[ControlConfig] = TypeAdapter(ControlConfig)


def _shape(base: dict[str, object] | None, **written: object) -> dict[str, object]:
    control_id = base["id"] if base is not None else "nuevo"
    return resolve_shape(base, ControlChange.model_validate({"id": control_id, **written}))


def _refused(base: dict[str, object] | None, match: str, **written: object) -> None:
    with pytest.raises(ConfigError, match=match):
        _shape(base, **written)


def _kind(resolved: dict[str, object]) -> type:
    return type(_CONTROL.validate_python(resolved))


# One test per row of the table, in its order.


def test_runs_and_nothing_of_a_shape_is_written_merges_as_today() -> None:
    resolved = _shape(RUNS, mode="measure")

    assert resolved == {**RUNS, "mode": "measure"}
    assert _kind(resolved) is CommandControlConfig


def test_runs_and_a_new_run_keeps_the_rest_of_its_base() -> None:
    resolved = _shape(RUNS, run="pytest -n auto --junitxml {out}")

    assert (resolved["empty_exit_codes"], resolved["when_exists"]) == ([5], "tests/unit")
    assert _kind(resolved) is CommandControlConfig


def test_runs_and_reads_and_adds_with_coverage_make_a_reader() -> None:
    from_base = _shape(COVERS, reads="unit", adds="--cov {out}")
    written = _shape(RUNS, reads="lint", adds="--cov {out}", report="coverage")

    for resolved in (from_base, written):
        assert not {"run", "empty_exit_codes", "timeout_s", "when_exists"} & set(resolved)
        assert _kind(resolved) is ReaderControlConfig


def test_runs_and_reads_with_another_report_is_refused() -> None:
    _refused(
        RUNS,
        'solo puede ser de cobertura: su report es "coverage", no "junit"',
        reads="x",
        adds="a {out}",
    )


def test_runs_and_reads_with_a_run_field_is_refused() -> None:
    _refused(COVERS, "timeout_s es de una ejecución", reads="unit", adds="a {out}", timeout_s=9)


@pytest.mark.parametrize("written", [{"reads": "unit"}, {"adds": "a {out}"}])
def test_runs_and_reads_or_adds_alone_is_refused(written: dict[str, object]) -> None:
    _refused(COVERS, "hacen falta reads y adds", **written)


def test_reads_and_nothing_of_a_shape_is_written_merges() -> None:
    resolved = _shape(READS, mode="block", min_score=80)

    assert resolved == {**READS, "mode": "block", "min_score": 80}
    assert _kind(resolved) is ReaderControlConfig


def test_reads_and_only_adds_changes_only_adds() -> None:
    resolved = _shape(READS, adds="--cov=lib {out}")

    assert resolved == {**READS, "adds": "--cov=lib {out}"}


def test_reads_and_another_report_is_refused() -> None:
    _refused(READS, 'no "junit"', report="junit")


def test_reads_and_a_run_runs_with_the_run_fields_it_writes_or_their_defaults() -> None:
    """How Lictor's own lictor.json gives `coverage` its run."""
    resolved = _shape(READS, run="pytest tests --cov-report=json:{out}", timeout_s=4500)

    assert "reads" not in resolved
    assert "adds" not in resolved
    control = _CONTROL.validate_python(resolved)
    assert isinstance(control, CommandControlConfig)
    assert (control.report, control.timeout_s, control.empty_exit_codes) == ("coverage", 4500, [])
    assert control.when_exists is None


@pytest.mark.parametrize(
    "written", [{"timeout_s": 9}, {"empty_exit_codes": [5]}, {"when_exists": None}]
)
def test_reads_and_a_run_field_without_run_is_refused(written: dict[str, object]) -> None:
    """A null written is written: `when_exists: null` on a reader would apply nothing."""
    _refused(READS, "es de una ejecución y no se aplicaría.*escríbelo en 'unit'", **written)


def test_several_run_fields_are_named_together() -> None:
    _refused(
        READS,
        "timeout_s y when_exists son de una ejecución y no se aplicarían. Quítalos, o escríbelos",
        timeout_s=9,
        when_exists="tests",
    )


@pytest.mark.parametrize("base", [RUNS, READS, None])
def test_run_and_reads_together_are_two_shapes(base: dict[str, object] | None) -> None:
    _refused(base, "no las dos cosas", run="x", reads="unit", level="L2", report="junit")


def test_a_new_id_with_level_report_and_run_runs() -> None:
    resolved = _shape(None, level="L4", report="exit-code", run="arch")

    assert _kind(resolved) is CommandControlConfig


def test_a_new_id_with_level_coverage_reads_and_adds_reads() -> None:
    resolved = _shape(None, level="L2", report="coverage", reads="unit", adds="a {out}")

    assert _kind(resolved) is ReaderControlConfig


@pytest.mark.parametrize(
    ("written", "match"),
    [
        ({"level": "L2"}, r"así que lo añade, pero no dice qué ejecuta\. Un control que"),
        ({"run": "x"}, "escribe también level y report"),
        ({"reads": "unit", "level": "L2"}, "escribe también report y adds"),
        (
            {"reads": "unit", "adds": "a {out}", "level": "L2", "report": "junit"},
            "solo puede ser de cobertura",
        ),
        (
            {"reads": "u", "adds": "a {out}", "level": "L2", "report": "coverage", "timeout_s": 1},
            "timeout_s es de una ejecución",
        ),
    ],
)
def test_a_new_id_otherwise_names_both_shapes_or_what_is_missing(
    written: dict[str, object], match: str
) -> None:
    _refused(None, match, **written)


# Around the table.


def _project(tmp_path: Path, **fields: object) -> Path:
    path = tmp_path / "lictor.json"
    path.write_text(json.dumps({"profile": "con-lector", **fields}), encoding="utf-8")
    return path


@pytest.fixture
def profiles(tmp_path: Path) -> Path:
    """The real profile with its coverage as a reader of unit."""
    dest = tmp_path / "perfiles"
    shutil.copytree(DEFAULT_PROFILES_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"))
    data = json.loads((dest / "python-uv.json").read_text(encoding="utf-8"))
    data["name"] = "con-lector"
    data["controls"] = [
        c if c["id"] != "coverage" else {**READS, "when_plugin": "pytest_cov.plugin"}
        for c in data["controls"]
    ]
    (dest / "python-uv.json").unlink()
    (dest / "con-lector.json").write_text(json.dumps(data), encoding="utf-8")
    return dest


def test_a_reader_resolves_from_the_profile(tmp_path: Path, profiles: Path) -> None:
    config = load_config(_project(tmp_path), profiles)

    coverage = next(c for c in config.controls if c.id == "coverage")
    assert isinstance(coverage, ReaderControlConfig)


def test_dropping_a_control_writes_nothing_else(tmp_path: Path, profiles: Path) -> None:
    root = _project(tmp_path, controls=[{"id": "coverage", "enabled": False, "mode": "block"}])

    with pytest.raises(ConfigError, match="enabled: false, así que mode no se aplicaría"):
        load_config(root, profiles)


@pytest.mark.parametrize(
    "field",
    ["run", "reads", "adds", "level", "report", "mode", "stage", "timeout_s", "empty_exit_codes"],
)
def test_a_null_that_would_mean_nothing_is_refused(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        ControlChange.model_validate({"id": "coverage", field: None})


@pytest.mark.parametrize("field", ["when_exists", "when_plugin", "min_score"])
def test_a_null_clears_what_takes_it(field: str) -> None:
    assert ControlChange.model_validate({"id": "unit", field: None}).model_fields_set == {
        "id",
        field,
    }


def test_the_schema_takes_no_null_where_it_would_mean_nothing() -> None:
    properties = ControlChange.model_json_schema()["properties"]

    for field in ("run", "reads", "adds", "level", "report", "mode", "stage", "timeout_s"):
        assert "null" not in json.dumps(properties[field]), field
        assert "default" not in properties[field], field


def test_a_control_with_neither_run_nor_reads_names_both() -> None:
    with pytest.raises(ValidationError, match="'x' no dice qué ejecuta: escribe run, o reads"):
        _CONTROL.validate_python({"id": "x", "level": "L2", "report": "junit"})


def test_a_control_with_adds_and_no_reads_is_a_reader_missing_reads() -> None:
    """Built by hand or in a profile, never the error of a union's discriminator."""
    with pytest.raises(ValidationError, match=r"reads\n  Field required"):
        _CONTROL.validate_python({"id": "c", "level": "L2", "adds": "{out}", "report": "coverage"})


def test_a_profile_cant_retire_its_own_version() -> None:
    data = load_profile("python-uv").model_dump(mode="json")
    data["retired_versions"] = {data["version"]: "nada"}

    with pytest.raises(ValidationError, match="retira su propia versión"):
        Profile.model_validate(data)


def test_an_acceptance_that_doesnt_split_is_refused_with_its_own_message(tmp_path: Path) -> None:
    root = _project(tmp_path, acceptance={"run": "pytest '{out}"})

    with pytest.raises(ConfigError, match=r"acceptance no se puede trocear") as refused:
        load_config(root, DEFAULT_PROFILES_DIR)
    assert "input_value" not in str(refused.value)


def test_a_reader_with_a_run_field_cant_be_built() -> None:
    with pytest.raises(ValidationError, match="timeout_s"):
        _CONTROL.validate_python({**READS, "timeout_s": 5})


def test_a_reader_needs_out_in_adds() -> None:
    with pytest.raises(ValidationError, match=r"'\{out\}' en adds"):
        _CONTROL.validate_python({**READS, "adds": "--cov"})


def test_adds_that_dont_split_are_refused_naming_the_control() -> None:
    with pytest.raises(ValidationError, match=r"adds del control 'coverage'.*No closing"):
        _CONTROL.validate_python({**READS, "adds": "'--cov {out}"})


def test_a_hand_built_configuration_follows_the_rules_of_the_pairing() -> None:
    """ADR-0008, point 2: a `LictorConfig` built without `load_config` can't skip them."""
    config = build_config_from_profile(load_profile("python-uv")).model_dump(mode="json")
    config["controls"] = [READS]

    with pytest.raises(ValidationError, match="'unit', que no está entre los controles activos"):
        LictorConfig.model_validate(config)
    config["controls"] = [{**RUNS, "report": "exit-code", "run": "x"}, READS]
    with pytest.raises(ValidationError, match="cuyo report es 'exit-code'"):
        LictorConfig.model_validate(config)


def test_a_profile_with_an_orphan_reader_doesnt_load() -> None:
    data = load_profile("python-uv").model_dump(mode="json")
    others = [c for c in data["controls"] if c["id"] not in ("unit", "coverage")]
    data["controls"] = [*others, {**READS, "when_plugin": "pytest_cov.plugin"}]

    with pytest.raises(ValidationError, match="no está entre los controles activos"):
        Profile.model_validate(data)


def test_a_new_reader_written_before_its_read_control_is_refused(
    tmp_path: Path, profiles: Path
) -> None:
    reader = {**READS, "id": "otra", "reads": "propio", "when_plugin": None}
    reader.pop("stage")
    own = {"id": "propio", "level": "L2", "run": "p --junitxml {out}", "report": "junit"}
    root = _project(tmp_path, controls=[{"id": "coverage", "enabled": False}, reader, own])

    with pytest.raises(ConfigError, match="'otra' va antes que 'propio'"):
        load_config(root, profiles)


def test_dropping_unit_leaves_coverage_orphan_and_says_how_out(
    tmp_path: Path, profiles: Path
) -> None:
    root = _project(tmp_path, controls=[{"id": "unit", "enabled": False}])

    with pytest.raises(ConfigError, match=r'quita también .coverage. \(\{"id": "coverage"'):
        load_config(root, profiles)


def test_unit_only_in_check_with_coverage_in_every_attempt_says_how_out(
    tmp_path: Path, profiles: Path
) -> None:
    root = _project(tmp_path, controls=[{"id": "unit", "stage": "check"}])

    with pytest.raises(ConfigError, match=r"pon 'coverage' también en check") as refused:
        load_config(root, profiles)
    # Its own message, not pydantic's dump of the whole configuration around it.
    assert "input_value" not in str(refused.value)


def test_both_only_in_check_load(tmp_path: Path, profiles: Path) -> None:
    changes = [{"id": "unit", "stage": "check"}, {"id": "coverage", "stage": "check"}]

    config = load_config(_project(tmp_path, controls=changes), profiles)

    assert {c.id: c.stage for c in config.controls}["coverage"] == "check"


def test_allow_empty_on_a_reader_says_its_read_controls_codes_apply(
    tmp_path: Path, profiles: Path
) -> None:
    root = _project(tmp_path, controls=[{"id": "coverage", "allow_empty": True}])

    with pytest.raises(
        ConfigError,
        match=r"lee la ejecución de 'unit'.*valen los de 'unit', que "
        r"declara \[5\]",
    ):
        load_config(root, profiles)


def test_the_fingerprint_changes_with_adds(tmp_path: Path, profiles: Path) -> None:
    def fingerprint(**fields: object) -> str:
        config = load_config(_project(tmp_path, **fields), profiles)
        return config_fingerprint(config, resolve_defences(config, profiles))

    assert fingerprint() != fingerprint(controls=[{"id": "coverage", "adds": "--cov=x {out}"}])
