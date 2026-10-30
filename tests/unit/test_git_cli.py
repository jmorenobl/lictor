"""Unit tests for GitCli VCS adapter."""

import shutil
import subprocess
from pathlib import Path

import pytest

from lictor.adapters.vcs.git_cli import GitCli

GIT_BIN = shutil.which("git") or "git"


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create an isolated real git repository for testing GitCli."""
    subprocess.run([GIT_BIN, "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [GIT_BIN, "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        [GIT_BIN, "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )

    # Add .gitignore
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n.lictor/\n*.ignored\n", encoding="utf-8")

    # Initial file and commit
    readme = tmp_path / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "."], cwd=tmp_path, check=True)
    subprocess.run([GIT_BIN, "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    return tmp_path


def test_status_porcelain_detects_changes(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    assert cli.status() == []

    # Modify tracked file
    (git_repo / "README.md").write_text("# Changed\n", encoding="utf-8")

    # Add new untracked file
    new_file = git_repo / "src" / "main.py"
    new_file.parent.mkdir(parents=True, exist_ok=True)
    new_file.write_text("print('hello')\n", encoding="utf-8")

    status = cli.status()
    assert "README.md" in status
    assert "src/main.py" in status


def test_reset_to_head_preserves_ignored_files(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)

    # Modify tracked file
    (git_repo / "README.md").write_text("# Broken\n", encoding="utf-8")

    # Create untracked file
    (git_repo / "untracked.txt").write_text("trash", encoding="utf-8")

    # Create ignored directories and files
    venv_file = git_repo / ".venv" / "bin" / "python"
    venv_file.parent.mkdir(parents=True, exist_ok=True)
    venv_file.write_text("fake binary", encoding="utf-8")

    lictor_state = git_repo / ".lictor" / "state.json"
    lictor_state.parent.mkdir(parents=True, exist_ok=True)
    lictor_state.write_text('{"ok": true}', encoding="utf-8")

    cli.reset_to_head()

    # Tracked file restored
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "# Test Repo\n"
    # Untracked file removed
    assert not (git_repo / "untracked.txt").exists()
    # Ignored files preserved!
    assert venv_file.exists()
    assert lictor_state.exists()


def test_commit_with_trailers_and_get_trailers(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)

    file_a = git_repo / "file_a.txt"
    file_a.write_text("content", encoding="utf-8")

    sha = cli.commit(
        message="feat: implement iva_001",
        trailers={
            "Scenario": "IVA-001",
            "Lictor-Run": "run-1234",
            "Lictor-Attempts": "1",
        },
    )

    assert len(sha) >= 7

    # Verify author is Lictor <lictor@localhost>
    author_res = subprocess.run(
        [GIT_BIN, "log", "-1", "--format=%an <%ae>"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert author_res.stdout.strip() == "Lictor <lictor@localhost>"

    # Verify get_trailers
    scenarios = cli.get_trailers("Scenario")
    assert "IVA-001" in scenarios

    runs = cli.get_trailers("Lictor-Run")
    assert "run-1234" in runs


def test_revert_paths_selective(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)

    # Valid change
    valid = git_repo / "src" / "valid.py"
    valid.parent.mkdir(parents=True, exist_ok=True)
    valid.write_text("valid = True\n", encoding="utf-8")

    # Invalid modification to tracked file
    (git_repo / "README.md").write_text("# Modified by mistake\n", encoding="utf-8")

    # Invalid untracked file
    forbidden = git_repo / "forbidden.txt"
    forbidden.write_text("forbidden\n", encoding="utf-8")

    # Revert only forbidden files
    cli.revert_paths(["README.md", "forbidden.txt"])

    # README.md reverted
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "# Test Repo\n"
    # forbidden.txt deleted
    assert not forbidden.exists()
    # valid.py preserved
    assert valid.exists()
    assert "src/valid.py" in cli.status()


def test_diff_shows_changes(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "README.md").write_text("# Test Repo\n+ new line\n", encoding="utf-8")
    diff = cli.diff()
    assert "+ new line" in diff
