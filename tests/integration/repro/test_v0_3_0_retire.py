"""0.3.0, phase 3 (ADR-0005, points 7, 10 and 12): retiring a worktree, and the other commands.

The result is a branch the person integrates; afterwards `lictor retire` takes the worktree away,
never a branch without asking, and never a worktree with something still to lose.
"""

import json
from pathlib import Path

from typer.testing import CliRunner, Result

from lictor.application.run_index import read_run_index
from lictor.cli import app
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    green_ids,
    require,
    run_from_checkout,
    run_worktree,
    write,
)
from tests.integration.repro.test_phase4_recovery import (
    ALL_GREEN,
    RESUME_TIMEOUT_S,
    _command,
    _environment,
    _stop_at,
)
from tests.integration.repro.test_v0_3_0_own_worktree import _killing_first_calls


def _lictor(*args: str) -> Result:
    return CliRunner().invoke(app, list(args))


def _finished_run(make_project: ProjectFactory, name: str = "iva") -> tuple[Path, Path]:
    root = make_project()
    require(run_from_checkout(root, Agent(), branch=name) == Outcome(0), "el run debía acabar")
    return root, run_worktree(root, f"lictor/{name}")


def test_retire_removes_the_worktree_of_an_integrated_run(make_project: ProjectFactory) -> None:
    root, worktree = _finished_run(make_project)
    git(root, "merge", "-q", "--ff-only", "lictor/iva")

    result = _lictor("retire", "iva", "--delete-branch", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert not worktree.exists()
    assert str(worktree) not in git(root, "worktree", "list")
    assert "lictor/iva" not in git(root, "branch", "--list")
    assert read_run_index(root) == []
    assert green_ids(root) == ALL_GREEN, "lo integrado se queda"


def test_retire_never_deletes_the_branch_without_being_asked(
    make_project: ProjectFactory,
) -> None:
    root, worktree = _finished_run(make_project)

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert not worktree.exists()
    assert "lictor/iva" in git(root, "branch", "--list")
    assert "git branch -d lictor/iva" in result.output


def test_retire_keeps_a_branch_that_isnt_integrated_even_when_asked(
    make_project: ProjectFactory,
) -> None:
    root, worktree = _finished_run(make_project)

    result = _lictor("retire", "iva", "--delete-branch", "--cwd", str(root))

    assert result.exit_code == 10, result.output
    assert not worktree.exists()
    assert "lictor/iva" in git(root, "branch", "--list"), "git branch -d no borra lo no integrado"
    assert green_ids(root) == [], "lo no integrado no llega a la rama de la persona"


def test_retire_refuses_a_worktree_with_uncommitted_changes(make_project: ProjectFactory) -> None:
    root, worktree = _finished_run(make_project)
    write(worktree, "notas.txt", "a mano\n")

    result = _lictor("retire", "iva", "--delete-branch", "--cwd", str(root))

    assert result.exit_code == 10, result.output
    assert "notas.txt" in result.output
    assert (worktree / "notas.txt").is_file()
    assert "lictor/iva" in git(root, "branch", "--list")
    assert [run.branch for run in read_run_index(root)] == ["lictor/iva"]


def test_retire_refuses_a_run_that_can_still_go_on(make_project: ProjectFactory) -> None:
    root = make_project()
    require(
        run_from_checkout(root, _killing_first_calls(1), branch="iva").killed,
        "el run debía quedar a medias",
    )
    worktree = run_worktree(root, "lictor/iva")

    result = _lictor("retire", "iva", "--delete-branch", "--cwd", str(root))

    assert result.exit_code == 10, result.output
    assert "lictor run --branch iva" in result.output
    assert (worktree / ".lictor" / "checkpoints.sqlite").is_file()


def test_retire_names_the_runs_it_knows_when_the_name_is_wrong(
    make_project: ProjectFactory,
) -> None:
    root, worktree = _finished_run(make_project)

    result = _lictor("retire", "otra", "--cwd", str(root))

    assert result.exit_code == 10, result.output
    assert "lictor/iva" in result.output
    assert worktree.is_dir()


def test_retire_is_for_the_persons_checkout(make_project: ProjectFactory) -> None:
    _, worktree = _finished_run(make_project)

    result = _lictor("retire", "iva", "--cwd", str(worktree))

    assert result.exit_code == 10, result.output
    assert worktree.is_dir()


def test_status_and_report_take_a_run_by_its_branch(make_project: ProjectFactory) -> None:
    root, worktree = _finished_run(make_project, "a")
    require(run_from_checkout(root, Agent(), branch="b") == Outcome(0), "el segundo debía acabar")

    status = _lictor("status", "--branch", "a", "--cwd", str(root))
    as_json = _lictor("status", "--branch", "a", "--json", "--cwd", str(root))
    report = _lictor("report", "--branch", "a", "--cwd", str(root))
    unknown = _lictor("status", "--branch", "c", "--cwd", str(root))

    assert status.exit_code == 0, status.output
    assert "Rama lictor/a" in status.output
    assert "lictor/b" not in status.output
    assert json.loads(as_json.output)["runs"][0]["branch"] == "lictor/a"
    assert report.exit_code == 0, report.output
    assert str(worktree / ".lictor" / "report.md") in report.output
    assert unknown.exit_code == 10
    assert "lictor/a" in unknown.output


def test_register_in_the_checkout_doesnt_step_on_a_run_in_its_worktree(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """`register` moves the person's branch; the run anchored its own, in its worktree."""
    root = make_project()
    point = "implement:durante"
    marker, release = tmp_path / "alcanzado", tmp_path / "suelta"
    command = _command(root, tmp_path, "verde", point, str(marker), str(release))
    run = _stop_at(command, point, marker, _environment(branch="iva"))
    try:
        registered = _lictor("register", "IVA-003", "--yes", "--cwd", str(root))
    finally:
        release.touch()
    output, _ = run.communicate(timeout=RESUME_TIMEOUT_S)

    assert registered.exit_code == 0, registered.output
    assert "Lictor-Asserted-By" in git(root, "log", "-1", "--format=%B")
    assert run.returncode == 0, output.decode()
    worktree = run_worktree(root, "lictor/iva")
    assert green_ids(worktree) == ALL_GREEN
    assert false_greens(worktree) == []
    assert not events_of(worktree, "state.tampered")
