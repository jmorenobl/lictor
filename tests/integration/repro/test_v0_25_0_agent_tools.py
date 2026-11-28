"""S17: a control a project adds reaches the agent without copying the profile's `agent_tools`.

The check of a control that blocks every attempt was dropped from the agent's commands in silence
when no rule allowed it, and `agent_tools` in `lictor.json` replaces the profile's whole: the
project had to copy its six rules to add one. Now every check verification runs in every attempt
is granted to the agent by an exact rule; `"agent": false` keeps one out.
"""

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import Agent, Outcome, good_impl, run_lictor

SECRETS = {"id": "secretos", "level": "L4", "run": "true --secretos", "report": "exit-code"}


def test_a_control_the_project_adds_is_the_agent_s_to_run(make_project: ProjectFactory) -> None:
    root = make_project(extra_controls=[SECRETS])
    seen: list[tuple[str, ...]] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int = 1) -> None:
        seen.append(tuple(task.allowed_tools))
        good_impl(task, scenario_id, attempt)

    agent = Agent(impl=impl)

    assert run_lictor(root, agent) == Outcome(0)

    assert "Bash(true --secretos)" in seen[0]
    assert "Bash(uv run pytest:*)" in seen[0]
    told = agent.prompts["IVA-001"][0]
    assert "  - `true --secretos`" in told
    assert "y estas órdenes tal cual: `true --secretos`" in told


def test_a_control_kept_from_the_agent_is_not_granted(make_project: ProjectFactory) -> None:
    root = make_project(extra_controls=[{**SECRETS, "agent": False}])
    seen: list[tuple[str, ...]] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int = 1) -> None:
        seen.append(tuple(task.allowed_tools))
        good_impl(task, scenario_id, attempt)

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    assert not any("secretos" in tool for tool in seen[0])
