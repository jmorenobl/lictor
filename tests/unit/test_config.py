import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from lictor.adapters.config.loader import (
    build_config_from_profile,
    detect_profile,
    export_config_schema,
    init_config_file,
    list_profiles,
    load_config,
    load_profile,
)
from lictor.adapters.config.models import (
    AcceptanceConfig,
    BackendConfig,
    ControlConfig,
    LictorConfig,
    PathsConfig,
    Profile,
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
    assert env["PYTEST_PLUGINS"] == "pytest_bdd.plugin,lictor_project_path"
    assert "PYTHONPATH" not in env


def test_profile_reaches_the_sources_only_after_the_tool_has_started() -> None:
    files = load_profile("python-uv").verification.files

    plugin = files["lib/python*/site-packages/lictor_project_path.py"]
    assert "def pytest_load_initial_conftests(early_config):" in plugin
    assert 'sys.path.append(str(early_config.invocation_params.dir / "src"))' in plugin
    assert len(files) == 1


# Each entry holds because of something measured with an exact environment: a variable the tools
# read to find themselves, their cache or the network. Nothing here names code to load.
INHERITED = {
    "PATH": "without it the tools can't be found",
    "HOME": "where the tools keep their state, when it differs from the user database",
    "TMPDIR": "sandboxes and CI may only allow writing there",
    "XDG_CACHE_HOME": "moves the cache of uv",
    "XDG_DATA_HOME": "moves the interpreters uv manages",
    "UV_CACHE_DIR": "moves the cache of uv",
    "UV_PYTHON_INSTALL_DIR": "moves the interpreters uv manages",
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
    assert sorted(load_profile("python-uv").verification.inherit) == sorted(INHERITED)


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
    assert name not in load_profile("python-uv").verification.inherit


def test_invalid_banned_pattern_is_rejected() -> None:
    data = load_profile("python-uv").model_dump(mode="json")
    data["banned_patterns"] = ["("]
    with pytest.raises(ValidationError, match="expresión inválida"):
        Profile.model_validate(data)


def test_old_project_config_inherits_the_verification_of_its_profile(tmp_path: Path) -> None:
    config = load_config(REPO_ROOT)
    assert config.verification == load_profile("python-uv").verification
    assert config.forbidden_paths == load_profile("python-uv").forbidden_paths


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
