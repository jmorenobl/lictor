import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lictor.adapters.agents.fake import FakeExecutor
from lictor.cli import app
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


def test_cli_init_and_doctor(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: initial"], cwd=tmp_path, check=True, capture_output=True
    )

    result = runner.invoke(app, ["init", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / "lictor.json").is_file()
    assert (tmp_path / ".lictor").is_dir()

    # Commit the generated config to have clean tree for doctor
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: add lictor"], cwd=tmp_path, check=True, capture_output=True
    )

    doc_res = runner.invoke(app, ["doctor", "--cwd", str(tmp_path)])
    # Doctor exits with 0 or 10 depending on whether claude binary is installed in environment
    assert doc_res.exit_code in (0, 10)


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


def test_cli_check_success_and_filter(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    lictor_json = {
        "profile": "python-uv@0.1",
        "backend": {"kind": "fake", "model": "fake-model-1"},
        "paths": {"writable": ["src/"], "driver": ["tests/"], "protected": ["lictor.json"]},
        "acceptance": {"run": "echo test {out}", "report": "exit-code"},
        "controls": [
            {"id": "ctrl1", "level": "L1", "run": "echo ctrl1", "report": "exit-code"},
            {"id": "ctrl2", "level": "L2", "run": "echo ctrl2", "report": "exit-code"},
        ],
        "suppressions": [],
        "agent_tools": [],
        "hints": "",
        "gitignore": [],
    }
    (tmp_path / "lictor.json").write_text(json.dumps(lictor_json), encoding="utf-8")

    res = runner.invoke(app, ["check", "--cwd", str(tmp_path)])
    assert res.exit_code == 0
    assert "ctrl1" in res.output
    assert "ctrl2" in res.output

    res_l1 = runner.invoke(app, ["check", "--level", "L1", "--cwd", str(tmp_path)])
    assert res_l1.exit_code == 0
    assert "ctrl1" in res_l1.output


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
        "controls": [],
        "suppressions": [],
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
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=tmp_path, check=True, capture_output=True)
    broken = Health(ok=False, cli_version="", auth_status="none", message="sin sesión")
    monkeypatch.setattr(FakeExecutor, "health", lambda _self: broken)

    result = runner.invoke(app, ["doctor", "--cwd", str(tmp_path)])

    assert result.exit_code == 40


def test_cli_doctor_fails_on_missing_control_binary(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    lictor_json = {
        "profile": "python-uv@0.1",
        "backend": {"kind": "fake", "model": "fake-model-1"},
        "paths": {"writable": ["src/"], "driver": ["tests/"], "protected": ["lictor.json"]},
        "acceptance": {"run": "echo test {out}", "report": "exit-code"},
        "controls": [
            {
                "id": "missing_tool",
                "level": "L4",
                "run": "nonexistent_cli_tool_binary_xyz --check",
                "report": "exit-code",
            },
        ],
        "suppressions": [],
        "agent_tools": [],
        "hints": "",
        "gitignore": [],
    }
    (tmp_path / "lictor.json").write_text(json.dumps(lictor_json), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: setup"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    doc_res = runner.invoke(app, ["doctor", "--cwd", str(tmp_path)])
    assert doc_res.exit_code == 10
    assert "no está disponible o no es ejecutable" in doc_res.output
