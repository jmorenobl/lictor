import json
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from typer.testing import CliRunner

import lictor
from lictor.adapters.agents.fake import FakeExecutor
from lictor.application.run_files import exclusive_run
from lictor.application.skill import SKILL_FILE, skill_stub
from lictor.cli import app, version_line
from lictor.domain.model import Health

runner = CliRunner()


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "master"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_cli_init_existing_without_force_fails(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "lictor.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["init", "--cwd", str(tmp_path)])
    assert result.exit_code == 10


def test_cli_doctor_on_dirty_tree_fails_with_10(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: initial"], cwd=tmp_path, check=True, capture_output=True
    )
    runner.invoke(app, ["init", "--cwd", str(tmp_path)])
    # Tree is dirty because lictor.json and .lictor/ are uncommitted
    result = runner.invoke(app, ["doctor", "--cwd", str(tmp_path)])
    assert result.exit_code == 10


def test_cli_status_and_report(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    lictor_dir = tmp_path / ".lictor"
    lictor_dir.mkdir(parents=True)
    (lictor_dir / "current_run").write_text("run-test-123\n", encoding="utf-8")
    (lictor_dir / "journal.jsonl").write_text(
        '{"specversion":"1.0","id":"1","source":"lictor/0.1.0","type":"lictor.run.finished.v1","time":"2026-10-30T10:00:00Z","runid":"run-test-123","data":{"exit_code":0,"totals":{}}}\n',
        encoding="utf-8",
    )
    lictor_json = {
        "profile": "python-uv@0.1",
        "backend": {"kind": "fake", "model": "fake-model-1"},
        "paths": {"writable": ["src/"], "driver": ["tests/"], "protected": ["lictor.json"]},
        "acceptance": {"run": "echo test {out}", "report": "exit-code"},
        "controls": [{"id": c, "enabled": False} for c in ("lint", "types", "unit", "coverage")],
        "agent_tools": [],
        "hints": "",
        "gitignore": [],
    }
    (tmp_path / "lictor.json").write_text(json.dumps(lictor_json), encoding="utf-8")

    res_stat = runner.invoke(app, ["status", "--json", "--cwd", str(tmp_path)])
    assert res_stat.exit_code == 0
    parsed = json.loads(res_stat.output)
    assert parsed["run_id"] == "run-test-123"

    res_rep = runner.invoke(app, ["report", "--cwd", str(tmp_path)])
    assert res_rep.exit_code == 0
    assert (lictor_dir / "report.md").is_file()


def test_cli_doctor_exits_40_when_the_executor_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert runner.invoke(app, ["init", "--cwd", str(tmp_path)]).exit_code == 0
    config = json.loads((tmp_path / "lictor.json").read_text(encoding="utf-8"))
    config["backend"]["kind"] = "fake"
    (tmp_path / "lictor.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "features").mkdir()
    (tmp_path / "features" / "x.feature").write_text(
        "Feature: X\n  @X-001\n  Scenario: Uno\n    Given algo\n", encoding="utf-8"
    )
    (tmp_path / SKILL_FILE).write_text("---\nname: lictor\n---\nde otra versión\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=tmp_path, check=True, capture_output=True)
    broken = Health(ok=False, cli_version="", auth_status="none", message="sin sesión")
    monkeypatch.setattr(FakeExecutor, "health", lambda _self: broken)

    result = runner.invoke(app, ["doctor", "--cwd", str(tmp_path)])

    assert result.exit_code == 40
    assert "✗ Ejecutor: el ejecutor no está disponible: sin sesión" in result.output
    # The stub isn't this version's: worth telling, but not what stops the run.
    assert "⚠ Skill de Lictor:" in result.output
    assert "ejecuta `lictor skill`" in result.output


@pytest.mark.parametrize("command", [["run"], ["init", "--force"]])
def test_cli_refuses_while_another_run_holds_the_project(
    tmp_path: Path, command: list[str]
) -> None:
    (tmp_path / "lictor.json").write_text("{}", encoding="utf-8")

    with exclusive_run(tmp_path, "lictor run"):
        result = runner.invoke(app, [*command, "--cwd", str(tmp_path)])

    assert result.exit_code == 10, result.output
    assert "otra ejecución" in result.output
    assert (tmp_path / "lictor.json").read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in (tmp_path / ".lictor").iterdir()) == ["run.lock"]


def _installed_version() -> str:
    """The version of the installed package, or the marker of a tree that was never installed.

    Lictor verifies itself in an environment where the project isn't installed, so both happen.
    """
    try:
        return version("lictor")
    except PackageNotFoundError:
        return "0+desconocida"


def test_cli_version_is_the_installed_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.startswith(f"lictor {_installed_version()}")
    assert lictor.__version__ == _installed_version()


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        (None, "lictor 0.2.0"),
        ('{"url": "https://example.org/lictor.whl", "archive_info": {}}', "lictor 0.2.0"),
        ('{"url": "file:///tmp/x.whl", "archive_info": {}}', "lictor 0.2.0"),
        ('{"url": "file:///home/ana/lictor", "dir_info": {}}', "lictor 0.2.0"),
        (
            '{"url": "file:///home/ana/lictor", "dir_info": {"editable": true}}',
            "lictor 0.2.0 (editable, desde /home/ana/lictor)",
        ),
        ('{"dir_info": {"editable": true}}', "lictor 0.2.0 (editable)"),
        ("no es JSON", "lictor 0.2.0"),
        ("[1]", "lictor 0.2.0"),
    ],
)
def test_cli_version_says_when_the_installation_is_editable(
    direct_url: str | None, expected: str
) -> None:
    """A venv with an editable Lictor first on the PATH looks like the released one (F01)."""
    assert version_line("0.2.0", direct_url) == expected


def _python_project(path: Path) -> None:
    _init_git_repo(path)
    (path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")


def test_cli_init_installs_the_skill_stub_and_says_so(tmp_path: Path) -> None:
    _python_project(tmp_path)

    result = runner.invoke(app, ["init", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / SKILL_FILE).read_text(encoding="utf-8") == skill_stub()
    assert ".claude/skills/lictor/SKILL.md" in result.output


def test_cli_init_summarizes_what_it_wrote_and_says_the_next_step(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.14"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["init", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, result.output
    out = result.output
    assert "Perfil: python-uv@0.1" in out
    assert "Modelo: claude-sonnet-5-5" in out
    assert "Python >=3.14" in out
    assert ".gitignore: añadidas .lictor/" in out
    assert "Siguiente paso:" in out
    assert out.index("commit") < out.index("lictor doctor")


def test_cli_init_without_the_skill_leaves_no_stub(tmp_path: Path) -> None:
    _python_project(tmp_path)

    result = runner.invoke(app, ["init", "--no-skill", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude").exists()


def test_cli_skill_restores_the_stub_without_touching_the_configuration(tmp_path: Path) -> None:
    (tmp_path / "lictor.json").write_text("{}", encoding="utf-8")
    (tmp_path / SKILL_FILE).parent.mkdir(parents=True)
    (tmp_path / SKILL_FILE).write_text("editado\n", encoding="utf-8")

    result = runner.invoke(app, ["skill", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / SKILL_FILE).read_text(encoding="utf-8") == skill_stub()
    assert (tmp_path / "lictor.json").read_text(encoding="utf-8") == "{}"


def test_cli_skill_show_prints_the_content_anywhere_and_writes_nothing(tmp_path: Path) -> None:
    result = runner.invoke(app, ["skill", "--show", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert f"Lictor {lictor.__version__}" in result.output.splitlines()[0]
    assert "## Perfil python-uv" in result.output
    assert "No lances `lictor run`" in result.output
    assert list(tmp_path.iterdir()) == []
