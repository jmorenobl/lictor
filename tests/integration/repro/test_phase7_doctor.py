"""Phase 7: prerequisites are checked before any agent call."""

import pytest

from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import Agent, git, pending, run_lictor


@pending(7, "portabilidad: identidad git")
def test_e29_missing_git_identity_is_caught_before_the_driver(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project()
    git(root, "config", "--unset", "user.name")
    git(root, "config", "--unset", "user.email")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "")
    monkeypatch.setenv("EMAIL", "")
    agent = Agent()

    run_lictor(root, agent)

    assert agent.calls == []
