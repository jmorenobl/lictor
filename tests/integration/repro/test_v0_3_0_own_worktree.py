"""0.3.0, phase 2 (ADR-0005, points 1, 3, 4, 5 and 11): Lictor creates the run's worktree.

`lictor run` from a person's checkout works in a worktree and a branch of its own, created from
HEAD; the person keeps working in their checkout, commits included.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lictor.application.run_index import read_run_index
from lictor.cli import app
from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_impl,
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


def _killing_first_calls(count: int) -> Agent:
    """An agent that dies on its first `count` calls and implements well afterwards."""
    left = [count]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        if left[0]:
            left[0] -= 1
            raise KeyboardInterrupt
        good_impl(task, scenario_id, attempt)

    return Agent(impl=impl)


def test_a_run_from_the_checkout_works_in_its_own_worktree_and_branch(
    make_project: ProjectFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project()
    marker = tmp_path / "post-checkout-ejecutado"
    write(root / ".git", "hooks/post-checkout", f"#!/bin/sh\ntouch {marker}\n")
    (root / ".git" / "hooks" / "post-checkout").chmod(0o755)
    head = git(root, "rev-parse", "HEAD")

    assert run_from_checkout(root, Agent(), branch="iva") == Outcome(0)

    worktree = run_worktree(root, "lictor/iva")
    assert not marker.exists(), "crear el worktree no ejecuta los hooks del repositorio"
    assert not worktree.is_relative_to(root)
    assert not worktree.is_relative_to(Path(tempfile.gettempdir()))
    assert git(worktree, "branch", "--show-current").strip() == "lictor/iva"
    assert green_ids(worktree) == ALL_GREEN
    assert false_greens(worktree) == []
    assert (worktree / ".lictor" / "report.md").is_file()
    assert git(root, "rev-parse", "HEAD") == head, "la rama de la persona no se mueve"
    assert git(root, "branch", "--show-current").strip() == "master"
    assert not git(root, "status", "--short")
    assert not (root / ".lictor" / "journal.jsonl").exists(), "el estado vive en el worktree"
    reason = events_of(worktree, "run.finished")[-1]["data"]["reason"]
    assert "rama lictor/iva" in reason
    assert "git merge lictor/iva" in reason
    assert "lictor/iva" in capsys.readouterr().err

    git(root, "merge", "-q", "--ff-only", "lictor/iva")
    assert green_ids(root) == ALL_GREEN


def test_without_a_name_the_branch_is_named_after_the_run(make_project: ProjectFactory) -> None:
    root = make_project()

    assert run_from_checkout(root, Agent()) == Outcome(0)

    [run] = read_run_index(root)
    assert run.branch == f"lictor/{run.run_id}"
    assert events_of(Path(run.worktree), "run.started")[0]["runid"] == run.run_id


def test_what_isnt_committed_stays_out_and_the_person_is_told(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project()
    write(root, "README.md", "editado a mano\n")
    write(root, "notas.txt", "sin añadir\n")

    assert run_from_checkout(root, Agent(), branch="iva") == Outcome(0)

    err = capsys.readouterr().err
    assert "README.md" in err
    assert "notas.txt" in err
    worktree = run_worktree(root, "lictor/iva")
    assert green_ids(worktree) == ALL_GREEN
    assert "notas.txt" not in git(worktree, "ls-tree", "-r", "--name-only", "HEAD")
    assert "editado a mano" not in git(worktree, "show", "HEAD:README.md")
    assert (root / "README.md").read_text(encoding="utf-8") == "editado a mano\n"
    assert (root / "notas.txt").is_file()


def test_an_existing_branch_is_refused_without_touching_anything(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    git(root, "branch", "lictor/iva")
    agent = Agent()

    assert run_from_checkout(root, agent, branch="iva") == Outcome(10)

    assert agent.calls == []
    assert read_run_index(root) == []


def test_a_name_that_cant_be_a_branch_is_refused(make_project: ProjectFactory) -> None:
    root = make_project()

    assert run_from_checkout(root, Agent(), branch="a/../b") == Outcome(10)

    assert read_run_index(root) == []


def test_a_killed_run_is_resumed_from_the_checkout(make_project: ProjectFactory) -> None:
    root = make_project()
    agent = _killing_first_calls(1)
    require(run_from_checkout(root, agent, branch="iva").killed, "el agente debía matarla")

    assert run_from_checkout(root, agent) == Outcome(0)

    assert [run.branch for run in read_run_index(root)] == ["lictor/iva"]
    worktree = run_worktree(root, "lictor/iva")
    assert green_ids(worktree) == ALL_GREEN
    assert events_of(worktree, "run.resumed")


def test_with_several_runs_to_resume_the_branch_has_to_be_named(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project()
    agent = _killing_first_calls(2)
    require(run_from_checkout(root, agent, branch="a").killed, "la primera debía morir")
    require(run_from_checkout(root, agent, branch="b").killed, "la segunda debía morir")
    calls = len(agent.calls)
    capsys.readouterr()

    assert run_from_checkout(root, agent) == Outcome(10)

    err = capsys.readouterr().err
    assert "lictor/a, lictor/b" in err
    assert "--branch" in err
    assert len(agent.calls) == calls
    assert run_from_checkout(root, agent, branch="a") == Outcome(0)
    assert run_from_checkout(root, agent) == Outcome(0), "queda una sola: se reanuda sin nombre"
    assert green_ids(run_worktree(root, "lictor/a")) == ALL_GREEN
    assert green_ids(run_worktree(root, "lictor/b")) == ALL_GREEN


def test_new_starts_another_run_beside_the_one_that_can_be_resumed(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    agent = _killing_first_calls(1)
    require(run_from_checkout(root, agent, branch="a").killed, "la primera debía morir")

    assert run_from_checkout(root, agent, new=True) == Outcome(0)

    branches = [run.branch for run in read_run_index(root)]
    assert branches[0] == "lictor/a"
    assert len(branches) == 2
    assert green_ids(run_worktree(root, branches[1])) == ALL_GREEN


def test_status_from_the_checkout_tells_each_run_by_its_branch(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    agent = _killing_first_calls(1)
    require(run_from_checkout(root, agent, branch="b").killed, "debía quedar a medias")
    assert run_from_checkout(root, agent, branch="a") == Outcome(0)

    result = CliRunner().invoke(app, ["status", "--cwd", str(root)])

    assert result.exit_code == 0, result.output
    assert f"Rama lictor/b · {run_worktree(root, 'lictor/b')}" in result.output
    assert f"Rama lictor/a · {run_worktree(root, 'lictor/a')}" in result.output


def test_inside_a_linked_worktree_a_branch_name_is_refused(make_project: ProjectFactory) -> None:
    root = make_project()
    worktree = root.with_name(f"{root.name}-enlazado")
    git(root, "worktree", "add", "-q", "-b", "trabajo", str(worktree))

    assert run_from_checkout(worktree, Agent(), branch="otra") == Outcome(10)

    assert read_run_index(root) == []
    assert read_run_index(worktree) == []


def test_two_runs_in_parallel_and_a_commit_by_hand_dont_step_on_each_other(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """Both stand still inside an agent call while the person commits in their checkout."""
    root = make_project()
    point = "implement:durante"
    release = tmp_path / "suelta"
    runs: dict[str, subprocess.Popen[bytes]] = {}
    try:
        for name in ("a", "b"):
            marker = tmp_path / f"alcanzado-{name}"
            flags = tmp_path / name
            flags.mkdir()
            command = _command(root, tmp_path, "verde", point, str(marker), str(release))
            command[command.index(str(tmp_path / "marcas"))] = str(flags)
            runs[name] = _stop_at(command, point, marker, _environment(branch=name))

        write(root, "NOTAS.md", "escrito por la persona mientras corren\n")
        git(root, "add", "NOTAS.md")
        git(root, "commit", "-q", "-m", "docs: notas a mano")
        person = git(root, "rev-parse", "HEAD")
    finally:
        release.touch()
    outputs = {
        name: process.communicate(timeout=RESUME_TIMEOUT_S)[0] for name, process in runs.items()
    }

    for name, process in runs.items():
        assert process.returncode == 0, outputs[name].decode()
        worktree = run_worktree(root, f"lictor/{name}")
        assert green_ids(worktree) == ALL_GREEN
        assert false_greens(worktree) == []
        assert not events_of(worktree, "state.tampered")
        failed_at = {e["data"]["failed_at"] for e in events_of(worktree, "verify.finished")}
        assert "L0-estado" not in failed_at
        assert "NOTAS.md" not in git(worktree, "ls-tree", "--name-only", "HEAD")
    assert git(root, "rev-parse", "HEAD") == person, "el commit de la persona sigue en su rama"
    assert git(root, "branch", "--show-current").strip() == "master"
