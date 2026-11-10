"""0.3.0, phase 1 (ADR-0005, points 2 and 6): integrity inside a linked worktree.

A linked worktree keeps `HEAD` in `<common>/worktrees/<name>` and shares `config`, `hooks/` and
`info/` with the main checkout: what decides what git runs lives in the common directory.
"""

from pathlib import Path

import pytest

from lictor.application.ports import Vcs
from lictor.domain.model import AgentTask
from lictor.orchestration import graph
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
    run_lictor,
    shell,
    uv_sync,
    write,
)


def _linked_worktree(root: Path) -> Path:
    worktree = root.with_name(f"{root.name}-enlazado")
    git(root, "worktree", "add", "-q", "-b", "trabajo", str(worktree))
    uv_sync(worktree)
    require((worktree / ".git").is_file(), "el worktree enlazado debía tener un fichero .git")
    return worktree


def _failed_at(root: Path) -> set[str]:
    return {event["data"]["failed_at"] for event in events_of(root, "verify.finished")}


def test_from_the_copy_the_common_directory_is_out_of_reach(make_project: ProjectFactory) -> None:
    root = make_project()
    worktree = _linked_worktree(root)
    config = (root / ".git" / "config").read_bytes()
    refused: list[int] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        require(not task.cwd.is_relative_to(worktree), "el agente debía trabajar fuera del run")
        require(not task.cwd.is_relative_to(root), "el agente debía trabajar fuera del proyecto")
        require(not (task.cwd / ".git").exists(), "la copia no debía tener .git")
        good_impl(task, scenario_id, attempt)
        refused.append(shell(["git", "config", "--local", "core.x", "y"], task.cwd).returncode)
        refused.append(shell(["git", "rev-parse", "--git-common-dir"], task.cwd).returncode)

    assert run_lictor(worktree, Agent(impl=impl)) == Outcome(0)

    assert refused
    assert all(refused)
    assert (root / ".git" / "config").read_bytes() == config
    assert green_ids(worktree) == ["IVA-001", "IVA-002", "IVA-003"]


def test_the_common_directory_reached_by_absolute_path_is_detected_and_undone(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    worktree = _linked_worktree(root)
    common = root / ".git"
    config = (common / "config").read_bytes()
    planted = [False]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if not planted[0]:
            planted[0] = True
            write(common, "hooks/post-commit", "#!/bin/sh\ntouch /tmp/lictor-hook\n")
            git(root, "config", "core.lictorplantado", "si")

    run_lictor(worktree, Agent(impl=impl))

    require(planted[0], "el ataque debía ejecutarse")
    assert "L0-estado" in _failed_at(worktree)
    changes = events_of(worktree, "state.tampered")[0]["data"]["changes"]
    assert ".git/hooks/post-commit" in changes
    assert ".git/config" in changes
    assert not (common / "hooks" / "post-commit").exists()
    assert (common / "config").read_bytes() == config
    assert green_ids(worktree) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(worktree) == []


def test_config_planted_in_the_common_directory_before_dying_refuses_the_resume(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    root = make_project()
    worktree = _linked_worktree(root)
    marker = tmp_path / "filtro-ejecutado"

    def impl(_task: AgentTask, _sid: str, _attempt: int) -> None:
        git(root, "config", "filter.mal.clean", f"touch {marker}; cat")
        write(root / ".git", "info/attributes", "* filter=mal\n")
        write(worktree, "README.md", "tocado para que git tenga que leerlo\n")
        raise KeyboardInterrupt

    agent = Agent(impl=impl)
    require(run_lictor(worktree, agent).killed, "el agente debía matar la ejecución")
    calls = len(agent.calls)

    assert run_lictor(worktree, agent) == Outcome(10)

    assert len(agent.calls) == calls, "no debía llamarse al agente"
    assert not marker.exists()
    assert ".git" in events_of(worktree, "run.stopped")[-1]["data"]["reason"]


def test_two_worktrees_dont_share_the_verified_ref(make_project: ProjectFactory) -> None:
    root = make_project()
    worktree = _linked_worktree(root)

    assert run_lictor(worktree, Agent()) == Outcome(0)

    assert git(root, "for-each-ref", "refs/lictor/") == ""
    assert git(root, "for-each-ref", "refs/worktree/") == ""
    verified = git(worktree, "for-each-ref", "--format=%(refname)", "refs/worktree/")
    assert verified.strip() == "refs/worktree/lictor/verified"


def test_a_checkpoint_anchored_by_0_2_0_is_not_read_as_tampering(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.2.0 hashed the repository another way: comparing would read as tampering."""
    root = make_project()
    observe = graph.observe_anchor

    def old_anchor(vcs: Vcs, project_root: Path) -> dict[str, str]:
        anchor = observe(vcs, project_root)
        anchor.pop("format", None)
        return anchor

    def impl(_task: AgentTask, _sid: str, _attempt: int) -> None:
        raise KeyboardInterrupt

    agent = Agent(impl=impl)
    with monkeypatch.context() as patch:
        patch.setattr(graph, "observe_anchor", old_anchor)
        require(run_lictor(root, agent).killed, "el agente debía matar la ejecución")
    calls = len(agent.calls)

    assert run_lictor(root, agent) == Outcome(10)

    assert len(agent.calls) == calls, "no debía llamarse al agente"
    reason = events_of(root, "run.stopped")[-1]["data"]["reason"]
    assert "versión anterior" in reason
    assert "--new" in reason
    assert "cambiaron" not in reason
