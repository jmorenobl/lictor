"""Unit tests for where a run keeps its evidence."""

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from lictor.application.run_files import (
    _scratch_root,
    attempt_prefix,
    new_step_dir,
    relative_to_project,
    save_output,
    scratch_dir,
    sweep_scratch,
    unused_path,
)
from lictor.domain.errors import EnvironmentUnavailableError, ExitCode
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
    target = tmp_path / "ajeno"
    target.mkdir(mode=0o700)
    (target / "plantado").write_text("x", encoding="utf-8")
    root = _scratch_root(project)
    root.symlink_to(target)

    with pytest.raises(EnvironmentUnavailableError) as err:
        use(project)

    assert str(root) in err.value.message
    assert "enlace simbólico" in err.value.message
    assert (target / "plantado").exists()
    assert root.is_symlink()


@pytest.mark.parametrize("use", SCRATCH_USERS)
def test_a_scratch_root_open_to_others_is_refused(
    project: Path, use: Callable[[Path], None]
) -> None:
    root = _scratch_root(project)
    root.mkdir()
    root.chmod(0o777)
    (root / "plantado").write_text("x", encoding="utf-8")

    with pytest.raises(EnvironmentUnavailableError) as err:
        use(project)

    assert str(root) in err.value.message
    assert "0o777" in err.value.message
    assert (root / "plantado").exists()


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
