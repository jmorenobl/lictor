"""S22: what an editor writes in `.git/config` isn't the agent tampering with Lictor's state.

An editor with git open adds its own key under the branch just created (`vscode-merge-base`) a few
seconds after it is: in a run, that is when Lictor creates its branch and the agent works. Before
0.25.0 the whole config was compared, the attempt failed at `L0-estado` and the message named only
the file. Now a key git doesn't read, under a branch's section, is neither a change nor undone; a
key git reads (`core.hooksPath`) is still a tampering, named by its key.
"""

from collections.abc import Callable
from pathlib import Path

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    git,
    good_impl,
    green_ids,
    run_lictor,
    shell,
)

Impl = Callable[[AgentTask, str, int], None]


def _writing(root: Path, key: str, value: str) -> Impl:
    """An implementer that sets `key` in the project's config on its first call."""
    written = [False]

    def impl(task: AgentTask, scenario_id: str, attempt: int = 1) -> None:
        good_impl(task, scenario_id, attempt)
        if not written[0]:
            written[0] = True
            git(root, "config", key.replace("{branch}", _branch(root)), value)

    return impl


def _branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def test_an_editor_s_key_under_the_run_s_branch_fails_no_attempt(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    agent = Agent(impl=_writing(root, "branch.{branch}.vscode-merge-base", "origin/main"))

    assert run_lictor(root, agent) == Outcome(0)

    assert events_of(root, "state.tampered") == []
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    branch = _branch(root)
    assert git(root, "config", f"branch.{branch}.vscode-merge-base").strip() == "origin/main"


def test_a_key_git_reads_is_still_a_tampering_named_by_its_key(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    agent = Agent(impl=_writing(root, "core.hooksPath", "/srv/ganchos"))

    assert run_lictor(root, agent) == Outcome(0)

    (tampered,) = events_of(root, "state.tampered")
    assert ".git/config (core.hookspath)" in tampered["data"]["changes"]
    assert shell(["git", "config", "--get", "core.hooksPath"], root).stdout.strip() == ""
