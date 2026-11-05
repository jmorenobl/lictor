"""Events shared by the use cases that run the agent."""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor.application.events import new_event
from lictor.application.ports import Clock
from lictor.application.run_files import relative_to_project
from lictor.domain.model import AgentResult, AgentRole, Event


def denial_targets(result: AgentResult) -> list[dict[str, str]]:
    """Serialize the tool calls the backend refused."""
    return [{"tool": d.tool, "target": d.target} for d in result.permission_denials]


def tamper_event(finished: Event, changes: Sequence[str]) -> Event:
    """Record what the agent's call changed outside its copy, which has just been restored."""
    return Event(
        id=uuid.uuid4().hex,
        type="lictor.state.tampered.v1",
        source=finished.source,
        time=finished.time,
        runid=finished.runid,
        scenarioid=finished.scenarioid,
        data={
            "role": finished.data["role"],
            "attempt": finished.data["attempt"],
            "changes": list(changes),
        },
    )


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


@dataclass(frozen=True)
class AgentCall:
    """One call to the agent, as the journal tells it."""

    run_id: str
    scenario_id: str | None
    role: AgentRole
    attempt: int
    signal: str
    model: str
    cli_version: str
    prompt_sha: str
    raw_path: Path
    project_root: Path
    prompt: Mapping[str, str] | None = None  # the file the prompt was saved to, and its hash


def agent_finished_event(
    clock: Clock,
    call: AgentCall,
    result: AgentResult,
    extra: Mapping[str, object] | None = None,
) -> Event:
    """Record how a call to the agent ended and where its raw output lies."""
    stderr_path = result.stderr_path
    return new_event(
        clock,
        "lictor.agent.finished.v1",
        call.run_id,
        scenario_id=call.scenario_id,
        data={
            "role": call.role,
            "attempt": call.attempt,
            "status": result.status,
            "signal": call.signal,
            "exit_code": result.exit_code,
            "permission_denials": denial_targets(result),
            "model": call.model,
            "cli_version": call.cli_version,
            "tokens": {
                "input": result.tokens.input,
                "output": result.tokens.output,
                "cache_read": result.tokens.cache_read,
                "cache_write": result.tokens.cache_write,
            },
            "cost_usd": result.cost_usd or 0.0,
            "cost_kind": result.cost_kind,
            "num_turns": result.num_turns,
            "duration_ms": result.duration_ms,
            "prompt_sha": call.prompt_sha,
            "prompt": dict(call.prompt) if call.prompt else None,
            "raw": relative_to_project(call.raw_path, call.project_root),
            "stderr": (
                relative_to_project(stderr_path, call.project_root) if stderr_path else None
            ),
            **(extra or {}),
        },
    )
