"""Unit tests for GitCli VCS adapter."""

import shutil
import subprocess
from pathlib import Path

import pytest

from lictor.adapters.vcs.git_cli import GitCli
from lictor.domain.errors import CandidateError
from lictor.domain.model import Candidate

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


def _head(repo: Path) -> str:
    return subprocess.run(
        [GIT_BIN, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _copy(cli: GitCli, tmp_path: Path) -> Path:
    # `tmp_path` is the repository of these tests, and a copy inside would count as its changes.
    workdir = tmp_path.with_name(f"{tmp_path.name}-copia")
    cli.export_copy("HEAD", workdir)
    return workdir


def test_export_copy_has_the_files_and_no_repository(git_repo: Path, tmp_path: Path) -> None:
    (git_repo / "x.ignored").write_text("ignorado", encoding="utf-8")
    cli = GitCli(repo_dir=git_repo)

    workdir = _copy(cli, tmp_path)

    assert sorted(p.name for p in workdir.iterdir()) == [".gitignore", "README.md"]


def test_capture_reads_additions_edits_and_deletions(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "nuevo.txt").write_text("n\n", encoding="utf-8")
    (workdir / "README.md").unlink()

    tree = cli.capture(workdir)

    assert sorted((c.path, c.mode) for c in cli.changes(tree)) == [
        ("README.md", "000000"),
        ("nuevo.txt", "100644"),
    ]
    assert cli.status() == [], "capturar no toca el proyecto"


def test_capture_leaves_what_git_ignores_out(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "x.ignored").write_text("i", encoding="utf-8")
    (workdir / ".venv").mkdir()
    (workdir / ".venv" / "bin").write_text("b", encoding="utf-8")

    assert cli.changes(cli.capture(workdir)) == []


def test_capture_skips_paths_back_to_their_version_in_head(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "README.md").write_text("cambiado", encoding="utf-8")
    (workdir / "nuevo.txt").write_text("n", encoding="utf-8")
    (workdir / "fuera.txt").write_text("f", encoding="utf-8")

    tree = cli.capture(workdir, skip=["README.md", "fuera.txt"])

    assert [c.path for c in cli.changes(tree)] == ["nuevo.txt"]


def test_capture_keeps_links_visible(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "enlace").symlink_to("/etc/hosts")

    changes = cli.changes(cli.capture(workdir))

    assert [(c.path, c.is_link) for c in changes] == [("enlace", True)]


def test_capture_sees_a_nested_repository_as_a_link(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    nested = workdir / "anidado"
    nested.mkdir()
    subprocess.run([GIT_BIN, "init", "-q"], cwd=nested, check=True)
    (nested / "a").write_text("a", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "."], cwd=nested, check=True)
    identity = ["-c", "user.name=a", "-c", "user.email=a@b"]
    subprocess.run([GIT_BIN, *identity, "commit", "-qm", "x"], cwd=nested, check=True)

    changes = cli.changes(cli.capture(workdir))

    assert [(c.path, c.mode, c.is_link) for c in changes] == [("anidado", "160000", True)]


def test_capture_that_git_cant_read_is_a_candidate_error(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    nested = workdir / "vacio"
    nested.mkdir()
    subprocess.run([GIT_BIN, "init", "-q"], cwd=nested, check=True)

    with pytest.raises(CandidateError, match="^No se pudo leer"):
        cli.capture(workdir)


def test_capture_ignores_the_user_configuration(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A global `core.excludesFile` must not decide what the agent's result holds."""
    excludes = tmp_path / "excludes"
    excludes.write_text("*.txt\n", encoding="utf-8")
    config = tmp_path / "gitconfig"
    config.write_text(f"[core]\n\texcludesFile = {excludes}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "nuevo.txt").write_text("n", encoding="utf-8")

    assert [c.path for c in cli.changes(cli.capture(workdir))] == ["nuevo.txt"]


def test_changes_report_a_rename_as_deletion_and_addition(git_repo: Path, tmp_path: Path) -> None:
    """A rename must not hide the path it took the file from."""
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "README.md").rename(workdir / "LEEME.md")

    assert sorted(c.path for c in cli.changes(cli.capture(workdir))) == ["LEEME.md", "README.md"]


def test_added_lines_come_only_from_what_the_tree_adds(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "README.md").write_text("# Test Repo\nañadida\n++ con signos\n", encoding="utf-8")
    (workdir / "nuevo.txt").write_text("del nuevo\n", encoding="utf-8")

    added = cli.added_lines(cli.capture(workdir))

    assert sorted(added) == ["++ con signos", "añadida", "del nuevo"]


def test_added_lines_see_through_binary_files(git_repo: Path, tmp_path: Path) -> None:
    """A NUL or invalid UTF-8 must neither hide a banned line nor break the run."""
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "datos.py").write_bytes(b"\x00\xff\xfe\nx = 1  # noqa\n")

    assert "x = 1  # noqa" in cli.added_lines(cli.capture(workdir))


def test_commit_tree_carries_author_and_trailers_without_moving_the_branch(
    git_repo: Path, tmp_path: Path
) -> None:
    cli = GitCli(repo_dir=git_repo)
    head = _head(git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "file_a.txt").write_text("hello", encoding="utf-8")
    tree = cli.capture(workdir)

    candidate = cli.commit_tree(tree, "feat: test", {"Scenario": "IVA-001", "Lictor-Run": "r"})

    assert _head(git_repo) == head
    assert candidate.parent == head
    assert candidate.tree == tree
    assert cli.get_tree(candidate.sha) == tree
    assert cli.get_trailers("Scenario", commit_ref=candidate.sha) == ["IVA-001"]
    author = subprocess.run(
        [GIT_BIN, "log", "-1", "--format=%an <%ae>", candidate.sha],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert author == "Lictor <lictor@localhost>"


def test_commit_tree_allows_an_empty_change(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    head = _head(git_repo)

    candidate = cli.commit_tree(cli.get_tree("HEAD"), "m")

    assert candidate.parent == head
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
    candidate = cli.commit_tree(cli.capture(git_repo), "m")
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


def _candidate(cli: GitCli, tmp_path: Path, content: str = "verificado") -> Candidate:
    workdir = tmp_path.with_name(f"{tmp_path.name}-candidata")
    cli.export_copy("HEAD", workdir)
    (workdir / "file_a.txt").write_text(content, encoding="utf-8")
    candidate = cli.commit_tree(cli.capture(workdir), "m", {"Scenario": "IVA-001"})
    shutil.rmtree(workdir)
    return candidate


def test_advance_head_brings_the_commit_to_the_working_tree(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    candidate = _candidate(cli, tmp_path)

    cli.advance_head(candidate.sha, expected=candidate.parent)

    assert _head(git_repo) == candidate.sha
    assert (git_repo / "file_a.txt").read_text(encoding="utf-8") == "verificado"
    assert cli.status() == []


def test_advance_head_keeps_edits_the_commit_doesnt_touch(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    candidate = _candidate(cli, tmp_path)
    (git_repo / "README.md").write_text("del usuario", encoding="utf-8")
    (git_repo / "notas.txt").write_text("sin versionar", encoding="utf-8")

    cli.advance_head(candidate.sha, expected=candidate.parent)

    assert _head(git_repo) == candidate.sha
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "del usuario"
    assert (git_repo / "notas.txt").read_text(encoding="utf-8") == "sin versionar"


@pytest.mark.parametrize("tracked", [True, False], ids=["versionado", "sin-versionar"])
def test_advance_head_refuses_to_overwrite_an_edit(
    git_repo: Path, tmp_path: Path, tracked: bool
) -> None:
    cli = GitCli(repo_dir=git_repo)
    if tracked:
        (git_repo / "file_a.txt").write_text("base", encoding="utf-8")
        subprocess.run([GIT_BIN, "add", "."], cwd=git_repo, check=True)
        subprocess.run([GIT_BIN, "commit", "-qm", "base"], cwd=git_repo, check=True)
    candidate = _candidate(cli, tmp_path)
    (git_repo / "file_a.txt").write_text("del usuario", encoding="utf-8")

    with pytest.raises(CandidateError, match="file_a.txt"):
        cli.advance_head(candidate.sha, expected=candidate.parent)

    assert _head(git_repo) == candidate.parent
    assert (git_repo / "file_a.txt").read_text(encoding="utf-8") == "del usuario"


def test_advance_head_refuses_if_the_branch_moved(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    candidate = _candidate(cli, tmp_path)
    subprocess.run([GIT_BIN, "commit", "--allow-empty", "-m", "otro"], cwd=git_repo, check=True)
    moved = _head(git_repo)

    with pytest.raises(CandidateError):
        cli.advance_head(candidate.sha, expected=candidate.parent)

    assert _head(git_repo) == moved


def test_advance_head_refuses_a_sha_that_is_not_a_fast_forward(
    git_repo: Path, tmp_path: Path
) -> None:
    cli = GitCli(repo_dir=git_repo)
    head = _head(git_repo)
    candidate = _candidate(cli, tmp_path)
    cli.advance_head(candidate.sha, expected=head)
    other = _candidate(cli, tmp_path, "otro")
    subprocess.run([GIT_BIN, "reset", "-q", "--hard", head], cwd=git_repo, check=True)
    (git_repo / "aparte.txt").write_text("a", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "."], cwd=git_repo, check=True)
    subprocess.run([GIT_BIN, "commit", "-qm", "rama"], cwd=git_repo, check=True)
    diverged = _head(git_repo)

    with pytest.raises(CandidateError):
        cli.advance_head(other.sha, expected=diverged)

    assert _head(git_repo) == diverged


def test_git_output_that_is_not_utf8_doesnt_break_the_adapter(git_repo: Path) -> None:
    (git_repo / "binario").write_bytes(b"\xff\xfe")
    cli = GitCli(repo_dir=git_repo)

    assert cli.status() == ["binario"]
