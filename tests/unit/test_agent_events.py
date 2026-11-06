"""Unit tests for the events of a call to the agent: their whole shape is what the report reads."""

from dataclasses import replace
from pathlib import Path

from lictor.application.events import SOURCE
from lictor.application.usecases.agent_events import (
    AgentCall,
    agent_finished_event,
    agent_started_event,
    denial_event,
    tamper_event,
)
from lictor.domain.model import AgentResult, PermissionDenial, Tokens


class _Clock:
    def now_iso(self) -> str:
        return "2026-11-03T12:00:00Z"


CALL = AgentCall(
    run_id="R1",
    scenario_id="IVA-001",
    role="implement",
    attempt=2,
    signal="ok",
    model="claude-x",
    cli_version="2.1.316",
    prompt_sha="sha256:p",
    raw_path=Path("/p/.lictor/runs/R1/IVA-001/002-implement.json"),
    project_root=Path("/p"),
    prompt={"path": ".lictor/runs/R1/IVA-001/002-implement.prompt.md", "hash": "sha256:q"},
)
RESULT = AgentResult(
    status="ok",
    tokens=Tokens(input=1, output=2, cache_read=3, cache_write=4),
    cost_usd=0.5,
    cost_kind="estimated",
    num_turns=6,
    duration_ms=700,
    exit_code=0,
    stderr_path=Path("/p/.lictor/runs/R1/IVA-001/002-implement.stderr.log"),
    permission_denials=(PermissionDenial(tool="Bash", target="git push"),),
)


def test_agent_finished_event_records_the_whole_call() -> None:
    event = agent_finished_event(_Clock(), CALL, RESULT, extra={"reverted_files": ["x"]})

    assert (event.type, event.runid, event.scenarioid, event.time) == (
        "lictor.agent.finished.v1",
        "R1",
        "IVA-001",
        "2026-11-03T12:00:00Z",
    )
    assert event.data == {
        "role": "implement",
        "attempt": 2,
        "status": "ok",
        "signal": "ok",
        "exit_code": 0,
        "permission_denials": [{"tool": "Bash", "target": "git push"}],
        "model": "claude-x",
        "cli_version": "2.1.316",
        "tokens": {"input": 1, "output": 2, "cache_read": 3, "cache_write": 4},
        "cost_usd": 0.5,
        "cost_kind": "estimated",
        "num_turns": 6,
        "duration_ms": 700,
        "prompt_sha": "sha256:p",
        "prompt": {"path": ".lictor/runs/R1/IVA-001/002-implement.prompt.md", "hash": "sha256:q"},
        "raw": ".lictor/runs/R1/IVA-001/002-implement.json",
        "stderr": ".lictor/runs/R1/IVA-001/002-implement.stderr.log",
        "reverted_files": ["x"],
    }


def test_agent_finished_event_without_cost_or_stderr() -> None:
    event = agent_finished_event(_Clock(), replace(CALL, prompt=None), AgentResult(status="error"))

    assert (event.data["cost_usd"], event.data["stderr"], event.data["prompt"]) == (0.0, None, None)
    assert "reverted_files" not in event.data


def test_tamper_and_denial_events_share_the_envelope_of_the_call() -> None:
    finished = agent_finished_event(_Clock(), CALL, RESULT)

    tampered = tamper_event(finished, (".git/config", "lictor.json"))
    denied = denial_event(RESULT, finished)

    for event, kind in ((tampered, "lictor.state.tampered.v1"), (denied, "lictor.agent.denied.v1")):
        assert (event.type, event.source, event.time, event.runid, event.scenarioid) == (
            kind,
            SOURCE,
            "2026-11-03T12:00:00Z",
            "R1",
            "IVA-001",
        )
        assert len(event.id) == 32
        assert event.id != finished.id
    assert tampered.data == {
        "role": "implement",
        "attempt": 2,
        "changes": [".git/config", "lictor.json"],
    }
    assert denied.data == {
        "role": "implement",
        "attempt": 2,
        "denials": [{"tool": "Bash", "target": "git push"}],
    }


def test_agent_started_event_says_which_attempt_of_how_many_and_where_the_prompt_is() -> None:
    event = agent_started_event(_Clock(), CALL, max_attempts=3)

    assert (event.type, event.runid, event.scenarioid, event.time) == (
        "lictor.agent.started.v1",
        "R1",
        "IVA-001",
        "2026-11-03T12:00:00Z",
    )
    assert event.data == {
        "role": "implement",
        "attempt": 2,
        "max_attempts": 3,
        "prompt": {"path": ".lictor/runs/R1/IVA-001/002-implement.prompt.md", "hash": "sha256:q"},
    }


def test_agent_started_event_without_a_saved_prompt() -> None:
    event = agent_started_event(_Clock(), replace(CALL, prompt=None), max_attempts=1)

    assert event.data["prompt"] is None
