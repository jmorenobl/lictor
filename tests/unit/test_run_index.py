"""Unit tests for where run worktrees live and the index of the main checkout."""

import tempfile
from pathlib import Path

import pytest

from lictor.application.run_index import (
    HOME_VARIABLE,
    RunEntry,
    add_to_run_index,
    lictor_home,
    read_run_index,
    remove_from_run_index,
    run_worktree,
)
from lictor.domain.errors import EnvironmentUnavailableError


def test_the_home_comes_from_the_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOME_VARIABLE, str(tmp_path / "casa"))

    assert lictor_home() == (tmp_path / "casa").resolve()


def test_without_the_variable_the_home_is_in_the_users_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(HOME_VARIABLE)
    monkeypatch.setenv("HOME", str(tmp_path / "usuario"))

    assert lictor_home() == (tmp_path / "usuario" / ".lictor").resolve()


def test_a_home_under_the_temporary_directory_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOME_VARIABLE, str(Path(tempfile.gettempdir()) / "lictor"))

    with pytest.raises(EnvironmentUnavailableError, match=HOME_VARIABLE) as raised:
        lictor_home()
    assert raised.value.exit_code == 40


def test_each_repository_and_branch_gets_its_own_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(HOME_VARIABLE, str(tmp_path / "casa"))
    one, other = tmp_path / "a" / "repo", tmp_path / "b" / "repo"

    path = run_worktree(one, "lictor/iva")

    assert path.parent.parent == (tmp_path / "casa" / "worktrees").resolve()
    assert path.name == "iva"
    assert path.parent.name.startswith("repo-")
    assert run_worktree(other, "lictor/iva") != path
    assert run_worktree(one, "lictor/otra").parent == path.parent


def test_the_index_keeps_the_runs_in_order_and_replaces_a_branch(tmp_path: Path) -> None:
    first, second = RunEntry("lictor/a", "/w/a", "R1"), RunEntry("lictor/b", "/w/b", "R2")
    add_to_run_index(tmp_path, first)
    add_to_run_index(tmp_path, second)
    add_to_run_index(tmp_path, RunEntry("lictor/a", "/w/a", "R3"))

    assert read_run_index(tmp_path) == [second, RunEntry("lictor/a", "/w/a", "R3")]
    assert not (tmp_path / ".lictor" / "worktrees.json.tmp").exists()


@pytest.mark.parametrize("content", [None, "", "{", '{"runs": [{"x": 1}]}', "[]"])
def test_a_missing_or_damaged_index_points_nowhere(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        (tmp_path / ".lictor").mkdir()
        (tmp_path / ".lictor" / "worktrees.json").write_text(content, encoding="utf-8")

    assert read_run_index(tmp_path) == []


def test_a_retired_run_leaves_the_index(tmp_path: Path) -> None:
    first, second = RunEntry("lictor/a", "/w/a", "R1"), RunEntry("lictor/b", "/w/b", "R2")
    add_to_run_index(tmp_path, first)
    add_to_run_index(tmp_path, second)

    remove_from_run_index(tmp_path, "lictor/a")
    remove_from_run_index(tmp_path, "lictor/c")

    assert read_run_index(tmp_path) == [second]
