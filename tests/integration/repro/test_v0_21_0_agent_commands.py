"""F83: the agent's commands come from the controls, and follow a control the project changes.

With the real profile and `uv`. In the 4a pass with the 0.20.0, `lint` read more than the agent's
`uv run ruff check src`, from the profile's fixed list, and an attempt and a refactor were lost.
The list is gone: the agent checks each blocking control with what it runs, and gets the fix of
`format` only while that control blocks.
"""

from lictor.application.ports import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    false_greens,
    good_impl,
    good_refactor,
    green_ids,
    run_lictor,
    with_refactor,
)

ALL = ["IVA-001", "IVA-002", "IVA-003"]
FIX = "- Tras tu último cambio, ejecuta `uv run ruff format` (el control format bloquea)"


def test_the_implementer_checks_lint_with_the_run_the_project_wrote(
    make_project: ProjectFactory,
) -> None:
    lint = {"id": "lint", "run": "uv run ruff check src -q"}
    root = make_project(config_changes={"controls": [lint]})
    agent = Agent(impl=good_impl)

    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ALL
    [prompt, *_] = agent.prompts["IVA-001"]
    assert "  - `uv run ruff check src -q`\n" in prompt
    assert "  - `uv run ruff check src`\n" not in prompt
    assert "  - `uv run pytest tests/acceptance -q`\n" in prompt
    assert false_greens(root) == []


@with_refactor
def test_with_format_off_nothing_asks_to_format_and_the_refactor_stays_in_scope(
    make_project: ProjectFactory,
) -> None:
    """The light project drops `format`: its fix would rewrite files nobody verified."""
    root = make_project()

    def refactor(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_refactor(task, scenario_id, attempt)

    agent = Agent(impl=good_impl, refactor=refactor)

    assert run_lictor(root, agent) == Outcome(0)

    prompts = [*agent.prompts["IVA-001"], *agent.prompts["refactor:IVA-001"]]
    assert prompts
    assert all("ruff format" not in prompt for prompt in prompts)
    outcomes = {
        e["scenarioid"]: e["data"]["outcome"] for e in events_of(root, "scenario.refactored")
    }
    assert outcomes == dict.fromkeys(ALL, "applied")
    assert false_greens(root) == []


def test_with_format_blocking_the_agent_formats_and_then_checks(
    make_project: ProjectFactory,
) -> None:
    root = make_project(config_changes={"controls": [{"id": "format"}]})
    agent = Agent(impl=good_impl)

    assert run_lictor(root, agent) == Outcome(0)

    [prompt, *_] = agent.prompts["IVA-001"]
    assert any(line.startswith(FIX) for line in prompt.splitlines())
    assert "  - `uv run ruff format --check src`\n" in prompt
    assert green_ids(root) == ALL
