"""Unit tests for GitCli VCS adapter."""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from lictor.adapters.vcs.git_cli import GitCli
from lictor.domain.errors import CandidateError, EnvironmentUnavailableError
from lictor.domain.model import Candidate
from lictor.domain.refactor_scope import LineSpan

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


def test_capture_leaves_out_what_the_profile_ignores_without_a_gitignore(
    git_repo: Path, tmp_path: Path
) -> None:
    cli = GitCli(repo_dir=git_repo, ignored=[".coverage", ".coverage.*"])
    workdir = _copy(cli, tmp_path)
    (workdir / ".coverage").write_text("datos", encoding="utf-8")
    (workdir / ".coverage.host.1").write_text("datos", encoding="utf-8")
    (workdir / "nuevo.txt").write_text("n", encoding="utf-8")

    assert [c.path for c in cli.changes(cli.capture(workdir))] == ["nuevo.txt"]


def test_what_the_profile_ignores_cant_hide_a_tracked_file(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo, ignored=["README.md"])
    workdir = _copy(cli, tmp_path)
    (workdir / "README.md").write_text("cambiado", encoding="utf-8")

    assert [c.path for c in cli.changes(cli.capture(workdir))] == ["README.md"]


def test_capture_doesnt_read_the_users_global_ignore_file(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "xdg"
    (config_home / "git").mkdir(parents=True)
    (config_home / "git" / "ignore").write_text("oculto.txt\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "oculto.txt").write_text("o", encoding="utf-8")

    assert [c.path for c in cli.changes(cli.capture(workdir))] == ["oculto.txt"]


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

    with pytest.raises(CandidateError, match=r"^No se pudo leer"):
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


def test_changed_paths_name_what_a_commit_changes_renames_as_two(
    git_repo: Path, tmp_path: Path
) -> None:
    """What a green commit changed is what the refactor is told to look at first (ADR-0010)."""
    cli = GitCli(repo_dir=git_repo)
    base = cli.get_head_sha()
    workdir = _copy(cli, tmp_path)
    (workdir / "README.md").rename(workdir / "LEEME.md")
    (workdir / "src").mkdir()
    (workdir / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    commit = cli.commit_tree(cli.capture(workdir), message="cambio")

    assert sorted(cli.changed_paths(base, commit.sha)) == ["LEEME.md", "README.md", "src/a.py"]
    assert cli.changed_paths(base, base) == []


def test_changes_tell_a_new_path_from_one_head_had(git_repo: Path, tmp_path: Path) -> None:
    """F84: a refactor may create files; changing or deleting one that existed is its scope's."""
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "README.md").write_text("otro\n", encoding="utf-8")
    (workdir / ".gitignore").unlink()
    (workdir / "nuevo.txt").write_text("n\n", encoding="utf-8")

    found = {c.path: (c.is_new, c.is_deleted) for c in cli.changes(cli.capture(workdir))}

    assert found == {
        "README.md": (False, False),
        ".gitignore": (False, True),
        "nuevo.txt": (True, False),
    }


def _green(
    cli: GitCli, tmp_path: Path, files: dict[str, str | None], mode: int | None = None
) -> str:
    """A commit outside the branch writing (or deleting, with None) each file over HEAD."""
    workdir = _copy(cli, tmp_path)
    for name, text in files.items():
        path = workdir / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if mode is not None:
            path.chmod(mode)
    return cli.commit_tree(cli.capture(workdir), message="verde").sha


def test_changed_lines_are_what_the_commit_adds_or_changes_in_its_numbering(
    git_repo: Path, tmp_path: Path
) -> None:
    """F84: what the green touched, to scope its refactor; a hunk that only deletes is its line."""
    cli = GitCli(repo_dir=git_repo)
    lines = [f"l{i}" for i in range(1, 21)]
    (git_repo / "m.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "."], cwd=git_repo, check=True)
    subprocess.run([GIT_BIN, "commit", "-qm", "base"], cwd=git_repo, check=True)
    base = cli.get_head_sha()
    edited = [*lines[:2], "nueva", *lines[2:4], *lines[6:9], "otra", *lines[10:]]
    green = _green(cli, tmp_path, {"m.py": "\n".join(edited) + "\n"})

    assert cli.changed_lines(base, green) == {
        "m.py": (LineSpan(3, 3), LineSpan(5, 5), LineSpan(9, 9)),
    }


def test_changed_lines_name_every_file_head_has_even_without_lines(
    git_repo: Path, tmp_path: Path
) -> None:
    """An empty file added, a new file, a line that adds `++` and a path with spaces and accents;
    a deleted file isn't there, since HEAD no longer has it."""
    cli = GitCli(repo_dir=git_repo)
    base = cli.get_head_sha()
    green = _green(
        cli,
        tmp_path,
        {
            "pkg/__init__.py": "",
            "con espacio ñ.py": "a\n++ b\n@@ -1 +1 @@\n",
            "README.md": None,
        },
    )

    assert cli.changed_lines(base, green) == {
        "con espacio ñ.py": (LineSpan(1, 3),),
        "pkg/__init__.py": (),
    }


def test_changed_lines_keep_a_file_that_only_changed_its_mode(
    git_repo: Path, tmp_path: Path
) -> None:
    cli = GitCli(repo_dir=git_repo)
    base = cli.get_head_sha()
    green = _green(cli, tmp_path, {"README.md": "# Test Repo\n"}, mode=0o755)

    assert cli.changed_lines(base, green) == {"README.md": ()}


def test_added_lines_come_only_from_what_the_tree_adds(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "README.md").write_text("# Test Repo\nañadida\n++ con signos\n", encoding="utf-8")
    (workdir / "nuevo.txt").write_text("del nuevo\n", encoding="utf-8")

    added = cli.added_lines(cli.capture(workdir))

    assert sorted(added) == ["++ con signos", "añadida", "del nuevo"]


def test_removed_lines_are_what_the_tree_removes_or_moves_away(
    git_repo: Path, tmp_path: Path
) -> None:
    """A renamed file is a deletion and an addition: its lines are removed and added (F72)."""
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    readme = (workdir / "README.md").read_text(encoding="utf-8")
    (workdir / "README.md").unlink()
    (workdir / "LEEME.md").write_text(readme + "-- con signos\n", encoding="utf-8")

    tree = cli.capture(workdir)

    assert cli.removed_lines(tree) == readme.splitlines()
    assert cli.added_lines(tree) == [*readme.splitlines(), "-- con signos"]


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


def test_commit_tree_refuses_an_empty_change_unless_asked(git_repo: Path) -> None:
    """An attempt that changes nothing is not a candidate; only a registration may be empty."""
    cli = GitCli(repo_dir=git_repo)
    head = _head(git_repo)

    with pytest.raises(CandidateError, match="no cambia nada"):
        cli.commit_tree(cli.get_tree("HEAD"), "m")
    candidate = cli.commit_tree(cli.get_tree("HEAD"), "m", empty=True)

    assert candidate.parent == head
    assert candidate.sha != head


def test_commit_tree_chains_on_a_given_parent(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    head = _head(git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "file_a.txt").write_text("encadenado", encoding="utf-8")
    first = cli.commit_tree(cli.capture(workdir), "primero")

    second = cli.commit_tree(
        first.tree, "segundo", {"Scenario": ["IVA-002", "IVA-003"]}, parent=first.sha, empty=True
    )

    assert _head(git_repo) == head
    assert second.parent == first.sha
    assert cli.get_tree(second.sha) == first.tree
    assert cli.get_trailers("Scenario", commit_ref=second.sha) == ["IVA-002", "IVA-003"]
    with pytest.raises(CandidateError):
        cli.commit_tree(first.tree, "vacío", parent=first.sha)


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

    with pytest.raises(CandidateError, match=r"file_a\.txt"):
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


def test_advance_head_runs_no_hook_of_the_repository(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    candidate = _candidate(cli, tmp_path)
    marker = tmp_path / "hook-ejecutado"
    hook = git_repo / ".git" / "hooks" / "post-merge"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)

    cli.advance_head(candidate.sha, expected=candidate.parent)

    assert _head(git_repo) == candidate.sha
    assert not marker.exists()


def test_advance_head_refuses_if_the_branch_went_back(git_repo: Path, tmp_path: Path) -> None:
    """Fast-forwarding would succeed here: only the comparison with `expected` stops it."""
    subprocess.run([GIT_BIN, "commit", "--allow-empty", "-m", "base"], cwd=git_repo, check=True)
    cli = GitCli(repo_dir=git_repo)
    candidate = _candidate(cli, tmp_path)
    subprocess.run([GIT_BIN, "reset", "-q", "--hard", "HEAD~1"], cwd=git_repo, check=True)
    behind = _head(git_repo)

    with pytest.raises(CandidateError):
        cli.advance_head(candidate.sha, expected=candidate.parent)

    assert _head(git_repo) == behind


def _only_config_reads(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """No git may run in the repository: only `git config --file` on a copy, outside it (S22)."""
    real_run = subprocess.run

    def no_git_in_the_repository(args: list[str], **kwargs: object) -> object:
        outside = not Path(str(kwargs.get("cwd"))).resolve().is_relative_to(repo.resolve())
        if "--file" in args and "--no-includes" in args and outside:
            return real_run(args, **kwargs)  # type: ignore[call-overload]
        raise AssertionError("no debe ejecutarse git en el repositorio")

    monkeypatch.setattr(subprocess, "run", no_git_in_the_repository)


def test_fingerprint_changes_with_the_control_files_and_runs_no_git(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = GitCli(repo_dir=git_repo)
    before = cli.fingerprint()
    assert cli.fingerprint() == before

    _only_config_reads(monkeypatch, git_repo)
    for rel_path in ("config", "hooks/pre-commit", "info/attributes"):
        target = git_repo / ".git" / rel_path
        target.parent.mkdir(exist_ok=True)
        original = target.read_bytes() if target.exists() else None
        target.write_text("x\n", encoding="utf-8")
        assert cli.fingerprint() != before, rel_path
        if original is None:
            target.unlink()
        else:
            target.write_bytes(original)
        assert cli.fingerprint() == before, rel_path


def test_fingerprint_doesnt_depend_on_refs_or_objects(git_repo: Path) -> None:
    """Advancing the branch is Lictor's own job: only control files count here."""
    cli = GitCli(repo_dir=git_repo)
    before = cli.fingerprint()

    subprocess.run([GIT_BIN, "commit", "--allow-empty", "-qm", "x"], cwd=git_repo, check=True)

    assert cli.fingerprint() == before


def test_restore_brings_back_control_files_and_removes_planted_ones(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    config = git_repo / ".git" / "config"
    original = config.read_bytes()
    pin = cli.pin()
    config.write_text("[core]\n\tfsmonitor = /x\n", encoding="utf-8")
    hook = git_repo / ".git" / "hooks" / "post-merge"
    hook.write_text("#!/bin/sh\n", encoding="utf-8")

    changed = cli.restore(pin)

    assert sorted(changed) == [
        ".git/config (core.bare, core.filemode, core.fsmonitor, core.logallrefupdates, "
        "core.repositoryformatversion y 2 más)",
        ".git/hooks/post-merge",
    ]
    assert config.read_bytes() == original
    assert not hook.exists()
    assert cli.restore(pin) == []


def test_restore_replaces_a_link_instead_of_writing_through_it(
    git_repo: Path, tmp_path: Path
) -> None:
    cli = GitCli(repo_dir=git_repo)
    pin = cli.pin()
    outside = tmp_path.with_name(f"{tmp_path.name}-fuera")
    outside.write_text("ajeno", encoding="utf-8")
    config = git_repo / ".git" / "config"
    config.unlink()
    config.symlink_to(outside)

    cli.restore(pin)

    assert outside.read_text(encoding="utf-8") == "ajeno"
    assert not config.is_symlink()


def test_restore_undoes_a_commit_and_keeps_unrelated_edits(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    (git_repo / "README.md").write_text("# Editado\n", encoding="utf-8")
    pin = cli.pin()
    (git_repo / "otro.txt").write_text("x", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "otro.txt"], cwd=git_repo, check=True)
    subprocess.run([GIT_BIN, "commit", "-qm", "ajeno"], cwd=git_repo, check=True)

    assert cli.restore(pin) == ["HEAD"]

    assert _head(git_repo) == pin.head
    assert not (git_repo / "otro.txt").exists()
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "# Editado\n"


def test_restore_moves_head_even_if_the_tree_cant_follow(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    pin = cli.pin()
    (git_repo / "README.md").write_text("cambiado", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "."], cwd=git_repo, check=True)
    subprocess.run([GIT_BIN, "commit", "-qm", "ajeno"], cwd=git_repo, check=True)
    (git_repo / "README.md").write_text("sin confirmar", encoding="utf-8")

    assert cli.restore(pin) == ["HEAD"]

    assert _head(git_repo) == pin.head


def test_a_worktree_finds_its_repository_by_reading(git_repo: Path, tmp_path: Path) -> None:
    other = tmp_path.with_name(f"{tmp_path.name}-worktree")
    subprocess.run(
        [GIT_BIN, "worktree", "add", "-q", "-b", "w", str(other)], cwd=git_repo, check=True
    )

    assert GitCli(repo_dir=other).pin().files["HEAD"].startswith(b"ref:")


def test_every_call_is_hardened_against_the_repository_config(
    git_repo: Path, tmp_path: Path
) -> None:
    marker = tmp_path.with_name(f"{tmp_path.name}-marca")
    hook = git_repo / ".git" / "evil.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run([GIT_BIN, "config", "core.fsmonitor", str(hook)], cwd=git_repo, check=True)
    (git_repo / "README.md").write_text("x", encoding="utf-8")
    cli = GitCli(repo_dir=git_repo)

    cli.status()
    cli.get_head_sha()
    cli.get_trailers("Scenario")

    assert not marker.exists()


def test_inherited_git_locations_dont_redirect_the_adapter(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Launched from a git hook, GIT_DIR points wherever that hook ran."""
    head = _head(git_repo)
    monkeypatch.setenv("GIT_DIR", str(tmp_path.with_name("no-existe")))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path.with_name("no-existe-index")))

    assert GitCli(repo_dir=git_repo).get_head_sha() == head


def test_replaced_objects_are_ignored(git_repo: Path) -> None:
    head = _head(git_repo)
    subprocess.run([GIT_BIN, "commit", "--allow-empty", "-qm", "b"], cwd=git_repo, check=True)
    other = _head(git_repo)
    subprocess.run([GIT_BIN, "replace", other, head], cwd=git_repo, check=True)

    assert GitCli(repo_dir=git_repo).get_head_message() == "b"


def test_export_tree_is_independent_of_the_user_configuration(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A global `core.autocrlf` would rewrite the tree under test on that machine alone."""
    config = tmp_path.with_name(f"{tmp_path.name}-gitconfig")
    config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    dest = tmp_path.with_name(f"{tmp_path.name}-exportado")

    GitCli(repo_dir=git_repo).export_tree("HEAD", dest)

    assert b"\r" not in (dest / "README.md").read_bytes()


def test_trailer_commits_list_every_value_newest_first(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    first = cli.commit_tree(cli.get_tree("HEAD"), "a", {"Scenario": "A-1"}, empty=True)
    cli.advance_head(first.sha, expected=first.parent)
    plain = cli.commit_tree(cli.get_tree("HEAD"), "sin trailer", empty=True)
    cli.advance_head(plain.sha, expected=plain.parent)
    both = cli.commit_tree(
        cli.get_tree("HEAD"), "b", {"Scenario": ["A-2", "A-3"], "Otro": "x"}, empty=True
    )
    cli.advance_head(both.sha, expected=both.parent)

    assert cli.trailer_commits("Scenario") == [(both.sha, ["A-2", "A-3"]), (first.sha, ["A-1"])]
    assert cli.parent_of(both.sha) == plain.sha


def test_list_and_read_files_of_an_older_commit(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    workdir = _copy(cli, tmp_path)
    (workdir / "features").mkdir()
    (workdir / "features" / "a.feature").write_text("viejo\r\n", encoding="utf-8")
    (workdir / "features" / "sub").mkdir()
    (workdir / "features" / "sub" / "b.feature").write_text("b", encoding="utf-8")
    old = cli.commit_tree(cli.capture(workdir), "con features")
    cli.advance_head(old.sha, expected=old.parent)
    (git_repo / "features" / "a.feature").write_text("nuevo", encoding="utf-8")

    assert sorted(cli.list_files(old.sha, "features")) == [
        "features/a.feature",
        "features/sub/b.feature",
    ]
    assert cli.list_files(old.parent, "features") == []
    # Line endings read as `Path.read_text` reads the working tree, so both hash the same text.
    assert cli.read_file(old.sha, "features/a.feature") == "viejo\n"


def test_parent_of_a_root_commit_is_empty(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    root = subprocess.run(
        [GIT_BIN, "rev-list", "--max-parents=0", "HEAD"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[0]
    assert cli.parent_of(root) == ""


def test_is_executable_reads_the_mode_in_the_commit(git_repo: Path) -> None:
    script = git_repo / "run.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    (git_repo / "plain.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "."], cwd=git_repo, check=True)
    subprocess.run([GIT_BIN, "commit", "-qm", "scripts"], cwd=git_repo, check=True)
    (git_repo / "plain.sh").chmod(0o755)
    (git_repo / "untracked.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (git_repo / "untracked.sh").chmod(0o755)
    cli = GitCli(repo_dir=git_repo)

    assert cli.is_executable("HEAD", "run.sh") is True
    assert cli.is_executable("HEAD", "plain.sh") is False
    assert cli.is_executable("HEAD", "untracked.sh") is False
    assert cli.is_executable("HEAD", "README.md") is False


def test_committer_identity_is_the_configured_one(git_repo: Path) -> None:
    assert GitCli(repo_dir=git_repo).committer_identity() == "Test User <test@example.com>"


def test_committer_identity_refuses_what_commit_tree_refuses(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_COMMITTER_NAME", "")
    cli = GitCli(repo_dir=git_repo)

    # git's own words, whatever language it speaks: the reason carries them after ours.
    with pytest.raises(EnvironmentUnavailableError, match=r"identidad .*: fatal: "):
        cli.committer_identity()
    with pytest.raises(subprocess.CalledProcessError):
        cli.commit_tree(cli.get_tree("HEAD"), "x", empty=True)


def test_a_repository_without_commits_has_no_trailer_commits(tmp_path: Path) -> None:
    subprocess.run([GIT_BIN, "init", "-q"], cwd=tmp_path, check=True)

    assert GitCli(repo_dir=tmp_path).trailer_commits("Scenario") == []


def test_has_commits_tells_a_repository_just_created(tmp_path: Path, git_repo: Path) -> None:
    empty = tmp_path / "vacio"
    empty.mkdir()
    subprocess.run([GIT_BIN, "init", "-q"], cwd=empty, check=True)

    assert GitCli(repo_dir=empty).has_commits() is False
    assert GitCli(repo_dir=git_repo).has_commits() is True


def _linked_worktree(git_repo: Path, tmp_path: Path) -> Path:
    other = tmp_path.with_name(f"{tmp_path.name}-enlazado")
    subprocess.run(
        [GIT_BIN, "worktree", "add", "-q", "-b", "enlazado", str(other)],
        cwd=git_repo,
        check=True,
    )
    return other


def test_a_linked_worktree_sees_the_control_files_of_the_common_directory(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`config`, `hooks/` and `info/` live in the common directory, not in the worktree's."""
    worktree = _linked_worktree(git_repo, tmp_path)
    own = git_repo / ".git" / "worktrees" / worktree.name
    cli = GitCli(repo_dir=worktree)
    before = cli.fingerprint()

    _only_config_reads(monkeypatch, worktree)
    targets = [
        git_repo / ".git" / "config",
        git_repo / ".git" / "hooks" / "post-commit",
        git_repo / ".git" / "info" / "attributes",
        own / "config.worktree",
        own / "commondir",
        worktree / ".git",
    ]
    for target in targets:
        target.parent.mkdir(exist_ok=True)
        original = target.read_bytes() if target.exists() else None
        # The config counts by its guarded entries (S22): a comment alone changes none of them.
        added = b"\n[core]\n\tx = 1\n" if target.name == "config" else b"\n# x\n"
        target.write_bytes((original or b"") + added)
        assert cli.fingerprint() != before, target
        if original is None:
            target.unlink()
        else:
            target.write_bytes(original)
        assert cli.fingerprint() == before, target


def test_restore_in_a_linked_worktree_brings_back_the_common_directory(
    git_repo: Path, tmp_path: Path
) -> None:
    worktree = _linked_worktree(git_repo, tmp_path)
    cli = GitCli(repo_dir=worktree)
    config = git_repo / ".git" / "config"
    original = config.read_bytes()
    pin = cli.pin()
    config.write_text("[core]\n\tfsmonitor = /x\n", encoding="utf-8")
    hook = git_repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\n", encoding="utf-8")

    changed = cli.restore(pin)

    assert [c.split(" ")[0] for c in sorted(changed)] == [".git/config", ".git/hooks/post-commit"]
    assert config.read_bytes() == original
    assert not hook.exists()
    assert cli.restore(pin) == []


def test_restore_follows_the_pointers_it_put_back(git_repo: Path, tmp_path: Path) -> None:
    """A redirected `.git` or `commondir` must not decide where the rest is restored."""
    worktree = _linked_worktree(git_repo, tmp_path)
    cli = GitCli(repo_dir=worktree)
    commondir = git_repo / ".git" / "worktrees" / worktree.name / "commondir"
    original = commondir.read_bytes()
    pin = cli.pin()
    decoy = tmp_path / "señuelo"
    (decoy / "hooks").mkdir(parents=True)
    commondir.write_text(f"{decoy}\n", encoding="utf-8")
    config = git_repo / ".git" / "config"
    config.write_text("[core]\n\tfsmonitor = /x\n", encoding="utf-8")

    changed = cli.restore(pin)

    assert ".git/commondir" in changed
    assert any(c.startswith(".git/config (") for c in changed)
    assert commondir.read_bytes() == original
    assert b"fsmonitor" not in config.read_bytes()
    assert not (decoy / "config").exists()


def test_a_normal_checkout_keeps_the_fingerprint_it_had(git_repo: Path) -> None:
    """Same files, same names: a checkout that isn't a linked worktree hashes them as they are,
    but its config by the entries Lictor guards (S22 of 0.25.0, anchor format 3)."""
    git_dir = git_repo / ".git"
    files = {"HEAD": git_dir / "HEAD", "config": git_dir / "config"}
    for directory in ("hooks", "info"):
        for path in (git_dir / directory).rglob("*"):
            files[path.relative_to(git_dir).as_posix()] = path
    listed = subprocess.run(
        [GIT_BIN, "config", "--file", str(git_dir / "config"), "--no-includes", "--list", "-z"],
        capture_output=True,
        check=True,
    ).stdout
    guarded = b"\0".join(
        record.replace(b"\n", b"\n", 1) for record in listed.split(b"\0") if record
    )
    digest = hashlib.sha256()
    for name, path in sorted(files.items()):
        if path.is_file():
            content = guarded if name == "config" else path.read_bytes()
            digest.update(name.encode() + b"\0" + content + b"\0")

    assert GitCli(repo_dir=git_repo).fingerprint() == f"sha256:{digest.hexdigest()}"


def test_each_worktree_exports_through_its_own_ref(git_repo: Path, tmp_path: Path) -> None:
    """Two runs in two worktrees must not move a ref they share."""
    worktree = _linked_worktree(git_repo, tmp_path)
    subprocess.run([GIT_BIN, "commit", "--allow-empty", "-qm", "w"], cwd=worktree, check=True)
    main, linked = GitCli(repo_dir=git_repo), GitCli(repo_dir=worktree)

    main.export_tree("HEAD", tmp_path / "desde-main")
    linked.export_tree("HEAD", tmp_path / "desde-enlazado")

    shared = subprocess.run(
        [GIT_BIN, "for-each-ref", "refs/lictor/"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert shared.stdout == ""
    assert main._run_git(["rev-parse", "refs/worktree/lictor/verified"]).stdout.strip() == (
        main.get_head_sha()
    )
    assert linked._run_git(["rev-parse", "refs/worktree/lictor/verified"]).stdout.strip() == (
        linked.get_head_sha()
    )
    assert main.get_head_sha() != linked.get_head_sha()


def test_add_worktree_creates_the_branch_from_head_and_runs_no_hook(
    git_repo: Path, tmp_path: Path
) -> None:
    """`git worktree add` runs `post-checkout`: the repository's hooks must not run here."""
    marker = tmp_path / "hook-ejecutado"
    hook = git_repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    cli = GitCli(repo_dir=git_repo)
    dest = tmp_path.with_name(f"{tmp_path.name}-run") / "iva"

    cli.add_worktree(dest, "lictor/iva")

    assert not marker.exists()
    linked = GitCli(repo_dir=dest)
    assert linked.is_linked_worktree()
    assert linked.current_branch() == "lictor/iva"
    assert linked.get_head_sha() == cli.get_head_sha()
    assert cli.current_branch() == "main"
    assert (dest / "README.md").is_file()


def test_a_normal_checkout_is_not_a_linked_worktree(git_repo: Path) -> None:
    assert not GitCli(repo_dir=git_repo).is_linked_worktree()


def test_branch_exists_looks_only_at_local_branches(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    subprocess.run([GIT_BIN, "tag", "lictor/etiqueta"], cwd=git_repo, check=True)

    assert cli.branch_exists("main")
    assert not cli.branch_exists("lictor/etiqueta")
    assert not cli.branch_exists("lictor/nada")


def test_a_detached_head_has_no_branch(git_repo: Path) -> None:
    subprocess.run([GIT_BIN, "checkout", "-q", "--detach"], cwd=git_repo, check=True)

    assert GitCli(repo_dir=git_repo).current_branch() == ""


def test_add_worktree_refuses_an_existing_branch(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    subprocess.run([GIT_BIN, "branch", "lictor/iva"], cwd=git_repo, check=True)

    with pytest.raises(CandidateError, match="lictor/iva"):
        cli.add_worktree(tmp_path.with_name(f"{tmp_path.name}-run") / "iva", "lictor/iva")


def test_remove_worktree_refuses_what_git_would_lose(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    dest = tmp_path.with_name(f"{tmp_path.name}-run") / "iva"
    cli.add_worktree(dest, "lictor/iva")
    (dest / "notas.txt").write_text("a mano\n", encoding="utf-8")

    with pytest.raises(CandidateError, match="No se pudo retirar el worktree"):
        cli.remove_worktree(dest)
    assert dest.is_dir()

    (dest / "notas.txt").unlink()
    (dest / "x.ignored").write_text("ignorado\n", encoding="utf-8")
    cli.remove_worktree(dest)
    assert not dest.exists()


def test_delete_branch_keeps_one_that_isnt_integrated(git_repo: Path, tmp_path: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    dest = tmp_path.with_name(f"{tmp_path.name}-run") / "iva"
    cli.add_worktree(dest, "lictor/iva")
    subprocess.run([GIT_BIN, "commit", "--allow-empty", "-qm", "x"], cwd=dest, check=True)
    cli.remove_worktree(dest)

    with pytest.raises(CandidateError, match="lictor/iva"):
        cli.delete_branch("lictor/iva")
    assert cli.branch_exists("lictor/iva")

    subprocess.run([GIT_BIN, "merge", "-q", "--ff-only", "lictor/iva"], cwd=git_repo, check=True)
    cli.delete_branch("lictor/iva")
    assert not cli.branch_exists("lictor/iva")


def test_cited_in_names_the_committed_files_with_the_whole_word(git_repo: Path) -> None:
    """ADR-0017: where a broken public name is still cited, read from HEAD, not the disk."""
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "uso.md").write_text("Llama a save_chain(nif).\n", encoding="utf-8")
    (git_repo / "CHANGELOG.md").write_text("- save_chain guarda\n", encoding="utf-8")
    (git_repo / "otro.md").write_text("save_chains y presave_chain no cuentan\n", encoding="utf-8")
    subprocess.run([GIT_BIN, "add", "."], cwd=git_repo, check=True)
    subprocess.run([GIT_BIN, "commit", "-qm", "docs"], cwd=git_repo, check=True)
    (git_repo / "sin_confirmar.md").write_text("save_chain\n", encoding="utf-8")
    cli = GitCli(repo_dir=git_repo)

    assert cli.cited_in("save_chain") == ["CHANGELOG.md", "docs/uso.md"]
    assert cli.cited_in("-e") == []  # a word, never an option
    assert cli.cited_in("nadie_lo_cita") == []


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    subprocess.run([GIT_BIN, "add", name], cwd=repo, check=True)
    subprocess.run([GIT_BIN, "commit", "-q", "-m", name], cwd=repo, check=True)
    done = subprocess.run(
        [GIT_BIN, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def test_the_base_of_a_branch_is_where_it_left_the_other(git_repo: Path) -> None:
    """H4 of 0.20.0: `api.base` compares with where the work left the branch it goes to."""
    cli = GitCli(repo_dir=git_repo)
    fork = cli.get_head_sha()
    subprocess.run([GIT_BIN, "checkout", "-q", "-b", "trabajo"], cwd=git_repo, check=True)
    work = _commit(git_repo, "trabajo.txt")
    subprocess.run([GIT_BIN, "checkout", "-q", "main"], cwd=git_repo, check=True)
    _commit(git_repo, "main.txt")

    assert cli.resolve_commit("main") is not None
    assert cli.merge_base(work, "main") == fork
    assert cli.merge_base(fork, "main") == fork  # behind it: itself


def test_a_reference_that_names_nothing_resolves_to_none(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)

    assert cli.resolve_commit("origin/main") is None
    assert cli.resolve_commit("--all") is None  # never read as an option


def test_histories_with_no_common_ancestor_have_no_base(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    head = cli.get_head_sha()
    subprocess.run([GIT_BIN, "checkout", "-q", "--orphan", "otra"], cwd=git_repo, check=True)
    subprocess.run([GIT_BIN, "rm", "-rq", "--cached", "."], cwd=git_repo, check=True)
    other = _commit(git_repo, "otra.txt")

    assert cli.merge_base(head, other) is None


def _worktree(repo: Path, path: Path, *extra: str) -> Path:
    subprocess.run(
        [GIT_BIN, *extra, "worktree", "add", "-q", "-b", path.name, str(path), "HEAD"],
        cwd=repo,
        check=True,
    )
    return path


def test_a_worktree_both_ends_point_to_is_linked(git_repo: Path, tmp_path: Path) -> None:
    worktree = _worktree(git_repo, tmp_path / "wt")
    assert GitCli(git_repo).link_of(worktree) == "linked"


def test_a_worktree_through_a_link_is_still_linked(git_repo: Path, tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "enlace").symlink_to(tmp_path / "real", target_is_directory=True)
    worktree = _worktree(git_repo, tmp_path / "enlace" / "wt")
    assert GitCli(git_repo).link_of(tmp_path / "enlace" / "wt") == "linked"
    assert GitCli(git_repo).link_of(tmp_path / "real" / "wt") == "linked"
    assert worktree.is_dir()


def test_relative_paths_of_git_are_resolved_from_their_own_ends(
    git_repo: Path, tmp_path: Path
) -> None:
    worktree = _worktree(git_repo, tmp_path / "wt", "-c", "worktree.useRelativePaths=true")
    if not (worktree / ".git").read_text(encoding="utf-8").startswith("gitdir: ."):
        pytest.skip("este git no escribe rutas relativas en los worktrees")
    assert GitCli(git_repo).link_of(worktree) == "linked"


def test_a_worktree_without_its_git_file_is_unlinked_and_repairable(
    git_repo: Path, tmp_path: Path
) -> None:
    worktree = _worktree(git_repo, tmp_path / "wt")
    (worktree / ".git").unlink()
    assert GitCli(git_repo).link_of(worktree) == "unlinked"


def test_a_moved_checkout_leaves_its_worktrees_unlinked_not_orphaned(tmp_path: Path) -> None:
    """B1: the worktree's `.git` points to the old place, and `git worktree repair` fixes it."""
    repo = tmp_path / "proyecto"
    repo.mkdir()
    subprocess.run([GIT_BIN, "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        [
            GIT_BIN,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "i",
        ],
        cwd=repo,
        check=True,
    )
    worktree = _worktree(repo, tmp_path / "wt")
    moved = repo.rename(tmp_path / "movido")

    assert GitCli(moved).link_of(worktree) == "unlinked"


def test_a_worktree_git_removed_its_entry_of_is_orphaned(git_repo: Path, tmp_path: Path) -> None:
    worktree = _worktree(git_repo, tmp_path / "wt")
    shutil.rmtree(git_repo / ".git" / "worktrees" / "wt")
    assert GitCli(git_repo).link_of(worktree) == "orphan"
    assert GitCli(git_repo).link_of(tmp_path / "ninguno") == "orphan"


def test_removing_a_worktree_whose_directory_is_gone_drops_only_its_entry(
    git_repo: Path, tmp_path: Path
) -> None:
    """C2 and C5: `prune` would take a person's worktree on an unmounted disk as well."""
    gone = _worktree(git_repo, tmp_path / "wt")
    unmounted = _worktree(git_repo, tmp_path / "disco")
    shutil.rmtree(gone)
    unmounted.rename(tmp_path / "desmontado")

    GitCli(git_repo).remove_worktree(gone)

    assert sorted(p.name for p in (git_repo / ".git" / "worktrees").iterdir()) == ["disco"]


def test_a_stale_entry_beside_the_live_one_doesnt_unlink_a_sound_worktree(
    git_repo: Path, tmp_path: Path
) -> None:
    """P8 of the PR's review: the first entry naming the path decided, stale or not."""
    worktree = _worktree(git_repo, tmp_path / "wt")
    stale = git_repo / ".git" / "worktrees" / "aaa-viejo"
    stale.mkdir()
    (stale / "gitdir").write_text(f"{worktree / '.git'}\n", encoding="utf-8")

    assert GitCli(git_repo).link_of(worktree) == "linked"


def _set(repo: Path, key: str, value: str) -> None:
    subprocess.run([GIT_BIN, "config", key, value], cwd=repo, check=True)


def _get(repo: Path, key: str) -> str:
    return subprocess.run(
        [GIT_BIN, "config", "--get", key], cwd=repo, capture_output=True, text=True, check=False
    ).stdout.strip()


def test_an_editor_s_key_under_a_branch_is_neither_a_change_nor_undone(git_repo: Path) -> None:
    """S22: an editor writes `branch.<x>.vscode-merge-base` after a `git switch -c`, and the
    person sets up a branch of their own; none of it is Lictor's to guard."""
    cli = GitCli(repo_dir=git_repo)
    pin = cli.pin()
    print_before = cli.fingerprint()
    _set(git_repo, "branch.main.vscode-merge-base", "origin/main")
    _set(git_repo, "branch.feat.remote", "origin")

    assert cli.restore(pin) == []
    assert _get(git_repo, "branch.main.vscode-merge-base") == "origin/main"
    assert _get(git_repo, "branch.feat.remote") == "origin"
    assert cli.fingerprint() == print_before


def test_a_guarded_key_is_named_restored_and_changes_the_anchor(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    pin = cli.pin()
    print_before = cli.fingerprint()
    _set(git_repo, "core.hooksPath", "/srv/hooks")
    _set(git_repo, "branch.main.mergeOptions", "--no-ff")
    _set(git_repo, "branch.main.vscode-merge-base", "origin/main")
    print_after = cli.fingerprint()

    assert cli.restore(pin) == [".git/config (branch.main.mergeoptions, core.hookspath)"]
    assert _get(git_repo, "core.hooksPath") == ""
    assert print_after != print_before
    assert cli.fingerprint() == print_before


def test_a_config_git_can_t_read_counts_as_changed_whole(git_repo: Path) -> None:
    cli = GitCli(repo_dir=git_repo)
    pin = cli.pin()
    config = git_repo / ".git" / "config"
    config.write_text(config.read_text(encoding="utf-8") + "[roto\n", encoding="utf-8")

    assert cli.restore(pin) == [".git/config"]
    assert "[roto" not in config.read_text(encoding="utf-8")
