"""[M6]: the guard of `code_exclude` (0.23.1) in each state of an indexed run (0.23.2).

A `code_exclude` that excludes every code file of HEAD leaves `L0-alcance` nothing to guard: a new
run stops before any agent, and so must one that is picked again from the index. These cases cut
before any tool of the toolchain runs, so they need neither Go nor `gotestsum`: the project only
looks like a Go one. The state that goes on needs a sealed driver, and is with the Go cases
(`test_v0_24_0_toolchains.py`).
"""

import json
from pathlib import Path

import pytest

from lictor import cli
from lictor.application.run_index import mark_retiring, read_run_index
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    git,
    require,
    run_from_checkout,
    run_worktree,
    write,
)
from tests.integration.repro.toolchains import FEATURE_NIF, GO_CONFIG, GO_STUB

EXCLUDED = "code_exclude (*.go) excluye todos los ficheros"


def _excluding_project(root: Path) -> Path:
    """A `generic` project whose only code file `code_exclude` excludes."""
    write(root, "go.mod", "module example.com/toy\n\ngo 1.22\n")
    write(root, "features/nif.feature", FEATURE_NIF)
    write(root, "nif/nif.go", GO_STUB)
    write(root, "lictor.json", json.dumps({**GO_CONFIG, "code_exclude": ["*.go"]}, indent=2))
    git(root, "init", "-q", "-b", "master")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: initial commit")
    return root


def _killed_after_the_worktree(root: Path, agent: Agent) -> Path:
    """What a kill right after `git worktree add` leaves: the worktree, its entry, no checkpoint."""

    def killed(*_args: object, **_kwargs: object) -> int:
        raise KeyboardInterrupt

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli, "_run_here", killed)
        outcome = run_from_checkout(root, agent, branch="nif")
    require(outcome.exit_code is None, "el run debía morir tras crear su worktree")
    worktree = run_worktree(root, "lictor/nif")
    require(not (worktree / ".lictor" / "checkpoints.sqlite").exists(), "sin checkpoint")
    return worktree


def _stopped_for_the_paths(worktree: Path) -> bool:
    ends = [*events_of(worktree, "run.finished"), *events_of(worktree, "run.stopped")]
    return any(EXCLUDED in str(event["data"].get("reason", "")) for event in ends)


def test_a_run_that_never_started_is_picked_again_and_stops_at_its_start(tmp_path: Path) -> None:
    root = _excluding_project(tmp_path / "nif")
    agent = Agent()

    worktree = _killed_after_the_worktree(root, agent)

    assert run_from_checkout(root, agent) == Outcome(10)

    assert agent.calls == []
    assert [entry.branch for entry in read_run_index(root)] == ["lictor/nif"]
    assert _stopped_for_the_paths(worktree)


@pytest.mark.parametrize("state", ["broken", "retiring"])
def test_a_run_that_cant_go_on_is_never_picked_and_a_new_one_stops_at_its_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    root = _excluding_project(tmp_path / "nif")
    agent = Agent()
    worktree = _killed_after_the_worktree(root, agent)
    checkpoint = worktree / ".lictor" / "checkpoints.sqlite"
    if state == "broken":
        checkpoint.parent.mkdir(exist_ok=True)
        checkpoint.write_bytes(b"esto no es una base de datos")
    else:
        mark_retiring(root, "lictor/nif")
    before = sorted(p.relative_to(worktree).as_posix() for p in worktree.rglob("*"))
    capsys.readouterr()

    assert run_from_checkout(root, agent, branch="nif") == Outcome(10)
    named = capsys.readouterr().err
    assert run_from_checkout(root, agent, branch="otra") == Outcome(10)

    assert agent.calls == []
    assert "`lictor retire nif`" in named
    assert sorted(p.relative_to(worktree).as_posix() for p in worktree.rglob("*")) == before
    assert _stopped_for_the_paths(run_worktree(root, "lictor/otra"))
