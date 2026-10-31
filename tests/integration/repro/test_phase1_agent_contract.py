"""Phase 1 (A1, A6): the agent's environment and result are under control."""

from pathlib import Path

import pytest

from lictor.adapters.agents.claude_cli import ClaudeCLI
from lictor.domain.model import AgentResult, AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    pending,
    quarantined,
    require,
    run_lictor,
)

PLAIN_LIMIT = "echo 'Claude usage limit reached. Your limit will reset at 7pm'; exit 1"
JSON_LIMIT = (
    """echo '{"type":"result","subtype":"success","is_error":true,"""
    """"result":"You'"'"'ve hit your session limit · resets 3pm"}'"""
)
JSON_429 = (
    """echo '{"type":"result","subtype":"error_during_execution","is_error":true,"""
    """"api_error_status":429,"result":"x"}'"""
)
OK_RC_1 = (
    """echo '{"type":"result","subtype":"success","is_error":false,"result":"hecho"}'; exit 1"""
)
FALSE_POSITIVE = (
    """echo '{"type":"result","subtype":"success","is_error":true,"""
    """"result":"Falló el test de rate limit del módulo"}'"""
)
LOGIN = (
    """echo '{"type":"result","subtype":"success","is_error":true,"result":"Please run /login"}'"""
)
DENIALS = (
    """echo '{"type":"result","subtype":"success","is_error":false,"result":"todo ok","""
    """"permission_denials":[{"tool_name":"Bash"}]}'"""
)
JSON_LIST = """echo '[{"type":"result"}]'"""


def _run_fake_claude(tmp_path: Path, body: str) -> AgentResult:
    binary = tmp_path / "claude.sh"
    binary.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    binary.chmod(0o755)
    task = AgentTask(
        role="implement",
        prompt="p",
        prompt_sha="x",
        cwd=tmp_path,
        model="m-1",
        allowed_tools=("Bash(x:*)",),
        max_turns=1,
        timeout_s=5,
        raw_path=tmp_path / "raw.json",
    )
    return ClaudeCLI(cli_binary=str(binary)).run(task)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(PLAIN_LIMIT, "rate_limited", marks=pending(1, "A6"), id="d19-texto-plano-rc1"),
        pytest.param(JSON_LIMIT, "rate_limited", marks=pending(1, "A6"), id="d19-json-limite"),
        pytest.param(JSON_429, "rate_limited", id="d19-json-429"),
        pytest.param(OK_RC_1, "error", marks=pending(1, "A6"), id="d19-json-ok-rc1"),
        pytest.param(FALSE_POSITIVE, "error", marks=pending(1, "A6"), id="d19-falso-positivo"),
        pytest.param(LOGIN, "auth", marks=pending(1, "A6"), id="d19-login"),
    ],
)
def test_d19_status_from_cli_output(tmp_path: Path, body: str, expected: str) -> None:
    assert _run_fake_claude(tmp_path, body).status == expected


@pending(1, "A6")
def test_d19_non_object_json_is_an_error_status(tmp_path: Path) -> None:
    try:
        status = _run_fake_claude(tmp_path, JSON_LIST).status
    except AttributeError as exc:
        status = f"excepción: {exc}"
    assert status == "error"


@pending(1, "A1")
def test_d19_permission_denials_are_reported(tmp_path: Path) -> None:
    result = _run_fake_claude(tmp_path, DENIALS)
    assert getattr(result, "permission_denials", ()), "las denegaciones se pierden"


@pending(1, "A6")
def test_d18_backend_down_quarantines_nothing(make_project: ProjectFactory) -> None:
    root = make_project()
    agent = Agent(impl=lambda _task, _sid, _attempt: "error")

    outcome = run_lictor(root, agent)

    require("IVA-001" in agent.calls, "el agente debía llegar a implementar")
    assert quarantined(root) == [], f"exit {outcome.exit_code}, llamadas {agent.calls}"
    assert agent.calls.count("IVA-001") < 3


@pending(1, "A1")
def test_d26_agent_gets_runnable_commands_including_acceptance(
    make_project: ProjectFactory,
) -> None:
    root = make_project()

    def impl(_task: AgentTask, _sid: str, _attempt: int) -> None:
        raise KeyboardInterrupt

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require(agent.prompts["IVA-001"], "el agente debía recibir el prompt")
    rules = next(line for line in agent.prompts["IVA-001"][0].splitlines() if "Comprueba" in line)
    assert "{out}" not in rules, rules
    assert "tests/acceptance" in rules, rules
