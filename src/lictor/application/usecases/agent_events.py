"""Events shared by the use cases that run the agent."""

import uuid

from lictor.domain.model import AgentResult, Event


def denial_targets(result: AgentResult) -> list[dict[str, str]]:
    """Serialize the tool calls the backend refused."""
    return [{"tool": d.tool, "target": d.target} for d in result.permission_denials]


def denial_event(result: AgentResult, finished: Event) -> Event:
    """Record that the agent was refused something, even if the call itself succeeded."""
    return Event(
        id=uuid.uuid4().hex,
        type="lictor.agent.denied.v1",
        source=finished.source,
        time=finished.time,
        runid=finished.runid,
        scenarioid=finished.scenarioid,
        data={
            "role": finished.data["role"],
            "attempt": finished.data["attempt"],
            "denials": denial_targets(result),
        },
    )
