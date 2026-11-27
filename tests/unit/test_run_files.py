"""Unit tests for where a run keeps its evidence."""

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from lictor.application.run_files import (
    _scratch_root,
    archive_run_evidence,
    attempt_prefix,
    exclusive_run,
    holding_project,
    lock_held,
    new_step_dir,
    own_dir,
    prune_runs,
    relative_to_project,
    remove_tree,
    save_output,
    scratch_dir,
    sweep_scratch,
    tree_entries_on_disk,
    unused_path,
)
from lictor.application.usecases.verify_attempt import IntegritySnapshot
from lictor.domain.errors import (
    CleanupError,
    EnvironmentUnavailableError,
    ExitCode,
    RunInProgressError,
)
from lictor.domain.model import CommandOutput


def test_attempt_prefix_groups_by_owner(tmp_path: Path) -> None:
    prefix = attempt_prefix(tmp_path, "R", "IVA-001", 2)
    assert prefix == tmp_path / ".lictor" / "runs" / "R" / "IVA-001" / "002"


def test_unused_path_is_the_path_itself_when_free(tmp_path: Path) -> None:
    assert unused_path(tmp_path / "a.json") == tmp_path / "a.json"


def test_unused_path_skips_taken_names(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("x", encoding="utf-8")
    (tmp_path / "a-r1.json").write_text("x", encoding="utf-8")
    assert unused_path(tmp_path / "a.json") == tmp_path / "a-r2.json"


def test_unused_path_starts_at_the_given_retry(tmp_path: Path) -> None:
    assert unused_path(tmp_path / "a.json", start=2) == tmp_path / "a-r2.json"


def test_new_step_dir_never_reuses_a_directory(tmp_path: Path) -> None:
    first = new_step_dir(tmp_path / "x" / "001-verify")
    second = new_step_dir(tmp_path / "x" / "001-verify")
    assert first.is_dir()
    assert second.is_dir()
    assert first != second


def test_save_output_keeps_exit_code_and_both_streams(tmp_path: Path) -> None:
    path = tmp_path / "control.log"
    save_output(path, ["x"], CommandOutput(returncode=7, stdout="fuera", stderr="dentro"))
    text = path.read_text(encoding="utf-8")
    assert "Código de salida: 7" in text
    assert "fuera" in text
    assert "dentro" in text


def test_save_output_survives_undecodable_text(tmp_path: Path) -> None:
    path = tmp_path / "control.log"
    save_output(path, ["x"], CommandOutput(returncode=0, stdout="\udcff", stderr=""))
    assert path.is_file()


def test_save_output_starts_with_the_command_that_ran(tmp_path: Path) -> None:
    """ADR-0009: the evidence says what ran, quoted as a shell would need it."""
    path = tmp_path / "control.log"
    save_output(path, ["pytest", "tests/unit", "-k", "a b"], CommandOutput(0, "", ""))
    first, second = path.read_text(encoding="utf-8").splitlines()[:2]
    assert first == "Comando: pytest tests/unit -k 'a b'"
    assert second == "Código de salida: 0"


def test_relative_to_project_uses_posix_paths(tmp_path: Path) -> None:
    assert relative_to_project(tmp_path / "a" / "b", tmp_path) == "a/b"


def test_relative_to_project_keeps_outside_paths(tmp_path: Path) -> None:
    assert relative_to_project(Path("/otro/sitio"), tmp_path) == "/otro/sitio"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project whose temporary directories land in a directory of the test's own."""
    shared = tmp_path / "tmp"
    shared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(shared))
    root = tmp_path / "proyecto"
    root.mkdir()
    return root


def _use_scratch(project: Path) -> None:
    with scratch_dir(project, "x-"):
        pass


SCRATCH_USERS: list[Callable[[Path], None]] = [_use_scratch, sweep_scratch]


def test_scratch_root_is_created_private(project: Path) -> None:
    with scratch_dir(project, "x-") as tmp:
        root = _scratch_root(project)
        info = os.lstat(root)
        assert tmp.parent == root
        assert info.st_mode & 0o777 == 0o700
        assert info.st_uid == os.getuid()

    sweep_scratch(project)

    assert not root.exists()


def test_sweep_without_a_scratch_root_does_nothing(project: Path) -> None:
    sweep_scratch(project)

    assert not _scratch_root(project).exists()


@pytest.mark.parametrize("use", SCRATCH_USERS)
def test_a_scratch_root_of_another_user_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch, use: Callable[[Path], None]
) -> None:
    root = _scratch_root(project)
    root.mkdir(mode=0o700)
    (root / "plantado").write_text("x", encoding="utf-8")
    monkeypatch.setattr(os, "getuid", lambda: os.geteuid() + 1)

    with pytest.raises(EnvironmentUnavailableError) as err:
        use(project)

    assert err.value.exit_code == ExitCode.ENVIRONMENT_UNAVAILABLE
    assert str(root) in err.value.message
    assert "otro usuario" in err.value.message
    assert "TMPDIR" in err.value.message
    assert (root / "plantado").exists()


@pytest.mark.parametrize("use", SCRATCH_USERS)
def test_a_scratch_root_that_is_a_symlink_is_refused(
    project: Path, tmp_path: Path, use: Callable[[Path], None]
) -> None:
    """The target would pass as left by an older version: it must not be made private through it."""
    target = tmp_path / "ajeno"
    target.mkdir()
    target.chmod(0o755)
    (target / "plantado").write_text("x", encoding="utf-8")
    root = _scratch_root(project)
    root.symlink_to(target)

    with pytest.raises(EnvironmentUnavailableError) as err:
        use(project)

    assert str(root) in err.value.message
    assert "enlace simbólico" in err.value.message
    assert (target / "plantado").exists()
    assert root.is_symlink()
    assert os.lstat(target).st_mode & 0o777 == 0o755


@pytest.mark.parametrize("mode", [0o777, 0o775, 0o757], ids=oct)
@pytest.mark.parametrize("use", SCRATCH_USERS)
def test_a_scratch_root_others_can_write_into_is_refused(
    project: Path, use: Callable[[Path], None], mode: int
) -> None:
    """Someone else may already have planted something in it: making it private now is too late."""
    root = _scratch_root(project)
    root.mkdir()
    root.chmod(mode)
    (root / "plantado").write_text("x", encoding="utf-8")

    with pytest.raises(EnvironmentUnavailableError) as err:
        use(project)

    assert str(root) in err.value.message
    assert oct(mode) in err.value.message
    assert "escritura" in err.value.message
    assert (root / "plantado").exists()
    assert os.lstat(root).st_mode & 0o777 == mode


@pytest.mark.parametrize("use", SCRATCH_USERS)
def test_a_scratch_root_that_cannot_be_opened_is_refused(
    project: Path, use: Callable[[Path], None]
) -> None:
    root = _scratch_root(project)
    root.mkdir()
    root.chmod(0o000)
    try:
        with pytest.raises(EnvironmentUnavailableError) as err:
            use(project)
    finally:
        root.chmod(0o700)

    assert err.value.exit_code == ExitCode.ENVIRONMENT_UNAVAILABLE
    assert "no se puede abrir" in err.value.message
    # Nobody else is involved: the remedy is the user's own permissions, not someone else's.
    assert "otro usuario" not in err.value.message
    assert "chmod 700" in err.value.message


OLDER_MODES = [0o755, 0o750, 0o711]


@pytest.mark.parametrize("mode", OLDER_MODES, ids=oct)
def test_a_scratch_root_left_by_an_older_version_is_made_private(project: Path, mode: int) -> None:
    """An older version created it `0o755`: nobody else could write into it, so it can be kept."""
    root = _scratch_root(project)
    root.mkdir()
    root.chmod(mode)
    (root / "anterior").write_text("x", encoding="utf-8")

    with scratch_dir(project, "x-") as tmp:
        assert tmp.parent == root
        assert os.lstat(root).st_mode & 0o777 == 0o700

    assert (root / "anterior").exists()


@pytest.mark.parametrize("mode", OLDER_MODES, ids=oct)
def test_a_scratch_root_left_by_an_older_version_is_swept(project: Path, mode: int) -> None:
    root = _scratch_root(project)
    root.mkdir()
    root.chmod(mode)
    (root / "anterior").write_text("x", encoding="utf-8")

    sweep_scratch(project)

    assert not os.path.lexists(root)


@pytest.mark.parametrize("use", SCRATCH_USERS)
def test_a_scratch_root_that_is_not_a_directory_is_refused(
    project: Path, use: Callable[[Path], None]
) -> None:
    root = _scratch_root(project)
    root.write_text("x", encoding="utf-8")

    with pytest.raises(EnvironmentUnavailableError) as err:
        use(project)

    assert "no es un directorio" in err.value.message
    assert root.is_file()


def test_a_second_run_lock_is_refused_while_the_first_is_held(tmp_path: Path) -> None:
    with (
        exclusive_run(tmp_path, "lictor run"),
        pytest.raises(RunInProgressError) as err,
        exclusive_run(tmp_path, "lictor init"),
    ):
        pass

    assert err.value.exit_code == ExitCode.PRECONDITION
    assert ".lictor/run.lock" in err.value.message
    assert "`lictor init`" in err.value.message


def test_the_run_lock_is_released_afterwards(tmp_path: Path) -> None:
    with exclusive_run(tmp_path, "lictor run"):
        pass

    with exclusive_run(tmp_path, "lictor run"):
        assert (tmp_path / ".lictor" / "run.lock").is_file()


def test_the_run_lock_is_released_when_the_run_fails(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentUnavailableError), exclusive_run(tmp_path, "lictor run"):
        raise EnvironmentUnavailableError("fallo")

    with exclusive_run(tmp_path, "lictor run"):
        pass


def test_the_run_lock_is_not_state_the_integrity_snapshot_guards(tmp_path: Path) -> None:
    lictor_dir = tmp_path / ".lictor"
    lictor_dir.mkdir()
    snapshot = IntegritySnapshot(lictor_dir, tmp_path)

    with exclusive_run(tmp_path, "lictor run"):
        pass

    assert snapshot.verify_and_restore()


def test_holding_the_project_says_whether_it_was_free(tmp_path: Path) -> None:
    with holding_project(tmp_path) as first, holding_project(tmp_path) as second:
        assert (first, second) == (True, False)
    with holding_project(tmp_path) as again:
        assert again is True


RUN_IDS = [f"2026110{d}-120000-abc{d}" for d in range(1, 5)]


def test_old_runs_are_recorded_then_removed_whole(tmp_path: Path) -> None:
    runs = tmp_path / ".lictor" / "runs"
    for run_id in RUN_IDS:
        (runs / run_id / "IVA-001").mkdir(parents=True)
    (runs / "check").mkdir()
    seen: list[list[str]] = []

    def record(pruned: list[str]) -> None:
        seen.append(pruned)
        assert all((runs / run_id).is_dir() for run_id in pruned)  # still there

    pruned = prune_runs(tmp_path, 1, {RUN_IDS[0]}, record)

    assert pruned == RUN_IDS[1:3]
    assert seen == [RUN_IDS[1:3]]
    assert sorted(p.name for p in runs.iterdir()) == sorted([RUN_IDS[0], RUN_IDS[3], "check"])


def test_nothing_to_prune_records_nothing(tmp_path: Path) -> None:
    seen: list[list[str]] = []
    assert prune_runs(tmp_path, 1, (), seen.append) == []
    (tmp_path / ".lictor" / "runs" / RUN_IDS[0]).mkdir(parents=True)
    assert prune_runs(tmp_path, 1, (), seen.append) == []
    assert seen == []


def test_a_link_shaped_like_a_run_is_left_alone(tmp_path: Path) -> None:
    runs = tmp_path / ".lictor" / "runs"
    target = tmp_path / "fuera"
    (target / "dato").mkdir(parents=True)
    runs.mkdir(parents=True)
    (runs / RUN_IDS[0]).symlink_to(target)
    (runs / RUN_IDS[1]).mkdir()

    assert prune_runs(tmp_path, 0, (), lambda _pruned: None) == [RUN_IDS[1]]
    assert (target / "dato").is_dir()


def test_lictors_directory_is_ignored_by_git_whatever_the_project_ignores(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    with exclusive_run(tmp_path, "lictor run"):
        (tmp_path / ".lictor" / "checkpoints.sqlite").write_text("x", encoding="utf-8")
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )

    assert status.stdout == ""


def test_lictors_own_gitignore_is_left_as_it_is_once_written(tmp_path: Path) -> None:
    own_dir(tmp_path)
    (tmp_path / ".lictor" / ".gitignore").write_text("*\n!algo\n", encoding="utf-8")

    own_dir(tmp_path)

    assert (tmp_path / ".lictor" / ".gitignore").read_text(encoding="utf-8") == "*\n!algo\n"


def _worktree_with_evidence(root: Path, run_id: str) -> Path:
    lictor = root / ".lictor"
    (lictor / "runs" / run_id).mkdir(parents=True)
    (lictor / "runs" / run_id / "prueba.txt").write_text("e", encoding="utf-8")
    (lictor / "journal.jsonl").write_text("{}\n", encoding="utf-8")
    (lictor / "checkpoints.sqlite").write_text("c", encoding="utf-8")
    return root


def test_archiving_keeps_journal_report_and_runs_but_not_the_resume_state(tmp_path: Path) -> None:
    worktree = _worktree_with_evidence(tmp_path / "wt", "20261125-120000-abcd")

    kept = archive_run_evidence(worktree, tmp_path / "main", "uno-20261125-120000-abcd", 10)

    assert kept == tmp_path / "main" / ".lictor" / "retired" / "uno-20261125-120000-abcd"
    assert sorted(p.name for p in kept.iterdir()) == ["journal.jsonl", "runs"]
    assert (kept / "runs" / "20261125-120000-abcd" / "prueba.txt").read_text("utf-8") == "e"
    assert not list(kept.parent.glob(".*.partial"))


def test_archiving_a_worktree_without_evidence_keeps_nothing(tmp_path: Path) -> None:
    (tmp_path / "wt" / ".lictor").mkdir(parents=True)

    assert archive_run_evidence(tmp_path / "wt", tmp_path / "main", "uno-x", 10) is None
    assert not (tmp_path / "main" / ".lictor" / "retired").exists()


def test_archiving_keeps_only_the_most_recent_retired_runs(tmp_path: Path) -> None:
    ids = [f"2026112{d}-120000-abcd" for d in range(1, 5)]
    for rid in ids:
        worktree = _worktree_with_evidence(tmp_path / rid, rid)
        archive_run_evidence(worktree, tmp_path / "main", f"uno-{rid}", 2)

    retired = tmp_path / "main" / ".lictor" / "retired"
    assert sorted(p.name for p in retired.iterdir() if p.is_dir()) == [
        f"uno-{ids[2]}",
        f"uno-{ids[3]}",
    ]


ROOT_IGNORES_PERMISSIONS = pytest.mark.skipif(
    os.geteuid() == 0, reason="root borra dentro de un directorio sin escritura: no prueba nada"
)


def _read_only_tree(root: Path) -> Path:
    """What Go leaves as its module cache: directories without write permission, one unreadable."""
    module = root / "gomod" / "example.com" / "dep@v1.0.0"
    module.mkdir(parents=True)
    (module / "dep.go").write_text("package dep\n", encoding="utf-8")
    hidden = root / "oculto"
    hidden.mkdir()
    (hidden / "f").write_text("x", encoding="utf-8")
    for directory in (module, module.parent, hidden):
        directory.chmod(0o555)
    hidden.chmod(0o000)
    return module


@ROOT_IGNORES_PERMISSIONS
def test_remove_tree_removes_what_a_tool_left_read_only(tmp_path: Path) -> None:
    """G7 of 0.23.2: without write permission `rmtree` couldn't empty Go's module cache."""
    env = tmp_path / "verify-env"
    _read_only_tree(env)

    remove_tree(env)

    assert not env.exists()


@ROOT_IGNORES_PERMISSIONS
def test_remove_tree_never_touches_what_is_outside(tmp_path: Path) -> None:
    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "guardado").write_text("x", encoding="utf-8")
    outside.chmod(0o555)
    env = tmp_path / "padre" / "verify-env"
    _read_only_tree(env)
    (env / "enlace").symlink_to(outside, target_is_directory=True)
    env.parent.chmod(0o755)

    remove_tree(env)

    assert not env.exists()
    assert (outside / "guardado").is_file()
    assert oct(outside.stat().st_mode & 0o777) == "0o555", "fuera no se cambia ningún permiso"
    outside.chmod(0o755)


@ROOT_IGNORES_PERMISSIONS
def test_remove_tree_never_gives_its_parent_write_permission(tmp_path: Path) -> None:
    parent = tmp_path / "padre"
    (parent / "verify-env").mkdir(parents=True)
    parent.chmod(0o555)

    with pytest.raises(CleanupError, match="no se ha podido borrar"):
        remove_tree(parent / "verify-env")

    assert oct(parent.stat().st_mode & 0o777) == "0o555"
    parent.chmod(0o755)


def test_remove_tree_of_nothing_does_nothing(tmp_path: Path) -> None:
    remove_tree(tmp_path / "no-existe")


def test_remove_tree_says_what_isnt_a_permission_it_cant_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "env"
    (env / "a").mkdir(parents=True)
    (env / "a" / "f").write_text("x", encoding="utf-8")

    def busy(path: str, *, dir_fd: int | None = None) -> None:
        raise OSError(16, "Device or resource busy", path)

    monkeypatch.setattr(os, "unlink", busy)
    with pytest.raises(CleanupError, match="Device or resource busy"):
        remove_tree(env)


@ROOT_IGNORES_PERMISSIONS
def test_sweep_removes_a_read_only_cache_a_killed_doctor_left(project: Path) -> None:
    """A `doctor` of Go killed left the module cache in the scratch root for good."""
    with scratch_dir(project, "env-"):
        pass
    _read_only_tree(_scratch_root(project) / "env-muerto")

    sweep_scratch(project)

    assert not _scratch_root(project).exists()


def test_sweep_leaves_what_it_cant_remove_without_failing(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with scratch_dir(project, "env-"):
        pass
    (_scratch_root(project) / "restos").write_text("x", encoding="utf-8")

    def busy(path: str, *, dir_fd: int | None = None) -> None:
        raise OSError(16, "Device or resource busy", path)

    monkeypatch.setattr(os, "unlink", busy)
    sweep_scratch(project)

    assert (_scratch_root(project) / "restos").exists()


def test_a_lock_nobody_holds_is_free_and_finding_out_creates_nothing(tmp_path: Path) -> None:
    assert lock_held(tmp_path) is False
    assert not (tmp_path / ".lictor").exists()
    with holding_project(tmp_path):
        pass
    assert lock_held(tmp_path) is False


def test_a_lock_another_process_holds_is_held(tmp_path: Path) -> None:
    own_dir(tmp_path)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, os, sys; fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT); "
            "fcntl.flock(fd, fcntl.LOCK_EX); print('ok', flush=True); sys.stdin.read()",
            str(tmp_path / ".lictor" / "run.lock"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ok"
        assert lock_held(tmp_path) is True
    finally:
        holder.communicate("")
    assert lock_held(tmp_path) is False


def test_an_archive_already_kept_is_not_replaced(tmp_path: Path) -> None:
    """C3: a retire that stopped halfway kept the whole evidence; what is left may be less."""
    worktree, main = tmp_path / "wt", tmp_path / "main"
    (worktree / ".lictor" / "runs").mkdir(parents=True)
    (worktree / ".lictor" / "journal.jsonl").write_text("completa\n", encoding="utf-8")
    first = archive_run_evidence(worktree, main, "iva-r1", 10)
    (worktree / ".lictor" / "journal.jsonl").write_text("menos\n", encoding="utf-8")

    second = archive_run_evidence(worktree, main, "iva-r1", 10)

    assert first == second
    assert second is not None
    assert (second / "journal.jsonl").read_text(encoding="utf-8") == "completa\n"


def test_the_tree_on_disk_lists_links_without_following_them(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "f").write_text("x", encoding="utf-8")
    (tmp_path / "enlace").symlink_to(tmp_path / "a", target_is_directory=True)

    assert sorted(tree_entries_on_disk(tmp_path)) == [
        ("a", "dir"),
        ("a/b", "dir"),
        ("a/b/f", "file"),
        ("enlace", "link"),
    ]
