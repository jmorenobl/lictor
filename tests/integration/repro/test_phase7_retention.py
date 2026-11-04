"""Phase 7: `runs/` keeps the most recent runs, and never the one a resume needs."""

from pathlib import Path

from lictor.domain.model import AgentStatus, AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import Agent, events_of, good_impl, journal, run_lictor

OLD_RUNS = [f"2026100{d}-120000-beef" for d in range(1, 6)]


def _seed(root: Path) -> Path:
    runs = root / ".lictor" / "runs"
    for run_id in OLD_RUNS:
        (runs / run_id / "IVA-001" / "001-verify").mkdir(parents=True)
    (runs / "notas").mkdir()
    return runs


def test_a_new_run_keeps_the_most_recent_and_the_last_runs_evidence(
    make_project: ProjectFactory,
) -> None:
    root = make_project(config_changes={"retention": {"keep_runs": 3}})
    runs = _seed(root)
    # The last run is the oldest here: it is still kept.
    (root / ".lictor" / "current_run").write_text(OLD_RUNS[0], encoding="utf-8")

    outcome = run_lictor(root, Agent())

    assert outcome.exit_code == 0
    (pruned,) = events_of(root, "runs.pruned")
    # Three counting the new one: the two most recent before it, and the last run's on top.
    assert pruned["data"] == {"pruned": OLD_RUNS[1:3], "keep_runs": 3}
    new_run = pruned["runid"]
    remaining = sorted(p.name for p in runs.iterdir())
    assert remaining == sorted([OLD_RUNS[0], *OLD_RUNS[3:], new_run, "notas"])
    types = [event["type"] for event in journal(root)]
    assert types.index("lictor.runs.pruned.v1") < types.index("lictor.run.started.v1")


def test_a_resume_retires_nothing(make_project: ProjectFactory) -> None:
    root = make_project(config_changes={"retention": {"keep_runs": 1}})
    limited = [True]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if limited.pop() if limited else False:
            return "rate_limited"
        return good_impl(task, scenario_id, attempt)

    agent = Agent(impl=impl)
    assert run_lictor(root, agent).exit_code == 50
    runs = _seed(root)

    assert run_lictor(root, agent).exit_code == 0

    assert events_of(root, "runs.pruned") == []
    assert all((runs / run_id).is_dir() for run_id in OLD_RUNS)
