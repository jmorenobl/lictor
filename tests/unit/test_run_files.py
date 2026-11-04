"""Unit tests for where a run keeps its evidence."""

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from lictor.application.run_files import (
    _scratch_root,
    attempt_prefix,
    exclusive_run,
    holding_project,
    new_step_dir,
    prune_runs,
    relative_to_project,
    save_output,
    scratch_dir,
    sweep_scratch,
    unused_path,
)
from lictor.application.usecases.verify_attempt import IntegritySnapshot
from lictor.domain.errors import EnvironmentUnavailableError, ExitCode, RunInProgressError
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
    save_output(path, CommandOutput(returncode=7, stdout="fuera", stderr="dentro"))
    text = path.read_text(encoding="utf-8")
    assert "Código de salida: 7" in text
    assert "fuera" in text
    assert "dentro" in text


def test_save_output_survives_undecodable_text(tmp_path: Path) -> None:
    path = tmp_path / "control.log"
    save_output(path, CommandOutput(returncode=0, stdout="\udcff", stderr=""))
    assert path.is_file()


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
