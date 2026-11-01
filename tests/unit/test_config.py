import ast
import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from lictor.adapters.config.loader import (
    DEFAULT_PROFILES_DIR,
    build_config_from_profile,
    config_fingerprint,
    detect_profile,
    export_config_schema,
    init_config_file,
    list_profiles,
    load_config,
    load_profile,
    resolve_defences,
)
from lictor.adapters.config.models import (
    AcceptanceConfig,
    BackendConfig,
    ControlConfig,
    LictorConfig,
    PathsConfig,
    Profile,
    ProfileVerification,
    ProjectVerification,
)
from lictor.adapters.config.profiles.python_uv import lictor_project_path
from lictor.adapters.config.profiles.python_uv.lictor_project_path import (
    pytest_load_initial_conftests,
)
from lictor.domain.errors import ConfigError, ExitCode

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_load_python_uv_profile() -> None:
    profile = load_profile("python-uv")
    assert profile.name == "python-uv"
    assert profile.version == "0.1"
    assert profile.status == "stable"
    assert profile.detect == ["pyproject.toml"]
    assert "tests/acceptance/" in profile.paths.driver
    assert "tests/acceptance/" in profile.paths.protected
    assert len(profile.controls) == 3


def test_load_profile_with_version_suffix() -> None:
    profile = load_profile("python-uv@0.1")
    assert profile.name == "python-uv"
    assert profile.version == "0.1"


def test_load_profile_unknown_or_wrong_version_raises_config_error() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_profile("nonexistent-profile")
    assert exc_info.value.exit_code == ExitCode.PRECONDITION

    with pytest.raises(ConfigError):
        load_profile("python-uv@9.9")


def test_list_profiles_includes_python_uv() -> None:
    profiles = list_profiles()
    names = [p.name for p in profiles]
    assert "python-uv" in names


def test_controls_must_have_unique_ids() -> None:
    profile = load_profile("python-uv")
    dup_control = profile.controls[0]
    raw = profile.model_dump()
    raw["controls"] = [dup_control.model_dump(), dup_control.model_dump()]

    with pytest.raises(ValidationError, match="únicos"):
        Profile.model_validate(raw)

    cfg = build_config_from_profile(profile)
    cfg_raw = cfg.model_dump()
    cfg_raw["controls"] = [dup_control.model_dump(), dup_control.model_dump()]
    with pytest.raises(ValidationError, match="únicos"):
        LictorConfig.model_validate(cfg_raw)


@pytest.mark.parametrize("level", ["L1", "L2", "L4", "L5"])
def test_control_allowed_levels(level: str) -> None:
    ctrl = ControlConfig.model_validate(
        {
            "id": "chk",
            "level": level,
            "run": "cmd check",
            "report": "exit-code",
            "mode": "block",
        }
    )
    assert ctrl.level == level


@pytest.mark.parametrize("invalid_level", ["L0", "L3", "L6", "invalid"])
def test_control_rejects_disallowed_levels(invalid_level: str) -> None:
    with pytest.raises(ValidationError):
        ControlConfig.model_validate(
            {
                "id": "chk",
                "level": invalid_level,
                "run": "cmd check",
                "report": "exit-code",
                "mode": "block",
            }
        )


def test_junit_report_requires_out_placeholder_in_control() -> None:
    with pytest.raises(ValidationError, match=r"\{out\}"):
        ControlConfig.model_validate(
            {
                "id": "unit",
                "level": "L2",
                "run": "cmd test tests/unit",
                "report": "junit",
                "mode": "block",
            }
        )

    valid = ControlConfig.model_validate(
        {
            "id": "unit",
            "level": "L2",
            "run": "cmd test tests/unit --xml {out}",
            "report": "junit",
            "mode": "block",
        }
    )
    assert "{out}" in valid.run


def test_junit_report_requires_out_placeholder_in_acceptance() -> None:
    with pytest.raises(ValidationError, match=r"\{out\}"):
        AcceptanceConfig.model_validate(
            {
                "run": "cmd acceptance",
                "report": "junit",
            }
        )

    valid_exit = AcceptanceConfig.model_validate(
        {
            "run": "cmd acceptance",
            "report": "exit-code",
        }
    )
    assert valid_exit.report == "exit-code"


def test_paths_allows_driver_inside_protected() -> None:
    paths = PathsConfig(
        writable=["src/", "tests/unit/"],
        driver=["tests/acceptance/"],
        protected=["features/", "tests/acceptance/", "lictor.json"],
    )
    assert "tests/acceptance/" in paths.driver
    assert "tests/acceptance/" in paths.protected


@pytest.mark.parametrize(
    ("writable", "driver", "protected"),
    [
        (["src/", "features/"], ["tests/acceptance/"], ["features/", "tests/acceptance/"]),
        (["src/"], ["tests/acceptance/"], ["src/internal/", "tests/acceptance/"]),
        (["src/sub/"], ["tests/acceptance/"], ["src/", "tests/acceptance/"]),
        (["src/", "tests/acceptance/"], ["tests/acceptance/"], ["tests/acceptance/"]),
        (["tests/"], ["tests/acceptance/"], ["features/", "tests/acceptance/"]),
        (["src/", "tests/acceptance/"], ["tests/acceptance/"], ["features/"]),
    ],
)
def test_paths_rejects_overlap_between_writable_and_protected_or_driver(
    writable: list[str],
    driver: list[str],
    protected: list[str],
) -> None:
    with pytest.raises(ValidationError, match="solap"):
        PathsConfig(writable=writable, driver=driver, protected=protected)


@pytest.mark.parametrize(
    "short_alias",
    [
        "opus",
        "sonnet",
        "haiku",
        "default",
        "latest",
        "claude",
        "claude-opus",
        "claude-sonnet",
        "claude-haiku",
        "claude-sonnet-latest",
        "   ",
    ],
)
def test_backend_model_rejects_short_aliases(short_alias: str) -> None:
    with pytest.raises(ValidationError):
        BackendConfig(model=short_alias)


@pytest.mark.parametrize(
    "full_model_id",
    [
        "claude-sonnet-4-5-20250929",
        "claude-opus-4-6",
        "claude-haiku-4-5-20251001",
    ],
)
def test_backend_model_accepts_full_ids(full_model_id: str) -> None:
    backend = BackendConfig(model=full_model_id)
    assert backend.model == full_model_id


def test_detect_profile_by_pyproject_toml(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    profile = detect_profile(tmp_path)
    assert profile.name == "python-uv"


def test_detect_profile_raises_when_no_match(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        detect_profile(tmp_path)
    assert exc_info.value.exit_code == ExitCode.PRECONDITION


def test_init_config_file_generates_and_does_not_overwrite_unless_forced(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

    cfg = init_config_file(tmp_path)
    config_path = tmp_path / "lictor.json"
    assert config_path.is_file()
    assert cfg.version == "0.1"
    assert cfg.profile == "python-uv@0.1"

    raw_disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert "detect" not in raw_disk
    assert "status" not in raw_disk
    assert raw_disk["profile"] == "python-uv@0.1"

    loaded = load_config(tmp_path)
    assert loaded == cfg

    # No sobrescribe si force=False
    with pytest.raises(ConfigError) as exc_info:
        init_config_file(tmp_path, model="claude-opus-4-6", force=False)
    assert exc_info.value.exit_code == ExitCode.PRECONDITION
    assert load_config(tmp_path).backend.model == cfg.backend.model

    # Con force=True sí sobrescribe
    updated = init_config_file(tmp_path, model="claude-opus-4-6", force=True)
    assert updated.backend.model == "claude-opus-4-6"
    assert load_config(tmp_path).backend.model == "claude-opus-4-6"


def test_load_config_from_repo_root_lictor_json() -> None:
    cfg = load_config(REPO_ROOT / "lictor.json")
    assert cfg.profile == "python-uv@0.1"
    assert cfg.paths.writable == ["src/", "tests/unit/"]
    assert any(c.id == "architecture" for c in cfg.controls)


def test_load_config_errors_on_missing_or_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.json")

    bad_json = tmp_path / "lictor.json"
    bad_json.write_text("{bad json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad_json)

    invalid_cfg = tmp_path / "invalid.json"
    invalid_cfg.write_text(json.dumps({"profile": "python-uv@0.1", "backend": {"model": "opus"}}))
    with pytest.raises(ConfigError):
        load_config(invalid_cfg)


def test_acceptance_failure_exit_codes_default_to_one() -> None:
    assert AcceptanceConfig(run="suite {out}").failure_exit_codes == [1]


def test_acceptance_failure_exit_codes_come_from_the_profile() -> None:
    assert load_profile("python-uv").acceptance.failure_exit_codes == [1]


@pytest.mark.parametrize("codes", [[], [0], [1, 0]])
def test_acceptance_failure_exit_codes_reject_invalid_values(codes: list[int]) -> None:
    with pytest.raises(ValidationError):
        AcceptanceConfig(run="suite {out}", failure_exit_codes=codes)


def test_project_acceptance_inherits_the_failure_codes_of_its_profile(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    profile = load_profile("python-uv").model_dump(mode="json")
    profile["acceptance"]["failure_exit_codes"] = [1, 2]
    (profiles / "python-uv.json").write_text(json.dumps(profile), encoding="utf-8")
    config = tmp_path / "lictor.json"
    config.write_text(
        json.dumps(
            {
                "profile": "python-uv@0.1",
                "acceptance": {"run": "otra {out}", "report": "junit"},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_config(config, profiles)

    assert loaded.acceptance.run == "otra {out}"
    assert loaded.acceptance.failure_exit_codes == [1, 2]


def test_profile_declares_how_verification_stays_out_of_the_agents_reach() -> None:
    profile = load_profile("python-uv")
    env = profile.verification.env
    assert "--no-install-project" in profile.verification.prepare
    assert "--frozen" in profile.verification.prepare
    assert env["UV_PROJECT_ENVIRONMENT"] == "{env_dir}"
    assert env["UV_LINK_MODE"] == "copy"
    assert env["UV_NO_SYNC"] == "1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["UV_NO_ENV_FILE"] == "1"
    assert env["PYTEST_ADDOPTS"] == "-o pythonpath="
    assert profile.verification.plugin_variable == "PYTEST_PLUGINS"
    assert "PYTHONPATH" not in env


def test_profile_reaches_the_sources_only_after_the_tool_has_started() -> None:
    spec = resolve_defences(load_config(REPO_ROOT)).verification

    assert [path.name for path in spec.files] == ["lictor_project_path.py"]
    assert all(path.is_file() for path in spec.files)
    assert spec.files_dir_command.startswith("{env_dir}/bin/python -I -B -c")
    assert "get_path" in spec.files_dir_command


def test_the_path_plugin_appends_the_sources_of_the_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", ["primero"])
    config = SimpleNamespace(invocation_params=SimpleNamespace(dir=tmp_path))

    pytest_load_initial_conftests(cast(pytest.Config, config))

    assert sys.path == ["primero", str(tmp_path / "src")]


def test_the_path_plugin_stands_alone() -> None:
    """It is installed on its own in an environment where Lictor doesn't exist."""
    tree = ast.parse(Path(lictor_project_path.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported |= {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert imported == {"pytest", "sys", "typing"}


def test_a_profile_file_that_is_missing_is_a_configuration_error(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    data = load_profile("python-uv").model_dump(mode="json")
    (profiles / "python-uv.json").write_text(json.dumps(data), encoding="utf-8")
    config = load_config(_project(tmp_path), profiles_dir=profiles)

    with pytest.raises(ConfigError, match=r"lictor_project_path\.py"):
        resolve_defences(config, profiles)


def test_profile_files_need_a_command_that_says_where_they_go() -> None:
    data = load_profile("python-uv").model_dump(mode="json")
    data["verification"]["files_dir_command"] = ""

    with pytest.raises(ValidationError, match="files_dir_command"):
        Profile.model_validate(data)


# Each entry holds because of something measured with an exact environment. The commands get almost
# nothing: the network settings and credentials stay with `prepare`, out of reach of project code.
INHERITED = {
    "PATH": "without it the tools can't be found",
    "TMPDIR": "sandboxes and CI may only allow writing there",
}
PREPARE_INHERITED = {
    **INHERITED,
    "NETRC": "credentials of a private index",
    "HTTP_PROXY": "the network may only be reachable through it",
    "HTTPS_PROXY": "the network may only be reachable through it",
    "ALL_PROXY": "the network may only be reachable through it",
    "NO_PROXY": "exceptions to the proxy",
    "http_proxy": "the lowercase spelling is read as well",
    "https_proxy": "the lowercase spelling is read as well",
    "all_proxy": "the lowercase spelling is read as well",
    "no_proxy": "the lowercase spelling is read as well",
    "SSL_CERT_FILE": "corporate roots",
    "SSL_CERT_DIR": "corporate roots",
    "UV_SYSTEM_CERTS": "corporate roots in the system store",
    "REQUESTS_CA_BUNDLE": "corporate roots for the audit, which doesn't read the others",
}


def test_profile_inherits_exactly_what_was_justified() -> None:
    verification = load_profile("python-uv").verification
    assert sorted(verification.inherit) == sorted(INHERITED)
    assert sorted(verification.prepare_inherit) == sorted(PREPARE_INHERITED)


def test_profile_gives_prepare_and_the_commands_a_home_of_their_own() -> None:
    verification = load_profile("python-uv").verification
    assert verification.env["HOME"] == "{home_dir}"
    assert verification.prepare_ask == {"UV_PYTHON_INSTALL_DIR": "uv python dir"}
    assert verification.prepare_home_files == {"NETRC": ".netrc"}


def test_profile_prepares_without_a_cache_anyone_can_write() -> None:
    assert "--no-cache" in load_profile("python-uv").verification.prepare


@pytest.mark.parametrize(
    "name",
    [
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "PYTEST_ADDOPTS",
        "COVERAGE_PROCESS_START",
        "MYPYPATH",
        "RUFF_CONFIG",
        "UV_CONFIG_FILE",
        "UV_ENV_FILE",
        "UV_INDEX_URL",
        "UV_PROJECT",
        "UV_PYTHON",
        "VIRTUAL_ENV",
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "BASH_ENV",
        "ENV",
        "LANG",
        "LC_ALL",
    ],
)
def test_profile_doesnt_inherit_what_the_tools_dont_need(name: str) -> None:
    verification = load_profile("python-uv").verification
    assert name not in verification.inherit
    assert name not in verification.prepare_inherit


def test_invalid_banned_pattern_is_rejected() -> None:
    data = load_profile("python-uv").model_dump(mode="json")
    data["banned_patterns"] = ["("]
    with pytest.raises(ValidationError, match="expresión inválida"):
        Profile.model_validate(data)


def _project(tmp_path: Path, **fields: object) -> Path:
    path = tmp_path / "lictor.json"
    path.write_text(json.dumps({"profile": "python-uv@0.1", **fields}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "fields",
    [
        {"suppressions": []},
        {"banned_patterns": []},
        {"forbidden_paths": []},
        {"verification": {"prepare": "uv sync"}},
        {"verification": {"files": {}}},
        {"verification": {"unset": []}},
        {"verification": {"reserved": []}},
        {"verification": {"plugin_variable": "OTRA"}},
    ],
    ids=lambda fields: next(iter(fields.get("verification", fields))),
)
def test_a_project_cannot_declare_a_defence_of_the_profile(
    tmp_path: Path, fields: dict[str, object]
) -> None:
    with pytest.raises(ConfigError, match="Extra inputs"):
        load_config(_project(tmp_path, **fields))


@pytest.mark.parametrize(
    "name",
    [
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "UV_PROJECT_ENVIRONMENT",
        "UV_NO_SYNC",
        "UV_FROZEN",
        "UV_CONFIG_FILE",
        "VIRTUAL_ENV",
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "BASH_ENV",
        "ENV",
        "COVERAGE_PROCESS_START",
        "COV_CORE_SOURCE",
        "MYPYPATH",
        "RUFF_CONFIG",
        "PATH",
        "HOME",
        "NETRC",
        "HTTP_PROXY",
    ],
)
@pytest.mark.parametrize(
    "declare",
    [
        lambda name: {"env": {name: "x"}},
        lambda name: {"inherit": [name]},
        lambda name: {"prepare_inherit": [name]},
    ],
    ids=["env", "inherit", "prepare_inherit"],
)
def test_a_project_cannot_touch_a_reserved_variable(
    tmp_path: Path, name: str, declare: Callable[[str], dict[str, object]]
) -> None:
    config = load_config(_project(tmp_path, verification=declare(name)))

    with pytest.raises(ConfigError, match=name):
        resolve_defences(config)


def test_a_project_adds_what_it_needs_on_top_of_the_profile(tmp_path: Path) -> None:
    config = load_config(
        _project(
            tmp_path,
            verification={
                "env": {"MI_BASE": "sqlite://"},
                "inherit": ["MI_TOKEN"],
            },
        )
    )
    profile = load_profile("python-uv").verification

    spec = resolve_defences(config).verification

    assert spec.env == profile.env | {"MI_BASE": "sqlite://"}
    assert list(spec.inherit) == [*profile.inherit, "MI_TOKEN"]
    assert spec.prepare == profile.prepare


def test_plugins_are_not_a_project_decision(tmp_path: Path) -> None:
    """They come from what is installed: there is nothing left to declare, or to keep in sync."""
    with pytest.raises(ConfigError, match="plugins"):
        load_config(_project(tmp_path, verification={"plugins": ["pytest_bdd.plugin"]}))


@pytest.mark.parametrize("field", ["plugins", "plugins_command"])
def test_plugins_need_a_variable_to_reach_the_tool(field: str) -> None:
    value: object = ["otro"] if field == "plugins" else "lista"

    with pytest.raises(ValidationError, match="plugin_variable"):
        ProfileVerification.model_validate({field: value})


def test_the_profile_tells_the_environment_how_to_load_plugins(tmp_path: Path) -> None:
    profile = load_profile("python-uv").verification

    spec = resolve_defences(load_config(_project(tmp_path))).verification

    assert spec.plugin_variable == profile.plugin_variable == "PYTEST_PLUGINS"
    assert spec.plugins == profile.plugins
    assert spec.plugins_command == profile.plugins_command
    assert "PYTEST_PLUGINS" not in spec.env


def test_the_plugins_question_reads_what_is_really_installed() -> None:
    """Run the profile's command on this very environment: it lists modules, importing none."""
    command = load_profile("python-uv").verification.plugins_command
    tokens = shlex.split(command.replace("{env_dir}", sys.prefix))

    listed = subprocess.run(tokens, capture_output=True, text=True, check=True).stdout.split()

    assert {"pytest_cov.plugin", "xdist.plugin"} <= set(listed)


def test_a_new_defence_reaches_a_project_initialized_before_it(tmp_path: Path) -> None:
    """Nothing is frozen in `lictor.json`: this is what a copy of the block used to break."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    profile_data = load_profile("python-uv").model_dump(mode="json")
    init_config_file(tmp_path)
    profile_data["verification"]["env"]["DEFENSA_NUEVA"] = "1"
    profile_data["forbidden_paths"].append("nuevo.py")
    profile_data["banned_patterns"].append("nuevo")
    profile_data["suppressions"].append("# nuevo")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "python-uv.json").write_text(json.dumps(profile_data), encoding="utf-8")
    plugin = Path(lictor_project_path.__file__)
    (profiles / "python_uv").mkdir()
    (profiles / "python_uv" / plugin.name).write_text("", encoding="utf-8")

    defences = resolve_defences(load_config(tmp_path, profiles), profiles)

    assert defences.verification.env["DEFENSA_NUEVA"] == "1"
    assert "nuevo.py" in defences.forbidden_paths
    assert "nuevo" in defences.banned_patterns
    assert "# nuevo" in defences.suppressions


def test_unknown_profile_cannot_skip_the_defences(tmp_path: Path) -> None:
    config = load_config(_project(tmp_path)).model_copy(update={"profile": "otro@0.1"})

    with pytest.raises(ConfigError, match="otro"):
        resolve_defences(config)


def test_init_copies_no_defence(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    init_config_file(tmp_path)

    written = json.loads((tmp_path / "lictor.json").read_text(encoding="utf-8"))
    assert written["verification"] == {"env": {}, "inherit": [], "prepare_inherit": []}
    assert not {"suppressions", "banned_patterns", "forbidden_paths"} & set(written)


def test_demo_declares_nothing_of_its_own() -> None:
    assert load_config(REPO_ROOT / "examples" / "demo-iva" / "lictor.json").verification == (
        ProjectVerification()
    )


def test_profile_no_longer_assumes_a_project_dependency() -> None:
    verification = load_profile("python-uv").verification

    assert verification.plugins == ["lictor_project_path"]
    assert "PYTEST_PLUGINS" not in verification.env


def test_the_plugins_question_ignores_the_working_directory_and_the_environment(
    tmp_path: Path,
) -> None:
    """A distribution planted where Python would look must not become a loaded plugin."""
    info = tmp_path / "trampa-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: trampa\nVersion: 1.0\n")
    (info / "entry_points.txt").write_text("[pytest11]\ntrampa = trampa\n")
    command = load_profile("python-uv").verification.plugins_command
    tokens = shlex.split(command.replace("{env_dir}", sys.prefix))

    listed = subprocess.run(
        tokens,
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
        env={"PYTHONPATH": str(tmp_path)},
    ).stdout.split()

    assert "trampa" not in listed
    assert "xdist.plugin" in listed


def _fingerprint(root: Path, profiles: Path | None = None) -> str:
    config = load_config(root, profiles)
    return config_fingerprint(config, resolve_defences(config, profiles))


def _profiles_copy(dest: Path) -> Path:
    shutil.copytree(DEFAULT_PROFILES_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def test_fingerprint_ignores_the_format_of_lictor_json(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = _fingerprint(root)
    data = json.loads(root.read_text(encoding="utf-8"))

    root.write_text(json.dumps(data, indent=8, sort_keys=True), encoding="utf-8")

    assert _fingerprint(root) == before


def test_fingerprint_changes_with_lictor_json(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = _fingerprint(root)

    assert _fingerprint(_project(tmp_path, verification={"env": {"MIO": "1"}})) != before


def test_fingerprint_does_not_depend_on_the_key_order_of_the_profile(tmp_path: Path) -> None:
    root = _project(tmp_path)
    profiles = _profiles_copy(tmp_path / "perfiles")
    before = _fingerprint(root, profiles)
    path = profiles / "python-uv.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["verification"]["env"] = dict(reversed(data["verification"]["env"].items()))
    path.write_text(json.dumps(data), encoding="utf-8")

    assert _fingerprint(root, profiles) == before


def test_fingerprint_does_not_depend_on_where_lictor_is_installed(tmp_path: Path) -> None:
    root = _project(tmp_path)

    first = _fingerprint(root, _profiles_copy(tmp_path / "aqui"))
    second = _fingerprint(root, _profiles_copy(tmp_path / "alli" / "otro"))

    assert first == second


@pytest.mark.parametrize(
    "field",
    [
        "prepare",
        "prepare_ask",
        "prepare_home_files",
        "files_dir_command",
        "plugin_variable",
        "plugins",
        "plugins_command",
    ],
)
def test_fingerprint_covers_every_field_of_the_verification(tmp_path: Path, field: str) -> None:
    config = load_config(_project(tmp_path))
    defences = resolve_defences(config)
    current = getattr(defences.verification, field)
    if isinstance(current, str):
        changed: object = f"{current}x"
    elif isinstance(current, dict):
        changed = {**current, "NUEVA": "1"}
    else:
        changed = [*current, "nuevo"]
    other = replace(defences, verification=replace(defences.verification, **{field: changed}))

    assert config_fingerprint(config, other) != config_fingerprint(config, defences)


@pytest.mark.parametrize("field", ["suppressions", "banned_patterns", "forbidden_paths"])
def test_fingerprint_covers_the_heuristics(tmp_path: Path, field: str) -> None:
    config = load_config(_project(tmp_path))
    defences = resolve_defences(config)

    other = replace(defences, **{field: [*getattr(defences, field), "nuevo"]})

    assert config_fingerprint(config, other) != config_fingerprint(config, defences)


def test_fingerprint_covers_the_content_of_the_profile_files(tmp_path: Path) -> None:
    root = _project(tmp_path)
    profiles = _profiles_copy(tmp_path / "perfiles")
    before = _fingerprint(root, profiles)

    plugin = profiles / "python_uv" / "lictor_project_path.py"
    plugin.write_text(plugin.read_text(encoding="utf-8") + "\n# nuevo\n", encoding="utf-8")

    assert _fingerprint(root, profiles) != before


def test_json_schema_export_matches_committed_schema(tmp_path: Path) -> None:
    out_file = tmp_path / "lictor.schema.json"
    schema = export_config_schema(out_file)
    assert out_file.is_file()
    assert json.loads(out_file.read_text(encoding="utf-8")) == schema

    repo_schema_path = REPO_ROOT / "schema" / "lictor.schema.json"
    assert repo_schema_path.is_file()
    assert json.loads(repo_schema_path.read_text(encoding="utf-8")) == schema


def test_profile_agent_commands_are_runnable_by_the_agent() -> None:
    profile = load_profile("python-uv")
    prefixes = [tool[len("Bash(") : -len(":*)")] for tool in profile.agent_tools]

    assert profile.agent_commands
    for command in profile.agent_commands:
        assert "{" not in command
        assert any(command.startswith(prefix) for prefix in prefixes), command


def test_profile_agent_commands_include_the_acceptance_suite() -> None:
    profile = load_profile("python-uv")
    target = profile.acceptance.run.split(" --")[0]

    assert target in profile.agent_commands
