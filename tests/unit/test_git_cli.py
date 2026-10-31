"""Unit tests for GitCli VCS adapter."""

import shutil
import subprocess
from pathlib import Path

import pytest

from lictor.adapters.vcs.git_cli import GitCli
from lictor.domain.errors import CandidateError

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


def _head(repo: Path) -> str:
    return subprocess.run(
        [GIT_BIN, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_candidate_carries_author_and_trailers_without_moving_the_branch(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "file_a.txt").write_text("content", encoding="utf-8")
    head = _head(git_repo)

    candidate = cli.create_candidate(
        message="feat: implement iva_001",
        trailers={"Scenario": "IVA-001", "Lictor-Run": "run-1234", "Lictor-Attempts": "1"},
    )

    assert _head(git_repo) == head
    assert cli.status() == ["file_a.txt"]
    assert cli.get_tree(candidate.sha) == candidate.tree
    assert cli.get_trailers("Scenario", commit_ref=candidate.sha) == ["IVA-001"]
    assert cli.get_trailers("Lictor-Run", commit_ref=candidate.sha) == ["run-1234"]
    author = subprocess.run(
        [GIT_BIN, "log", "-1", "--format=%an <%ae>", candidate.sha],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert author.stdout.strip() == "Lictor <lictor@localhost>"


def test_candidate_does_not_touch_the_index(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "file_a.txt").write_text("content", encoding="utf-8")

    cli.create_candidate(message="m")

    staged = subprocess.run(
        [GIT_BIN, "diff", "--cached", "--name-only"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert staged.stdout == ""


def test_candidate_only_takes_the_given_paths(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "src").mkdir()
    (git_repo / "src" / "code.py").write_text("print(1)\n", encoding="utf-8")
    (git_repo / "leak.txt").write_text("fuera\n", encoding="utf-8")

    candidate = cli.create_candidate(message="m", paths=["src/", "falta/"])

    files = _tree_files(git_repo, candidate.sha)
    assert "src/code.py" in files
    assert "leak.txt" not in files


def test_candidate_without_paths_takes_everything_but_ignored_files(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "nuevo.txt").write_text("x\n", encoding="utf-8")
    (git_repo / "oculto.ignored").write_text("x\n", encoding="utf-8")

    candidate = cli.create_candidate(message="m")

    files = _tree_files(git_repo, candidate.sha)
    assert "nuevo.txt" in files
    assert "oculto.ignored" not in files


def test_candidate_allows_empty_changes(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    head = _head(git_repo)

    candidate = cli.create_candidate(message="m", trailers={"Scenario": "IVA-003"})

    assert candidate.tree == cli.get_tree(head)
    assert candidate.sha != head


def _tree_files(repo: Path, rev: str) -> list[str]:
    return subprocess.run(
        [GIT_BIN, "ls-tree", "-r", "--name-only", rev],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


def test_export_tree_has_the_commit_and_nothing_ignored(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "nuevo.txt").write_text("x\n", encoding="utf-8")
    (git_repo / "oculto.ignored").write_text("x\n", encoding="utf-8")
    (git_repo / ".lictor").mkdir()
    candidate = cli.create_candidate(message="m")
    dest = tmp_path.parent / f"{tmp_path.name}-export"

    cli.export_tree(candidate.sha, dest)

    assert (dest / "nuevo.txt").is_file()
    assert (dest / "README.md").is_file()
    assert not (dest / "oculto.ignored").exists()
    assert not (dest / ".lictor").exists()
    assert GitCli(dest).get_tree("HEAD") == candidate.tree
    assert cli.get_head_message() == "initial commit"


def test_export_tree_ignores_the_hooks_of_the_template(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    hook = git_repo / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\ntouch delatado\n", encoding="utf-8")
    hook.chmod(0o755)
    dest = tmp_path.parent / f"{tmp_path.name}-export"

    cli.export_tree("HEAD", dest)

    assert not (dest / "delatado").exists()
    assert not (git_repo / "delatado").exists()


def test_export_tree_ignores_the_git_template(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = tmp_path.parent / f"{tmp_path.name}-template"
    (template / "info").mkdir(parents=True)
    (template / "info" / "exclude").write_text("*\n", encoding="utf-8")
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))
    dest = tmp_path.parent / f"{tmp_path.name}-export"

    GitCli(repo_dir=git_repo).export_tree("HEAD", dest)

    assert not (dest / ".git" / "info" / "exclude").exists()


def test_advance_head_moves_the_branch_to_the_exact_commit(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "file_a.txt").write_text("verificado", encoding="utf-8")
    head = _head(git_repo)
    candidate = cli.create_candidate(message="m", trailers={"Scenario": "IVA-001"})
    (git_repo / "file_a.txt").write_text("cambiado después", encoding="utf-8")

    cli.advance_head(candidate.sha, expected=head)

    assert _head(git_repo) == candidate.sha
    shown = subprocess.run(
        [GIT_BIN, "show", "HEAD:file_a.txt"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert shown.stdout == "verificado"
    assert cli.status() == ["file_a.txt"], "el cambio posterior no debe entrar ni perderse"


def test_advance_head_leaves_the_tree_clean_when_nothing_changed(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "file_a.txt").write_text("x", encoding="utf-8")
    head = _head(git_repo)
    candidate = cli.create_candidate(message="m")

    cli.advance_head(candidate.sha, expected=head)

    assert cli.status() == []


def test_advance_head_refuses_if_the_branch_moved(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    head = _head(git_repo)
    candidate = cli.create_candidate(message="m")
    subprocess.run([GIT_BIN, "commit", "--allow-empty", "-m", "otro"], cwd=git_repo, check=True)
    moved = _head(git_repo)

    with pytest.raises(CandidateError):
        cli.advance_head(candidate.sha, expected=head)

    assert _head(git_repo) == moved


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


def test_revert_paths_removes_broken_symlinks(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    broken_symlink = git_repo / "features" / "broken.feature"
    broken_symlink.parent.mkdir(parents=True, exist_ok=True)
    broken_symlink.symlink_to(git_repo / "nonexistent_target_file")
    assert broken_symlink.is_symlink()
    assert not broken_symlink.exists()

    cli.revert_paths(["features/broken.feature"])
    assert not broken_symlink.is_symlink()
    assert not broken_symlink.exists()


def test_reset_to_head_clears_staged_changes(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "README.md").write_text("# Modified and staged\n", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "README.md"], cwd=git_repo, check=True)

    cli.reset_to_head()
    assert cli.status() == []
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "# Test Repo\n"
