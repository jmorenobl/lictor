"""Unit tests for where a run keeps its evidence."""

from pathlib import Path

from lictor.application.run_files import (
    attempt_prefix,
    new_step_dir,
    relative_to_project,
    save_output,
    unused_path,
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
