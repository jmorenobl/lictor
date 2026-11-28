"""0.23.2, G8 and G7: `retire` finishes what a failed removal left, and never resumes it.

In the Go pass a module cache without write permission made `git worktree remove` fail halfway:
git deleted its own entry and part of the tree, and `retire` then read a worktree without its
`lictor.json` as a run that goes on, asking for a `lictor run` that would have spent. What a
failed removal leaves isn't fixed (git stops at the first failure of each directory, in `readdir`
order), so these cases build each shape by hand instead of relying on what git leaves.
"""

import json
import os
import shutil
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from typer.testing import CliRunner, Result

from lictor import cli
from lictor.application.run_index import mark_retiring, read_run_index
from lictor.cli import app
from lictor.orchestration.graph import get_sqlite_saver
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    git,
    require,
    run_from_checkout,
    run_worktree,
    write,
)

ROOT_IGNORES_PERMISSIONS = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root borra dentro de un directorio sin escritura: no prueba nada",
)


def _lictor(*args: str) -> Result:
    return CliRunner().invoke(app, list(args))


def _finished_run(make_project: ProjectFactory, name: str = "iva") -> tuple[Path, Path]:
    root = make_project()
    require(run_from_checkout(root, Agent(), branch=name) == Outcome(0), "el run debía acabar")
    return root, run_worktree(root, f"lictor/{name}")


def _git_forgets(root: Path, worktree: Path) -> None:
    """What `git worktree remove` does even when it fails: it deletes its own entry."""
    (entry,) = [
        e
        for e in (root / ".git" / "worktrees").iterdir()
        if (e / "gitdir").read_text(encoding="utf-8").strip() == str(worktree / ".git")
    ]
    shutil.rmtree(entry)


def _only_lictor_left(root: Path, worktree: Path) -> None:
    """The shape the pass left: git's entry gone, the tree gone but `.lictor/`."""
    _git_forgets(root, worktree)
    for path in worktree.iterdir():
        if path.name != ".lictor":
            shutil.rmtree(path) if path.is_dir() else path.unlink()


def _read_only_cache(worktree: Path, env: str = "verify-env") -> Path:
    module = worktree / ".lictor" / env / "gomod" / "example.com" / "dep@v1.0.0"
    module.mkdir(parents=True)
    (module / "dep.go").write_text("package dep\n", encoding="utf-8")
    module.chmod(0o555)
    module.parent.chmod(0o555)
    return module


def test_the_worktree_the_go_pass_left_is_retired_not_resumed(
    make_project: ProjectFactory,
) -> None:
    root, worktree = _finished_run(make_project)
    _only_lictor_left(root, worktree)

    result = _lictor("retire", "iva", "--delete-branch", "--cwd", str(root))

    assert result.exit_code == 10, result.output  # the branch isn't integrated: it stays
    assert "se puede reanudar" not in result.output
    assert not worktree.exists()
    assert read_run_index(root) == []
    assert (root / ".lictor" / "retired").is_dir(), "su evidencia se guarda"
    assert "lictor/iva" in git(root, "branch", "--list")


def test_a_run_that_cant_go_on_is_never_resumed(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root, worktree = _finished_run(make_project)
    _only_lictor_left(root, worktree)
    agent = Agent()

    assert run_from_checkout(root, agent, branch="iva") == Outcome(10)
    assert run_from_checkout(root, agent, branch="iva", new=True) == Outcome(10)

    assert agent.calls == []
    told = capsys.readouterr().err
    assert "git ya no conoce su worktree" in told
    assert "`lictor retire iva`" in told
    assert "ya existe la rama" not in told


def test_status_tells_a_broken_run_and_goes_on_with_the_rest(make_project: ProjectFactory) -> None:
    root, worktree = _finished_run(make_project)
    require(run_from_checkout(root, Agent(), branch="otra") == Outcome(0), "otro run")
    _only_lictor_left(root, worktree)

    result = _lictor("status", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert "Rama lictor/iva" in result.output
    assert "no se puede seguir: git ya no conoce su worktree" in result.output
    assert "Rama lictor/otra" in result.output
    report = _lictor("report", "--branch", "iva", "--cwd", str(root))
    assert report.exit_code == 10
    assert "lictor retire iva" in report.output


def test_a_marked_retire_finishes_even_with_the_projects_files_left(
    make_project: ProjectFactory,
) -> None:
    """B2: git stopped halfway, leaving its `.git` file, files of the branch and `.lictor/`."""
    root, worktree = _finished_run(make_project)
    mark_retiring(root, "lictor/iva")
    _git_forgets(root, worktree)

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert not worktree.exists()
    assert read_run_index(root) == []


def test_unmarked_files_of_the_project_are_left_to_the_person(
    make_project: ProjectFactory,
) -> None:
    root, worktree = _finished_run(make_project)
    _git_forgets(root, worktree)
    write(worktree, "notas.txt", "mías\n")

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 10, result.output
    assert "features" in result.output, "sin marca, ni lo de la rama es de Lictor"
    assert "Lictor no dejó este retiro a medias" in result.output
    assert "No se ha tocado nada" in result.output
    assert (worktree / "notas.txt").is_file()
    assert (worktree / ".lictor" / "checkpoints.sqlite").is_file()
    assert [run.branch for run in read_run_index(root)] == ["lictor/iva"]


def test_a_marked_retire_leaves_what_the_branch_doesnt_have(make_project: ProjectFactory) -> None:
    root, worktree = _finished_run(make_project)
    mark_retiring(root, "lictor/iva")
    _git_forgets(root, worktree)
    write(worktree, "notas.txt", "mías\n")

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 10, result.output
    assert "notas.txt" in result.output
    assert "No son de la rama" in result.output
    assert (worktree / "notas.txt").is_file()


@ROOT_IGNORES_PERMISSIONS
def test_a_read_only_cache_in_the_environment_doesnt_stop_retire(
    make_project: ProjectFactory,
) -> None:
    """G7: Go leaves its module cache read-only, and `git worktree remove` failed on it."""
    root, worktree = _finished_run(make_project)
    _read_only_cache(worktree)

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert not worktree.exists()
    assert str(worktree) not in git(root, "worktree", "list")
    assert read_run_index(root) == []


@ROOT_IGNORES_PERMISSIONS
def test_a_read_only_cache_in_doctors_kept_environment_doesnt_stop_retire(
    make_project: ProjectFactory,
) -> None:
    """0.24.0, G5: a `doctor` run inside the worktree keeps its environment there, Go's module
    cache included; `retire` removes it as it removes the run's, or G7 would come back."""
    root, worktree = _finished_run(make_project)
    _read_only_cache(worktree, "doctor-env/env")

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert not worktree.exists()


def test_a_run_that_never_started_is_retired(make_project: ProjectFactory) -> None:
    """Refusing it asked for a `lictor run`, which would have started it and spent."""
    root = make_project()
    target = cli._run_target(root, force_new=True, branch="iva")
    assert not isinstance(target, int)
    worktree = Path(target.worktree)

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert not worktree.exists()
    assert read_run_index(root) == []


def test_a_run_paused_under_another_id_is_not_retired(make_project: ProjectFactory) -> None:
    """C1: a resume goes on with what `current_run` names, not with the index's id."""
    root, worktree = _finished_run(make_project)
    lictor_dir = worktree / ".lictor"
    saver = get_sqlite_saver(lictor_dir / "checkpoints.sqlite")
    paused = empty_checkpoint()
    paused["channel_values"] = {"run_id": "otro", "current": "IVA-001"}
    config: RunnableConfig = {"configurable": {"thread_id": "otro", "checkpoint_ns": ""}}
    saver.put(config, paused, {}, {})
    saver.conn.close()
    (lictor_dir / "current_run").write_text("otro", encoding="utf-8")

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 10, result.output
    assert "lictor run --branch iva" in result.output
    assert worktree.is_dir()


def test_a_moved_checkout_asks_for_a_repair_and_touches_nothing(
    make_project: ProjectFactory,
) -> None:
    """B1: the worktree's `.git` points to the checkout's old place; `git worktree repair` fixes
    it."""
    root, worktree = _finished_run(make_project)
    moved = root.rename(root.with_name("movido"))
    before = sorted(p.name for p in (worktree / ".lictor").iterdir())

    result = _lictor("retire", "iva", "--cwd", str(moved))

    assert result.exit_code == 10, result.output
    assert f"git -C {moved} worktree repair {worktree}" in result.output.replace("\n", "")
    assert sorted(p.name for p in (worktree / ".lictor").iterdir()) == before
    git(moved, "worktree", "repair", str(worktree))
    assert _lictor("retire", "iva", "--cwd", str(moved)).exit_code == 0
    assert not worktree.exists()


def test_a_worktree_git_refuses_to_remove_stays_marked_and_retire_finishes_it_later(
    make_project: ProjectFactory,
) -> None:
    """V7: a locked worktree is refused by git for good, until the person unlocks it."""
    root, worktree = _finished_run(make_project)
    git(root, "worktree", "lock", str(worktree))

    refused = _lictor("retire", "iva", "--cwd", str(root))

    assert refused.exit_code == 40, refused.output
    assert "vuelve a lanzar `lictor retire iva`" in refused.output
    assert [run.retiring for run in read_run_index(root)] == [True]
    assert worktree.is_dir()
    git(root, "worktree", "unlock", str(worktree))
    assert _lictor("retire", "iva", "--cwd", str(root)).exit_code == 0
    assert not worktree.exists()


def test_status_json_tells_where_a_run_that_cant_go_on_stands(
    make_project: ProjectFactory,
) -> None:
    """P1 and P6 of the PR's review: a run retired halfway without its directory is listed too."""
    root, worktree = _finished_run(make_project)
    _only_lictor_left(root, worktree)

    broken = json.loads(_lictor("status", "--json", "--cwd", str(root)).stdout)["runs"]
    mark_retiring(root, "lictor/iva")
    shutil.rmtree(worktree)
    retiring = _lictor("status", "--cwd", str(root))

    assert [(run["branch"], run["standing"]) for run in broken] == [("lictor/iva", "broken")]
    assert "git ya no conoce su worktree" in broken[0]["reason"]
    assert retiring.exit_code == 0
    assert "Rama lictor/iva" in retiring.output
    assert "el retiro quedó a medias: termínalo con `lictor retire iva`" in retiring.output


def test_a_marked_retire_whose_branch_is_gone_says_so(make_project: ProjectFactory) -> None:
    """V3: without its branch Lictor can't know what was git's to remove."""
    root, worktree = _finished_run(make_project)
    mark_retiring(root, "lictor/iva")
    _git_forgets(root, worktree)
    git(root, "branch", "-D", "lictor/iva")

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 10, result.output
    assert "La rama ya no existe" in result.output
    assert "Lictor no dejó este retiro a medias" not in result.output
    assert worktree.is_dir()


def test_a_worktree_deleted_by_hand_leaves_git_nothing_holding_its_branch(
    make_project: ProjectFactory,
) -> None:
    """C5: git kept its entry, and `git branch -d` refused a branch «used by worktree»."""
    root, worktree = _finished_run(make_project)
    git(root, "merge", "-q", "--ff-only", "lictor/iva")
    shutil.rmtree(worktree)

    result = _lictor("retire", "iva", "--delete-branch", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert str(worktree) not in git(root, "worktree", "list")
    assert "lictor/iva" not in git(root, "branch", "--list")


def test_a_run_s_evidence_reaches_the_checkout_when_it_ends(make_project: ProjectFactory) -> None:
    """S21 (0.25.0): integrating the branch with `--delete-branch` took the worktree and its
    `.lictor/` with it, and `retire` then forgot the run with nothing kept and nothing said."""
    root, worktree = _finished_run(make_project)
    (run,) = read_run_index(root)
    kept = root / ".lictor" / "retired" / f"iva-{run.run_id}"

    assert (kept / "journal.jsonl").is_file()
    assert (kept / "report.md").is_file()
    shutil.rmtree(worktree)
    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert f"su evidencia está en .lictor/retired/iva-{run.run_id}" in result.output
    assert read_run_index(root) == []


def test_retiring_a_run_whose_worktree_and_evidence_are_gone_says_it_was_lost(
    make_project: ProjectFactory,
) -> None:
    root, worktree = _finished_run(make_project)
    shutil.rmtree(root / ".lictor" / "retired")
    shutil.rmtree(worktree)

    result = _lictor("retire", "iva", "--cwd", str(root))

    assert result.exit_code == 0, result.output
    assert "⚠ El worktree de lictor/iva ya no existía" in result.output
    assert "su evidencia se ha perdido" in result.output
