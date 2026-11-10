"""Controls: an honest run on the real profile, and crash cases the audit found to hold."""

from pathlib import Path
from typing import Any

import pytest

from lictor.adapters.config.loader import load_config
from lictor.adapters.vcs.git_cli import GitCli
from lictor.application.usecases.commit_green import CommitGreen
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    green_ids,
    require,
    run_lictor,
    write,
)

ALL_SCENARIOS = ["IVA-001", "IVA-002", "IVA-003"]


def test_c0_honest_agent_on_real_profile(make_project: ProjectFactory) -> None:
    root = make_project()
    agent = Agent()

    assert run_lictor(root, agent) == Outcome(0)

    assert agent.calls == ["driver", *ALL_SCENARIOS]
    assert green_ids(root) == ALL_SCENARIOS
    assert false_greens(root) == []
    verified = events_of(root, "verify.finished")
    assert [event["data"]["ok"] for event in verified] == [True] * 3


def test_oracle_rejects_a_green_commit_over_the_stub(make_project: ProjectFactory) -> None:
    root = make_project(sync=False)
    write(root, "src/iva/__init__.py", "def general(importe):\n    raise NotImplementedError\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: falso", "--trailer", "Scenario: IVA-001")

    found = false_greens(root)

    assert len(found) == 1
    assert "NotImplementedError" in found[0]


def test_d15a_crash_on_first_commit_resumes_without_duplicates(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project()
    original = GitCli.advance_head
    armed = [True]

    def die_once(self: GitCli, *args: Any, **kwargs: Any) -> Any:
        if armed.pop() if armed else False:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GitCli, "advance_head", die_once)
    agent = Agent()

    require(run_lictor(root, agent).killed, "el primer commit debía matar la ejecución")
    assert (root / ".lictor" / "seal.json").is_file()

    assert run_lictor(root, agent) == Outcome(0)
    assert green_ids(root) == ALL_SCENARIOS


def test_d15b_crash_after_green_commit_resumes_without_duplicates(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project()
    original = CommitGreen.execute
    armed = [True]

    def die_after(self: CommitGreen, request: Any) -> Any:
        result = original(self, request)
        if armed.pop() if armed else False:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(CommitGreen, "execute", die_after)
    agent = Agent()

    require(run_lictor(root, agent).killed, "CommitGreen debía matar la ejecución")

    assert run_lictor(root, agent) == Outcome(0)
    assert green_ids(root) == ALL_SCENARIOS


def test_repro_projects_use_the_real_profile(make_project: ProjectFactory) -> None:
    root: Path = make_project(sync=False)
    config = load_config(root)
    runs = [control.run for control in config.controls]
    assert "uv run ruff check src" in runs
    assert "uv run mypy src" in runs
    assert config.acceptance.run.startswith("uv run pytest tests/acceptance")


def test_each_worker_builds_its_own_template(template: Path, worker_id: str) -> None:
    """Cases run in parallel: a template shared between workers would mix their state."""
    assert worker_id == "master" or worker_id in str(template)
