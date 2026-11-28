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
from typing import Any, cast

import pytest
import yaml
from pydantic import ValidationError

from lictor.adapters.config.loader import (
    DEFAULT_PROFILES_DIR,
    PackagedProfiles,
    _resolve_controls,
    build_config_from_profile,
    config_fingerprint,
    declared_profile,
    detect_profile,
    export_config_schema,
    init_config_file,
    list_profiles,
    load_config,
    load_profile,
    project_root_of,
    resolve_defences,
)
from lictor.adapters.config.models import (
    DEFAULT_MODEL,
    AcceptanceConfig,
    BackendConfig,
    CommandControlConfig,
    ControlChange,
    LictorConfig,
    LimitsConfig,
    PathsConfig,
    Profile,
    ProfileVerification,
    ProjectVerification,
    ReaderControlConfig,
    RetentionConfig,
    SurveyConfig,
    agent_run_problems,
    domain_controls,
)
from lictor.adapters.config.profiles.python_uv import (
    lictor_api,
    lictor_complexity,
    lictor_coverage,
    lictor_doubles,
    lictor_duplication,
    lictor_gherkin,
    lictor_impostor,
    lictor_metadata,
    lictor_outcome,
    lictor_project_path,
    lictor_survey,
    lictor_with_src,
)
from lictor.adapters.config.profiles.python_uv.lictor_project_path import (
    pytest_load_initial_conftests,
)
from lictor.domain.controls import AgentCommands, AgentFix, agent_commands
from lictor.domain.errors import ConfigError, ExitCode
from lictor.domain.interface_proof import InterfaceImpostor
from lictor.domain.rules import StubConstant, banned_matches

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_load_python_uv_profile() -> None:
    profile = load_profile("python-uv")
    assert profile.name == "python-uv"
    assert profile.version == "0.2"
    assert profile.status == "stable"
    assert profile.detect == ["pyproject.toml"]
    assert "tests/acceptance/" in profile.paths.driver
    assert "tests/acceptance/" in profile.paths.protected
    assert [c.id for c in profile.controls] == [
        "lint",
        "format",
        "types",
        "unit",
        "coverage",
        "complexity",
        "duplication",
        "api",
    ]


@pytest.mark.parametrize(
    "line",
    [
        'sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))',
        "sys.path.append('src')",
        "sys.path[:0] = ['src']",
        "sys.path = ['src', *sys.path]",
        "sys.path += ['src']",
        "sys.path.extend(['src'])",
    ],
)
def test_the_profile_refuses_reordering_the_import_path(line: str) -> None:
    """`src` goes behind the dependencies on purpose: in front, it can replace one (F12)."""
    assert banned_matches([line], load_profile("python-uv").banned_patterns)


@pytest.mark.parametrize(
    "line", ["import sys", "print(sys.path)", "assert 'src' in sys.path", "sys.path == []"]
)
def test_the_profile_lets_code_read_the_import_path(line: str) -> None:
    assert banned_matches([line], load_profile("python-uv").banned_patterns) == []


def test_the_driver_is_told_not_to_touch_the_import_path() -> None:
    hints = load_profile("python-uv").acceptance.driver_hints

    assert "sys.path" in hints
    assert "ya está en el path" in hints


def test_load_profile_with_version_suffix() -> None:
    profile = load_profile("python-uv@0.2")
    assert profile.name == "python-uv"
    assert profile.version == "0.2"


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


def test_each_profile_adds_its_section_to_the_skill(tmp_path: Path) -> None:
    packaged = PackagedProfiles()
    assert packaged.names() == ["generic", "python-uv"]
    assert "uv add --dev" in (packaged.skill_text("python-uv") or "")

    profile = (DEFAULT_PROFILES_DIR / "python-uv.json").read_text(encoding="utf-8")
    (tmp_path / "a.json").write_text(profile.replace('"python-uv"', '"a"', 1), encoding="utf-8")
    (tmp_path / "b.json").write_text(profile.replace('"python-uv"', '"b"', 1), encoding="utf-8")
    (tmp_path / "b.skill.md").write_text("Lo de b.\n", encoding="utf-8")
    profiles = PackagedProfiles(tmp_path)
    assert profiles.names() == ["a", "b"]
    # A profile without a section adds nothing.
    assert profiles.skill_text("a") is None
    assert profiles.skill_text("b") == "Lo de b.\n"


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("cobol", r"Perfil desconocido: 'cobol'\. Los de esta versión: generic, python-uv"),
        ("python-uv@0.1", "ya no existe: usa python-uv@0.2"),
        ("python-uv@9.9", "no coincide"),
    ],
)
def test_a_profile_asked_for_is_refused_as_init_would_refuse_it(name: str, why: str) -> None:
    with pytest.raises(ConfigError, match=why):
        PackagedProfiles().requested(name)


def test_a_profile_asked_for_with_its_version_gives_its_name() -> None:
    assert PackagedProfiles().requested("python-uv@0.2") == "python-uv"
    assert PackagedProfiles().requested("python-uv") == "python-uv"


def test_the_detected_profile_is_the_one_init_would_choose(tmp_path: Path) -> None:
    assert PackagedProfiles().detected(tmp_path) is None

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert PackagedProfiles().detected(tmp_path) == "python-uv"


def test_the_project_root_is_found_from_a_subdirectory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    deep = repo / "src" / "pkg"
    deep.mkdir(parents=True)
    # Outside a repository and without lictor.json, the directory itself.
    assert project_root_of(deep) == deep.resolve()

    (repo / ".git").mkdir()
    assert project_root_of(deep) == repo.resolve()

    (repo / "src" / "lictor.json").write_text("{}", encoding="utf-8")
    assert project_root_of(deep) == (repo / "src").resolve()


def test_the_project_root_stops_at_the_repository(tmp_path: Path) -> None:
    """A `lictor.json` above the repository belongs to another project."""
    (tmp_path / "lictor.json").write_text("{}", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    # A linked worktree has a `.git` file, not a directory.
    (repo / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")

    assert project_root_of(repo / "src") == repo.resolve()


def test_the_declared_profile_is_read_even_from_a_file_that_doesnt_load(tmp_path: Path) -> None:
    """The skill helps most when `lictor.json` is broken, so it reads only the profile."""
    assert declared_profile(tmp_path) is None

    (tmp_path / "lictor.json").write_text(
        '{"profile": "python-uv@0.1", "acceptance": {"report": "junit"}}', encoding="utf-8"
    )
    assert declared_profile(tmp_path) == "python-uv"


@pytest.mark.parametrize(
    ("content", "why"),
    [
        ("{roto", "`lictor.json` no se pudo leer"),
        ("[]", "no dice su perfil"),
        ('{"profile": 3}', "no dice su perfil"),
        ('{"profile": "@0.2"}', "no dice su perfil"),
    ],
)
def test_a_lictor_json_without_a_readable_profile_says_why(
    tmp_path: Path, content: str, why: str
) -> None:
    (tmp_path / "lictor.json").write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match=why):
        declared_profile(tmp_path)


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
    ctrl = CommandControlConfig.model_validate(
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
        CommandControlConfig.model_validate(
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
        CommandControlConfig.model_validate(
            {
                "id": "unit",
                "level": "L2",
                "run": "cmd test tests/unit",
                "report": "junit",
                "mode": "block",
            }
        )

    valid = CommandControlConfig.model_validate(
        {
            "id": "unit",
            "level": "L2",
            "run": "cmd test tests/unit --xml {out}",
            "report": "junit",
            "mode": "block",
        }
    )
    assert "{out}" in valid.run


def test_the_acceptance_needs_out_where_it_leaves_its_report() -> None:
    with pytest.raises(ValidationError, match=r"\{out\}"):
        AcceptanceConfig.model_validate({"run": "cmd acceptance"})

    assert AcceptanceConfig.model_validate({"run": "cmd {out}"}).run == "cmd {out}"


def test_a_control_whose_command_doesnt_split_is_refused_at_load_naming_it() -> None:
    """ADR-0008, point 1: an unclosed quote is a configuration error, not a crash in a run."""
    with pytest.raises(ValidationError, match=r"run del control 'unit'.*No closing quotation"):
        CommandControlConfig.model_validate(
            {"id": "unit", "level": "L2", "run": "cmd 'tests --xml {out}", "report": "junit"}
        )


def test_an_acceptance_whose_command_doesnt_split_is_refused_at_load() -> None:
    with pytest.raises(ValidationError, match=r"acceptance.*No closing quotation"):
        AcceptanceConfig.model_validate({"run": 'cmd "{out}'})


def test_a_project_control_that_doesnt_split_stops_the_load(tmp_path: Path) -> None:
    (tmp_path / "lictor.json").write_text(
        json.dumps(
            {
                "profile": "python-uv",
                "controls": [{"id": "lint", "run": "uv run ruff check 'src"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"run del control 'lint'"):
        load_config(tmp_path)


@pytest.mark.parametrize("report", ["junit", "exit-code"])
def test_the_acceptance_report_is_refused_and_says_why(report: str) -> None:
    """ADR-0009: the acceptance always reads JUnit, so the field decided nothing."""
    with pytest.raises(ValidationError, match=r"quita acceptance\.report"):
        AcceptanceConfig.model_validate({"run": "cmd {out}", "report": report})


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
        "claude-sonnet-5-5",
        "claude-opus-4-6",
        "claude-haiku-4-5-20251001",
    ],
)
def test_backend_model_accepts_full_ids(full_model_id: str) -> None:
    backend = BackendConfig(model=full_model_id)
    assert backend.model == full_model_id


def test_the_default_model_is_a_full_id_the_validator_accepts() -> None:
    # Pydantic doesn't validate defaults: this is what checks the one `lictor init` writes.
    assert BackendConfig(model=DEFAULT_MODEL).model == DEFAULT_MODEL


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
    assert cfg.profile_name == "python-uv@0.2"
    assert cfg.to_write == ()

    raw_disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert "detect" not in raw_disk
    assert "status" not in raw_disk
    assert raw_disk["profile"] == "python-uv@0.2"

    loaded = load_config(tmp_path)
    assert loaded.version == "0.1"
    assert loaded.profile == "python-uv@0.2"
    assert loaded == build_config_from_profile(load_profile("python-uv"), project_root=tmp_path)

    # No sobrescribe si force=False
    with pytest.raises(ConfigError) as exc_info:
        init_config_file(tmp_path, model="claude-opus-4-6", force=False)
    assert exc_info.value.exit_code == ExitCode.PRECONDITION
    assert load_config(tmp_path).backend.model == cfg.model

    # Con force=True sí sobrescribe
    updated = init_config_file(tmp_path, model="claude-opus-4-6", force=True)
    assert updated.model == "claude-opus-4-6"
    assert load_config(tmp_path).backend.model == "claude-opus-4-6"


def test_load_config_from_repo_root_lictor_json() -> None:
    cfg = load_config(REPO_ROOT / "lictor.json")
    assert cfg.profile == "python-uv@0.2"
    assert cfg.paths.writable == ["src/", "tests/unit/"]
    assert any(c.id == "architecture" for c in cfg.controls)


# What else a self-verification step may take on a runner 1.4 times slower, besides its slowest
# control, in seconds: if one control hangs and the rest take what they usually do, Lictor cuts it,
# and not the CI. L1, L2 and L4: the whole step, about 4.5 min. L5: only the mutation, plus building
# the environment and reading the report, under 1 min; 2 min is the least any step leaves.
CHECK_STEP_MARGINS_S = {frozenset({"L1", "L2", "L4"}): 270, frozenset({"L5"}): 120}


def _ci_check_steps() -> list[tuple[str, frozenset[str], int, int]]:
    """Each `lictor check` step of ci.yml: its job, its levels, and its and its job's cut in s."""
    ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    found = []
    for job_id, job in ci["jobs"].items():
        for step in job["steps"]:
            if "lictor check" not in step.get("run", ""):
                continue
            tokens = shlex.split(step["run"])
            levels = frozenset(tokens[tokens.index("--level") + 1].split(","))
            assert "--keep-going" in tokens, job_id
            cuts = (int(step["timeout-minutes"]) * 60, int(job["timeout-minutes"]) * 60)
            found.append((job_id, levels, *cuts))
    return found


def test_the_ci_self_verification_runs_every_control_of_lictor_json_once() -> None:
    """The CI filters by level to skip the acceptance; a control at another level would be lost."""
    steps = _ci_check_steps()
    run_by = [levels for _, levels, _, _ in steps]
    controls = load_config(REPO_ROOT / "lictor.json").controls

    assert all("L3" not in levels for levels in run_by), steps
    assert {c.id: c.level for c in controls if not any(c.level in s for s in run_by)} == {}
    assert {c.id for c in controls if sum(c.level in s for s in run_by) > 1} == set()


def test_lictor_cuts_a_hung_control_before_the_ci_cuts_its_step() -> None:
    """A cut of the CI leaves no log or verdict of the control, and stops `--keep-going`."""
    controls = load_config(REPO_ROOT / "lictor.json").controls
    steps = _ci_check_steps()

    assert {levels for _, levels, _, _ in steps} == set(CHECK_STEP_MARGINS_S)
    for job_id, levels, cut_s, job_cut_s in steps:
        # Below the job's, so a hung step still leaves the next one to upload `.lictor/check/`.
        assert cut_s < job_cut_s, (job_id, cut_s, job_cut_s)
        slowest = max(
            c.timeout_s
            for c in controls
            if c.level in levels and isinstance(c, CommandControlConfig)
        )
        assert slowest + CHECK_STEP_MARGINS_S[levels] <= cut_s, (job_id, slowest, cut_s)


def test_load_config_errors_on_missing_or_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.json")

    bad_json = tmp_path / "lictor.json"
    bad_json.write_text("{bad json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad_json)

    invalid_cfg = tmp_path / "invalid.json"
    invalid_cfg.write_text(json.dumps({"profile": "python-uv@0.2", "backend": {"model": "opus"}}))
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
                "profile": "python-uv@0.2",
                "acceptance": {"run": "otra {out}"},
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

    assert [path.name for path in spec.files] == [
        "lictor_project_path.py",
        "lictor_metadata.py",
        "lictor_outcome.py",
        "lictor_impostor.py",
        "lictor_gherkin.py",
        "lictor_doubles.py",
        "lictor_complexity.py",
        "lictor_duplication.py",
        "lictor_coverage.py",
        "lictor_survey.py",
        "lictor_api.py",
        "lictor_with_src.py",
    ]
    assert all(path.is_file() for path in spec.files)
    assert spec.files_dir_command.startswith("{env_dir}/bin/python -I -B -c")
    assert "get_path" in spec.files_dir_command


def test_the_path_plugin_appends_the_sources_of_the_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", ["primero"])
    config = SimpleNamespace(invocation_params=SimpleNamespace(dir=tmp_path), inipath=None)

    pytest_load_initial_conftests(cast(pytest.Config, config))

    assert sys.path == ["primero", str(tmp_path / "src")]


def _imported(nodes: list[ast.AST]) -> set[str]:
    names = {
        alias.name.split(".")[0]
        for node in nodes
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    return names | {
        node.module.split(".")[0]
        for node in nodes
        if isinstance(node, ast.ImportFrom) and node.module
    }


def _runtime_nodes(tree: ast.Module) -> list[ast.AST]:
    """Every node but those under `if TYPE_CHECKING:`, which only the type checker reads."""
    skipped = {
        id(child)
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
        for branch in node.body
        for child in ast.walk(branch)
    }
    return [node for node in ast.walk(tree) if id(node) not in skipped]


@pytest.mark.parametrize(
    ("plugin", "expected"),
    [
        (
            lictor_project_path.__file__,
            {"configparser", "importlib", "os", "pathlib", "pytest", "sys", "typing"},
        ),
        (lictor_metadata.__file__, {"ast", "importlib", "pathlib", "re", "typing"}),
        (
            lictor_outcome.__file__,
            {
                "ast",
                "collections",
                "contextlib",
                "inspect",
                "pathlib",
                "pytest",
                "sys",
                "tokenize",
                "types",
                "typing",
            },
        ),
        (
            lictor_impostor.__file__,
            {
                "asyncio",
                "collections",
                "copy",
                "dataclasses",
                "datetime",
                "decimal",
                "dis",
                "enum",
                "fractions",
                "functools",
                "hashlib",
                "importlib",
                "inspect",
                "json",
                "math",
                "os",
                "pytest",
                "sys",
                "sysconfig",
                "types",
                "typing",
            },
        ),
        (
            lictor_gherkin.__file__,
            {
                "collections",
                "datetime",
                "decimal",
                "dis",
                "importlib",
                "inspect",
                "json",
                "os",
                "pytest",
                "re",
                "typing",
            },
        ),
        (lictor_doubles.__file__, {"collections", "math", "pytest", "sys"}),
        (lictor_complexity.__file__, {"ast", "collections", "json", "pathlib", "sys"}),
        (
            lictor_api.__file__,
            {"argparse", "ast", "collections", "dataclasses", "json", "pathlib", "sys"},
        ),
        (
            lictor_duplication.__file__,
            {
                "argparse",
                "ast",
                "bisect",
                "collections",
                "dataclasses",
                "io",
                "json",
                "keyword",
                "pathlib",
                "sys",
                "tokenize",
                "typing",
            },
        ),
    ],
)
def test_the_profile_plugins_stand_alone(plugin: str, expected: set[str]) -> None:
    """They are installed on their own in an environment where Lictor doesn't exist: what they
    import of it is only for the type checker, and their sibling they import by its own name."""
    tree = ast.parse(Path(plugin).read_text(encoding="utf-8"))

    assert "lictor" not in _imported(_runtime_nodes(tree))
    # `__future__` is a directive of the compiler, no module.
    assert _imported(list(ast.walk(tree))) - {"__future__", "lictor"} == expected


def test_a_profile_file_that_is_missing_is_a_configuration_error(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    data = load_profile("python-uv").model_dump(mode="json")
    (profiles / "python-uv.json").write_text(json.dumps(data), encoding="utf-8")
    config = load_config(_project(tmp_path), profiles_dir=profiles)

    with pytest.raises(ConfigError, match=r"lictor_project_path\.py"):
        resolve_defences(config, profiles)


def test_a_profile_control_can_only_need_a_plugin_if_the_profile_lists_them() -> None:
    """ADR-0008, point 3: without a list, "skip without the plugin" can't be decided."""
    data = load_profile("python-uv").model_dump(mode="json")
    data["verification"]["plugins_command"] = ""

    with pytest.raises(ValidationError, match=r"'coverage'.*plugins_command"):
        Profile.model_validate(data)


def test_a_project_control_can_only_need_a_plugin_if_its_profile_lists_them(
    tmp_path: Path,
) -> None:
    profiles = _profiles_copy(tmp_path / "profiles")
    profile_file = profiles / "python-uv.json"
    data = json.loads(profile_file.read_text(encoding="utf-8"))
    data["verification"]["plugins_command"] = ""
    data["controls"] = [c for c in data["controls"] if "when_plugin" not in c]
    data["acceptance"]["needs_plugins"] = {}
    profile_file.write_text(json.dumps(data), encoding="utf-8")
    extra = {"id": "extra", "level": "L2", "run": "x", "report": "exit-code", "when_plugin": "p"}
    config = load_config(_project(tmp_path, controls=[extra]), profiles_dir=profiles)

    with pytest.raises(ConfigError, match=r"'extra'.*plugins_command"):
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
    path.write_text(json.dumps({"profile": "python-uv@0.2", **fields}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "fields",
    [
        {"verification": {"files": {}}},
        {"verification": {"reserved": []}},
        {"verification": {"plugin_variable": "OTRA"}},
        {"oracle": {"interface_impostor": None}},
    ],
    ids=lambda fields: next(iter(fields.get("verification", fields))),
)
def test_a_project_cannot_declare_a_defence_of_the_profile(
    tmp_path: Path, fields: dict[str, object]
) -> None:
    with pytest.raises(ConfigError, match="solo lo declara el perfil") as refused:
        load_config(_project(tmp_path, **fields))
    assert "Extra inputs" not in str(refused.value)


def test_a_field_nobody_declares_is_pydantics_to_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Extra inputs"):
        load_config(_project(tmp_path, verification={"unset": []}))


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
        "LICTOR_IMPOSTOR",
        "LICTOR_OTRA",
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
                "prepare_inherit": ["MI_ESPEJO"],
            },
        )
    )
    profile = load_profile("python-uv").verification

    spec = resolve_defences(config).verification

    assert spec.env == profile.env | {"MI_BASE": "sqlite://"}
    assert list(spec.inherit) == [*profile.inherit, "MI_TOKEN"]
    assert list(spec.prepare_inherit) == [*profile.prepare_inherit, "MI_ESPEJO"]
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
    (profiles / "python_uv").mkdir()
    for plugin in (
        lictor_project_path.__file__,
        lictor_metadata.__file__,
        lictor_outcome.__file__,
        lictor_impostor.__file__,
        lictor_gherkin.__file__,
        lictor_doubles.__file__,
        lictor_complexity.__file__,
        lictor_duplication.__file__,
        lictor_coverage.__file__,
        lictor_survey.__file__,
        lictor_api.__file__,
        lictor_with_src.__file__,
    ):
        (profiles / "python_uv" / Path(plugin).name).write_text("", encoding="utf-8")

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
    assert "verification" not in written
    defences = {"suppressions", "banned_patterns", "forbidden_paths", "driver_exempt_paths"}
    assert not defences & set(written)


def test_demo_declares_nothing_of_its_own() -> None:
    assert load_config(REPO_ROOT / "examples" / "demo-iva" / "lictor.json").verification == (
        ProjectVerification()
    )


def test_profile_no_longer_assumes_a_project_dependency() -> None:
    verification = load_profile("python-uv").verification

    assert verification.plugins == [
        "lictor_project_path",
        "lictor_outcome",
        "lictor_impostor",
        "lictor_gherkin",
        "lictor_doubles",
    ]
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


def test_fingerprint_ignores_the_retention_which_only_a_new_run_reads(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = _fingerprint(root)

    assert _fingerprint(_project(tmp_path, retention={"keep_runs": 3})) == before


@pytest.mark.parametrize("keep", [0, -1])
def test_at_least_one_run_is_kept(keep: int) -> None:
    with pytest.raises(ValidationError):
        RetentionConfig(keep_runs=keep)


def test_ten_runs_are_kept_by_default() -> None:
    assert RetentionConfig().keep_runs == 10


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


@pytest.mark.parametrize(
    "field", ["suppressions", "banned_patterns", "forbidden_paths", "driver_exempt_paths"]
)
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


def _derived(config: LictorConfig) -> AgentCommands:
    return agent_commands(
        domain_controls(config),
        config.acceptance.run,
        config.agent_tools,
        ("pytest_cov.plugin", "pytest_bdd.plugin"),
    )


def test_the_profiles_agent_commands_come_from_its_controls(tmp_path: Path) -> None:
    """F83: the list `agent_commands` had, in its order, plus the check of what the fix fixes."""
    derived = _derived(load_config(_project(tmp_path)))

    assert derived.fixes == (AgentFix("format", "uv run ruff format src"),)
    assert derived.checks == (
        "uv run ruff check src",
        "uv run ruff format --check src",
        "uv run mypy src",
        "uv run pytest tests/unit -q",
        "uv run pytest tests/acceptance -q",
    )


# What pyverifactu wrote by hand in `agent_commands` (commit 1cff89a of the 4a copy), and the
# controls it had changed: the derivation gives the same checks, token by token.
_PYVERIFACTU_PATHS = "src tests/unit tests/integration tests/fixtures tests/conftest.py"
_PYVERIFACTU_CONTROLS = [
    {"id": "lint", "run": f"uv run ruff check {_PYVERIFACTU_PATHS}"},
    {
        "id": "format",
        "run": f"uv run ruff format --check {_PYVERIFACTU_PATHS}",
        "agent_fix": f"uv run ruff format {_PYVERIFACTU_PATHS}",
    },
    {
        "id": "unit",
        "run": 'uv run pytest tests -m "not integration" --ignore=tests/acceptance -q '
        "--junitxml {out}",
        "when_exists": "tests",
    },
    {
        "id": "coverage",
        "agent_run": "uv run python -I -B {lictor_files}/lictor_coverage.py tests -m "
        '"not integration" --ignore=tests/acceptance -q',
    },
    {"id": "api", "mode": "block"},
]
_PYVERIFACTU_BY_HAND = [
    f"uv run ruff format {_PYVERIFACTU_PATHS}",
    f"uv run ruff check {_PYVERIFACTU_PATHS}",
    "uv run mypy src",
    'uv run pytest tests -m "not integration" --ignore=tests/acceptance -q',
    "uv run pytest tests/acceptance -q",
]


def test_pyverifactus_hand_written_commands_come_from_its_controls(tmp_path: Path) -> None:
    derived = _derived(load_config(_project(tmp_path, controls=_PYVERIFACTU_CONTROLS)))

    split = [shlex.split(command) for command in derived.all]
    assert [shlex.split(command) for command in _PYVERIFACTU_BY_HAND] == [
        s for s in split if s[:4] != ["uv", "run", "ruff", "format"] or "--check" not in s
    ]
    assert "uv run python -I -B -m lictor_api src" not in derived.checks, "agent_tools no lo deja"


def test_a_lictor_json_with_agent_commands_is_refused_naming_where_they_go(tmp_path: Path) -> None:
    path = _project(tmp_path, agent_commands=["uv run pytest"])

    with pytest.raises(ConfigError) as exc_info:
        load_config(path)

    assert exc_info.value.exit_code == ExitCode.PRECONDITION
    assert "agent_commands" in str(exc_info.value)
    assert "agent_run o el agent_fix" in str(exc_info.value)


@pytest.mark.parametrize(
    ("controls", "named"),
    [
        ([{"id": "format", "run": "uv run ruff format --check ."}], "'format'"),
        ([{"id": "unit", "run": "uv run pytest tests -q --junitxml {out}"}], "'coverage'"),
        ([{"id": "coverage", "adds": "--cov=pkg --cov-report=json:{out}"}], "'coverage'"),
    ],
)
def test_a_changed_run_that_inherits_the_agents_command_is_refused(
    tmp_path: Path, controls: list[dict[str, object]], named: str
) -> None:
    """F83: under another run the profile's agent_run or agent_fix may check something else."""
    with pytest.raises(ConfigError) as exc_info:
        load_config(_project(tmp_path, controls=controls))

    message = str(exc_info.value)
    assert exc_info.value.exit_code == ExitCode.PRECONDITION
    assert named in message
    assert "o null si el agente no debe usarlo" in message


def test_writing_the_agents_command_or_null_accepts_a_changed_run(tmp_path: Path) -> None:
    controls = [
        {"id": "format", "run": "uv run ruff format --check .", "agent_fix": None},
        {"id": "unit", "run": "uv run pytest tests -q --junitxml {out}"},
        {"id": "coverage", "agent_run": "uv run python -I -B {lictor_files}/lictor_coverage.py t"},
    ]

    config = load_config(_project(tmp_path, controls=controls))

    assert _derived(config).fixes == ()


def test_a_reader_has_no_fix_of_its_own(tmp_path: Path) -> None:
    fixing = {"id": "f", "level": "L1", "run": "f --check", "report": "exit-code", "agent_fix": "f"}
    turned = ControlChange(id="f", reads="unit", adds="--x {out}", report="coverage")
    [resolved] = _resolve_controls([fixing], [turned])
    assert "agent_fix" not in resolved

    written = {"id": "coverage", "agent_fix": "uv run ruff format src"}
    with pytest.raises(ConfigError, match="agent_fix es de una ejecución"):
        load_config(_project(tmp_path, controls=[written]))


def test_an_agent_fix_the_tools_dont_allow_is_refused_if_written(tmp_path: Path) -> None:
    written = {"id": "format", "agent_fix": "make format"}

    with pytest.raises(ConfigError, match="agent_fix del control 'format'"):
        load_config(_project(tmp_path, controls=[written]))


def test_python_uv_unit_control_runs_only_where_its_tests_exist() -> None:
    unit = next(c for c in load_profile("python-uv").controls if c.id == "unit")
    assert unit.when_exists == "tests/unit"


@pytest.mark.parametrize("path", ["", "/abs/tests", "../fuera", "tests/../../fuera"])
def test_control_when_exists_must_stay_inside_the_project(path: str) -> None:
    with pytest.raises(ValidationError, match="when_exists"):
        CommandControlConfig.model_validate(
            {"id": "u", "level": "L2", "run": "cmd", "report": "exit-code", "when_exists": path}
        )


def test_the_oracle_comes_from_the_profile_only(tmp_path: Path) -> None:
    defences = resolve_defences(load_config(REPO_ROOT))

    assert defences.map_link == "scenario"
    assert defences.red_kinds == ("assertion", "not_implemented")
    assert defences.impostors is not None
    assert defences.impostors.replacements == (
        StubConstant(r"\1return None", "None"),
        StubConstant(r'\1return __import__("lictor_doubles").stub("True")', "True"),
        StubConstant(
            r'\1return __import__("lictor_doubles").stub("ONE")', "un doble de Lictor", True
        ),
        StubConstant(
            r'\1return __import__("lictor_doubles").stub("EMPTY")', "un doble vacío de Lictor", True
        ),
    )
    config = json.loads((REPO_ROOT / "lictor.json").read_text(encoding="utf-8"))
    config["oracle"] = {"red_kinds": []}
    (tmp_path / "lictor.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ConfigError, match="oracle"):
        load_config(tmp_path)


def test_a_profile_without_oracle_keeps_the_rules_of_before() -> None:
    raw = load_profile("python-uv").model_dump(mode="json")
    del raw["oracle"]
    oracle = Profile.model_validate(raw).oracle
    assert (oracle.link, oracle.red_kinds, oracle.impostors) == ("name", [], None)


@pytest.mark.parametrize(
    "oracle",
    [
        {"red_kinds": ["cualquiera"]},
        {"link": "otro"},
        {"impostors": {"pattern": "(", "replacements": ["x"]}},
        {"impostors": {"pattern": "x", "replacements": []}},
        {"desconocido": 1},
    ],
)
def test_an_invalid_oracle_is_refused(oracle: dict[str, object]) -> None:
    raw = load_profile("python-uv").model_dump(mode="json")
    raw["oracle"] = oracle
    with pytest.raises(ValidationError):
        Profile.model_validate(raw)


def test_python_uv_unit_control_declares_the_empty_run_code() -> None:
    unit = next(c for c in load_profile("python-uv").controls if c.id == "unit")
    assert unit.empty_exit_codes == [5]


def test_control_without_empty_exit_codes_declares_none() -> None:
    control = CommandControlConfig.model_validate(
        {"id": "u", "level": "L2", "run": "cmd", "report": "exit-code"}
    )
    assert control.empty_exit_codes == []


def test_a_lictor_json_with_allow_empty_says_what_to_write_instead(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    init_config_file(tmp_path)
    config_path = tmp_path / "lictor.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw["controls"] = [{"id": "unit", "allow_empty": True}]
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)

    assert exc_info.value.exit_code == ExitCode.PRECONDITION
    message = str(exc_info.value)
    assert "'unit' usa 'allow_empty', que ya no existe" in message
    assert "'empty_exit_codes'; el control 'unit' del perfil declara [5]" in message
    assert "no se podrá reanudar" in message


def test_a_control_with_allow_empty_is_refused_even_without_a_profile() -> None:
    with pytest.raises(ValidationError, match="'u' usa 'allow_empty', que ya no existe"):
        CommandControlConfig.model_validate(
            {"id": "u", "level": "L2", "run": "c", "report": "exit-code", "allow_empty": True}
        )


@pytest.mark.parametrize("report", ["junit", "mutation"])
def test_a_control_that_reads_a_report_needs_its_path(report: str) -> None:
    with pytest.raises(ValidationError, match=f"report='{report}' debe incluir"):
        CommandControlConfig.model_validate(
            {"id": "m", "level": "L5", "run": "cmd", "report": report}
        )


@pytest.mark.parametrize(("chars", "valid"), [(499, False), (500, True), (4000, True)])
def test_error_max_chars_keeps_room_for_the_reason(chars: int, valid: bool) -> None:
    if valid:
        assert LimitsConfig(error_max_chars=chars).error_max_chars == chars
    else:
        with pytest.raises(ValidationError, match="greater than or equal to 500"):
            LimitsConfig(error_max_chars=chars)


def test_min_score_belongs_to_a_control_with_a_score() -> None:
    mutation = {"id": "m", "level": "L5", "run": "x {out}", "report": "mutation"}
    coverage = {"id": "c", "level": "L2", "run": "x {out}", "report": "coverage"}
    assert CommandControlConfig(**mutation, min_score=95).min_score == 95
    assert CommandControlConfig(**coverage, min_score=80).min_score == 80
    with pytest.raises(ValidationError, match="solo vale con report='mutation' o 'coverage'"):
        CommandControlConfig(id="l", level="L1", run="lint", report="exit-code", min_score=95)


@pytest.mark.parametrize("minimum", [-1, 100.5])
def test_min_score_is_a_percentage(minimum: float) -> None:
    with pytest.raises(ValidationError):
        CommandControlConfig(
            id="m", level="L5", run="x {out}", report="mutation", min_score=minimum
        )


def test_init_does_not_copy_the_driver_hints(tmp_path: Path) -> None:
    """They are the profile's: a copy kept every project away from the profile's later fixes."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    init_config_file(tmp_path)

    written = json.loads((tmp_path / "lictor.json").read_text(encoding="utf-8"))
    assert "acceptance" not in written


def test_the_driver_hints_come_from_the_profile_on_every_load(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    init_config_file(tmp_path)
    profiles = _profiles_copy(tmp_path / "perfiles")
    path = profiles / "python-uv.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["acceptance"]["driver_hints"] = "Una instrucción nueva del perfil."
    path.write_text(json.dumps(data), encoding="utf-8")

    defences = resolve_defences(load_config(tmp_path, profiles), profiles)

    assert defences.driver_hints == "Una instrucción nueva del perfil."


def test_the_fingerprint_covers_the_driver_hints(tmp_path: Path) -> None:
    config = load_config(_project(tmp_path))
    defences = resolve_defences(config)

    other = replace(defences, driver_hints=f"{defences.driver_hints} Más.")

    assert config_fingerprint(config, other) != config_fingerprint(config, defences)


def test_a_lictor_json_with_driver_hints_is_refused_naming_hints(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    init_config_file(tmp_path)
    path = tmp_path / "lictor.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    profile_acceptance = load_profile("python-uv").acceptance
    assert profile_acceptance is not None
    acceptance = profile_acceptance.model_dump(exclude={"driver_hints", "needs_plugins"})
    data["acceptance"] = {**acceptance, "driver_hints": "Usa pytest-bdd."}
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_config(tmp_path)

    message = str(exc_info.value)
    assert exc_info.value.exit_code == ExitCode.PRECONDITION
    assert "acceptance.driver_hints" in message
    assert "perfil" in message
    assert "hints" in message.replace("driver_hints", "")


def test_init_takes_the_python_version_of_hints_from_requires_python(tmp_path: Path) -> None:
    """`uv init` writes the user's Python; the profile's own version misled the agent (F05)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.14"\n', encoding="utf-8"
    )

    init_config_file(tmp_path)

    hints = json.loads((tmp_path / "lictor.json").read_text(encoding="utf-8"))["hints"]
    assert "Python >=3.14" in hints
    assert "3.12" not in hints
    assert "{" not in hints


@pytest.mark.parametrize(
    "pyproject",
    ['[project]\nname = "x"\n', "esto no es TOML ][", "[project]\nrequires-python = 3\n"],
)
def test_without_a_readable_requires_python_hints_keep_the_profile_default(
    tmp_path: Path, pyproject: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    init_config_file(tmp_path)

    hints = json.loads((tmp_path / "lictor.json").read_text(encoding="utf-8"))["hints"]
    assert hints.startswith("Python >=3.12,")


def test_hints_left_to_the_profile_also_name_the_project_s_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.13"\n', encoding="utf-8"
    )
    (tmp_path / "lictor.json").write_text('{"profile": "python-uv@0.2"}', encoding="utf-8")

    assert load_config(tmp_path).hints.startswith("Python >=3.13,")


def test_the_profile_measures_coverage_only_where_the_plugin_is_installed() -> None:
    """It needs a dependency the agent can't add: without it, the control is skipped (F18)."""
    [coverage] = [c for c in load_profile("python-uv").controls if c.id == "coverage"]

    assert isinstance(coverage, ReaderControlConfig)
    assert coverage.mode == "measure"
    assert coverage.when_plugin == "pytest_cov.plugin"


def test_the_profile_keeps_the_coverage_as_a_score_of_the_run_of_unit() -> None:
    """F29 made the percentage a score; ADR-0008 reads it from the one run of unit."""
    [coverage] = [c for c in load_profile("python-uv").controls if c.id == "coverage"]

    assert isinstance(coverage, ReaderControlConfig)
    assert (coverage.report, coverage.reads) == ("coverage", "unit")
    assert "--cov-report=json:{out}" in coverage.adds
    assert "--cov-branch" in coverage.adds
    # The project's own threshold would fail unit with no test in red: a minimum is min_score.
    assert "--cov-fail-under=0" in coverage.adds
    # A table in unit's log would reach the prompt just when the suite doesn't start.
    assert "--cov-report=term" not in coverage.adds
    assert coverage.min_score is None


def test_a_lictor_json_that_asks_for_the_retired_profile_is_told_what_changed(
    tmp_path: Path,
) -> None:
    (tmp_path / "lictor.json").write_text('{"profile": "python-uv@0.1"}', encoding="utf-8")

    with pytest.raises(ConfigError) as refused:
        load_config(tmp_path)

    assert refused.value.exit_code == ExitCode.PRECONDITION
    assert str(refused.value) == (
        "El perfil python-uv@0.1 ya no existe: usa python-uv@0.2, que mide la cobertura en la "
        "ejecución de unit (ver el CHANGELOG)"
    )


def test_the_profile_tells_the_agent_how_to_measure_its_coverage() -> None:
    """F55: the agent couldn't measure the coverage its attempt was failed for."""
    profile = load_profile("python-uv")
    [coverage] = [c for c in profile.controls if c.id == "coverage"]

    assert coverage.agent_run is not None
    # The profile's module, which measures `src` with branches as verification counts (F71).
    assert coverage.agent_run == (
        "uv run python -I -B {lictor_files}/lictor_coverage.py tests/unit -q"
    )
    assert "python_uv/lictor_coverage.py" in [str(f) for f in profile.verification.files]
    assert agent_run_problems(profile.controls, profile.agent_tools) == []


def test_an_agent_run_the_agent_tools_dont_allow_is_refused(tmp_path: Path) -> None:
    path = _project(tmp_path, controls=[{"id": "coverage", "agent_run": "make cobertura"}])

    with pytest.raises(ConfigError) as refused:
        load_config(path)

    assert "El agent_run del control 'coverage' (make cobertura) no lo permite agent_tools" in str(
        refused.value
    )


def test_an_inherited_agent_run_the_project_tools_dont_allow_is_only_left_untold(
    tmp_path: Path,
) -> None:
    path = _project(tmp_path, agent_tools=[])

    config = load_config(path)

    [coverage] = [c for c in config.controls if c.id == "coverage"]
    assert coverage.agent_run is not None
    [told] = [c for c in domain_controls(config) if c.id == "coverage"]
    assert told.agent_run is None


def test_agent_run_problems_allow_a_prefix_rule_or_an_exact_one() -> None:
    tools = ["Bash(uv run pytest:*)", "Bash(make cov)", "Read"]

    def control(agent_run: str) -> CommandControlConfig:
        return CommandControlConfig(
            id="c", level="L2", run="x {out}", report="coverage", agent_run=agent_run
        )

    assert agent_run_problems([control("uv run pytest -q --cov")], tools) == []
    assert agent_run_problems([control("make cov")], tools) == []
    assert len(agent_run_problems([control("make cov extra")], tools)) == 1
    assert len(agent_run_problems([control("Read")], tools)) == 1


def test_a_project_changes_or_clears_the_agent_run_by_id(tmp_path: Path) -> None:
    command = "uv run pytest tests/unit --cov=src"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    changed = _project(tmp_path / "a", controls=[{"id": "coverage", "agent_run": command}])
    cleared = _project(tmp_path / "b", controls=[{"id": "coverage", "agent_run": None}])

    [coverage] = [c for c in load_config(changed).controls if c.id == "coverage"]
    [none] = [c for c in load_config(cleared).controls if c.id == "coverage"]

    assert coverage.agent_run == command
    assert none.agent_run is None


def test_a_project_asks_for_a_minimum_coverage_by_id(tmp_path: Path) -> None:
    path = _project(tmp_path, controls=[{"id": "coverage", "min_score": 80}])

    [coverage] = [c for c in load_config(path).controls if c.id == "coverage"]

    assert (coverage.min_score, coverage.mode) == (80, "measure")


# The refactor after each implemented green (ADR-0010).


def test_the_refactor_is_on_unless_the_project_turns_it_off(tmp_path: Path) -> None:
    assert load_config(_project(tmp_path)).refactor.enabled is True
    off = _project(tmp_path, refactor={"enabled": False})
    assert load_config(off).refactor.enabled is False


def test_init_does_not_write_the_refactor(tmp_path: Path) -> None:
    """What `init` doesn't write comes from the model: on, as the ADR decides."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    init_config_file(tmp_path)

    assert "refactor" not in json.loads((tmp_path / "lictor.json").read_text(encoding="utf-8"))


def test_turning_the_refactor_off_changes_the_fingerprint(tmp_path: Path) -> None:
    before = _fingerprint(_project(tmp_path))

    assert _fingerprint(_project(tmp_path, refactor={"enabled": False})) != before


def test_the_refactor_hints_are_the_profiles_and_in_the_fingerprint(tmp_path: Path) -> None:
    config = load_config(_project(tmp_path))
    defences = resolve_defences(config)

    assert "Complejidad ciclomática" in defences.refactor_hints
    other = replace(defences, refactor_hints=f"{defences.refactor_hints} Más.")
    assert config_fingerprint(config, other) != config_fingerprint(config, defences)


def test_a_lictor_json_with_refactor_hints_is_refused_naming_hints(tmp_path: Path) -> None:
    path = _project(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "refactor_hints": "Más corto."}), encoding="utf-8")

    with pytest.raises(ConfigError) as exc_info:
        load_config(path)

    message = str(exc_info.value)
    assert exc_info.value.exit_code == ExitCode.PRECONDITION
    assert "refactor_hints" in message
    assert "hints" in message.replace("refactor_hints", "")


def test_where_the_code_is_is_the_profiles_and_in_the_fingerprint(tmp_path: Path) -> None:
    """F84: the refactor stays in the code its scenario changed, and the profile says where."""
    config = load_config(_project(tmp_path))
    defences = resolve_defences(config)

    assert defences.code_paths == ("src/",)
    other = replace(defences, code_paths=("lib/",))
    assert config_fingerprint(config, other) != config_fingerprint(config, defences)


def test_code_paths_of_lictor_json_join_the_profiles(tmp_path: Path) -> None:
    """ADR-0010, amendment of 0.23.0: adding code only narrows what `L0-alcance` lets through."""
    paths = {"writable": ["src/", "lib/"], "driver": ["tests/acceptance/"], "protected": ["x"]}
    config = load_config(_project(tmp_path, code_paths=["lib/", "src/"], paths=paths))

    assert resolve_defences(config).code_paths == ("src/", "lib/")


def test_writable_paths_without_the_code_leave_the_refactor_unscoped_and_are_refused(
    tmp_path: Path,
) -> None:
    config = load_config(
        _project(
            tmp_path,
            paths={
                "writable": ["lib/", "tests/unit/"],
                "driver": ["tests/acceptance/"],
                "protected": ["features/", "tests/acceptance/", "lictor.json"],
            },
        )
    )

    with pytest.raises(ConfigError) as exc_info:
        resolve_defences(config)

    assert exc_info.value.exit_code == ExitCode.PRECONDITION
    assert "L0-alcance" in str(exc_info.value)


def test_the_profile_declares_the_impostor_of_the_interface() -> None:
    defences = resolve_defences(load_config(REPO_ROOT))

    assert defences.interface_impostor == InterfaceImpostor(
        "LICTOR_IMPOSTOR", "control", ("mas", "menos"), gherkin="LICTOR_GHERKIN"
    )


def _profile_with(**changes: Any) -> dict[str, Any]:
    data = load_profile("python-uv").model_dump(mode="json")
    for dotted, value in changes.items():
        *path, key = dotted.split("__")
        target = data
        for part in path:
            target = target[part]
        target[key] = value
    return data


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"verification__reserved": ["PYTHON*"]}, "no está en reserved"),
        ({"verification__inherit": ["PATH", "LICTOR_IMPOSTOR"]}, "que fija verification"),
        ({"verification__env": {"LICTOR_IMPOSTOR": "1"}}, "que fija verification"),
        (
            {"oracle__interface_impostor": {"variable": "X", "control": "a", "modes": ["a"]}},
            "repite un modo",
        ),
        (
            {"oracle__interface_impostor": {"variable": "X", "control": "a", "modes": ["../b"]}},
            "pattern",
        ),
        (
            {"oracle__interface_impostor": {"variable": "x-y", "control": "a", "modes": ["b"]}},
            "pattern",
        ),
        ({"oracle__interface_impostor": {"variable": "X", "control": "a", "modes": []}}, "modes"),
    ],
)
def test_the_impostor_s_variable_and_modes_are_checked_with_the_profile(
    changes: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Profile.model_validate(_profile_with(**changes))


def test_fingerprint_ignores_the_blind_spots_a_person_accepts(tmp_path: Path) -> None:
    before = _fingerprint(_project(tmp_path))

    accepted = {"accepted": ["tests/integration/", "examples/"]}
    root = _project(tmp_path, blind_spots=accepted)

    assert _fingerprint(root) == before
    assert load_config(tmp_path).blind_spots.accepted == ["tests/integration/", "examples/"]


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        ({"survey": {"run": "x {in} {out}"}}, "survey"),
        (
            {
                "acceptance": {
                    "run": "uv run pytest tests/acceptance --junitxml {out}",
                    "needs_plugins": {"x": "y"},
                }
            },
            "acceptance.needs_plugins",
        ),
    ],
)
def test_a_lictor_json_cant_declare_the_start_contract_of_its_profile(
    tmp_path: Path, fields: dict[str, object], named: str
) -> None:
    _project(tmp_path, **fields)

    with pytest.raises(ConfigError, match=rf"no puede declarar {named}"):
        load_config(tmp_path)


def test_the_profile_declares_what_its_acceptance_needs_and_how_to_survey() -> None:
    defences = resolve_defences(load_config(REPO_ROOT))

    assert defences.needs_plugins == {"pytest_bdd.plugin": "uv add --dev pytest-bdd"}
    assert defences.survey_run == "uv run python -I -B -m lictor_survey src --in {in} --out {out}"
    assert defences.detect == ("pyproject.toml",)


@pytest.mark.parametrize("run", ["x --out {out}", "x --in {in}"])
def test_a_survey_must_say_where_its_input_and_report_go(run: str) -> None:
    with pytest.raises(ValidationError, match=r"survey\.run debe incluir"):
        SurveyConfig(run=run)


def test_needing_a_plugin_needs_a_way_to_list_them() -> None:
    data = load_profile("python-uv").model_dump(mode="json")
    data["verification"]["plugins_command"] = ""
    data["controls"] = [c for c in data["controls"] if c.get("when_plugin") is None]

    with pytest.raises(ValidationError, match="needs_plugins necesita"):
        Profile.model_validate(data)
