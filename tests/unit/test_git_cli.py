"""Unit tests for GitCli VCS adapter."""

import shutil
import subprocess
from pathlib import Path

import pytest

from lictor.adapters.vcs.git_cli import GitCli
from lictor.domain.errors import CandidateError, EnvironmentUnavailableError
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


def test_fingerprint_changes_with_the_control_files_and_runs_no_git(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = GitCli(repo_dir=git_repo)
    before = cli.fingerprint()
    assert cli.fingerprint() == before

    def no_git(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no debe ejecutarse git")

    monkeypatch.setattr(subprocess, "run", no_git)
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

    assert sorted(changed) == [".git/config", ".git/hooks/post-merge"]
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

    with pytest.raises(EnvironmentUnavailableError, match="empty ident name"):
        cli.committer_identity()
    with pytest.raises(subprocess.CalledProcessError):
        cli.commit_tree(cli.get_tree("HEAD"), "x", empty=True)
