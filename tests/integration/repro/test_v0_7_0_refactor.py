"""0.7.0 (ADR-0010): a refactor after each implemented green, verified like an attempt.

With the real profile and `uv`: a refactor that keeps behaviour goes in as a commit of its own,
and the run's final tree, which no `Scenario:` commit names any more, still meets every green.
One that passes fewer unit tests than its green is discarded, and the green stays.
"""

from pathlib import Path

from lictor.application.ports import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    UNIT_TEST,
    Agent,
    Outcome,
    all_controls,
    events_of,
    false_greens,
    git,
    good_refactor,
    green_ids,
    run_lictor,
    with_refactor,
    write,
)

ALL = ["IVA-001", "IVA-002", "IVA-003"]
SUBJECT = "mejorar el código sin cambiar el comportamiento"


def _refactor_commits(root: Path) -> list[str]:
    log = git(root, "log", "--reverse", "--format=%s")
    return [line for line in log.splitlines() if line.startswith("refactor(")]


def _outcomes(root: Path) -> dict[str, tuple[str, str | None]]:
    return {
        e["scenarioid"]: (e["data"]["outcome"], e["data"]["failed_at"])
        for e in events_of(root, "scenario.refactored")
    }


@with_refactor
def test_each_implemented_green_gets_a_refactor_that_keeps_every_green(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    agent = Agent(refactor=good_refactor)

    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ALL
    assert _refactor_commits(root) == [f"refactor({sid}): {SUBJECT}" for sid in ALL]
    assert _outcomes(root) == dict.fromkeys(ALL, ("applied", None))
    # The final tree is the last refactor's, which no `Scenario:` commit names.
    assert git(root, "log", "-1", "--format=%s").startswith("refactor(IVA-003)")
    assert false_greens(root) == []
    assert not git(root, "status", "--short")


def _empties_the_unit_tests(task: AgentTask, scenario_id: str, _attempt: int = 1) -> None:
    """A refactor that leaves the unit tests without a single case: they still pass."""
    write(task.cwd, "tests/unit/test_smoke.py", "")


@with_refactor
@all_controls
def test_a_refactor_that_passes_fewer_unit_tests_is_discarded_and_the_green_stays(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    agent = Agent(refactor=_empties_the_unit_tests)

    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ALL
    assert _refactor_commits(root) == []
    assert _outcomes(root) == dict.fromkeys(ALL, ("discarded", "L0-regresión"))
    assert (root / "tests" / "unit" / "test_smoke.py").read_text(encoding="utf-8") == UNIT_TEST
    assert false_greens(root) == []
    assert not events_of(root, "scenario.quarantined")
