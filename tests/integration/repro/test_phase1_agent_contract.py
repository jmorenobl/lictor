"""Phase 1 (A1, A6): the agent's environment and result are under control."""

import json
import sys
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
    shell,
    write,
)

# Output shapes come from the spike with the real CLI 2.1.316 (.audits/analisis-base/spike-fase1.md).
# Lines are what the adapter reads from `--output-format stream-json`.
PLAIN_LIMIT = "echo 'Claude usage limit reached. Your limit will reset at 7pm'; exit 1"
REJECTED_LIMIT = (
    """echo '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected"}}'\n"""
    """echo '{"type":"result","subtype":"success","is_error":true,"terminal_reason":"api_error","""
    """"api_error_status":null,"result":"x"}'; exit 1"""
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
    """if [ "$1" = auth ]; then echo '{"loggedIn":false,"authMethod":"none"}'; exit 1; fi\n"""
    """echo '{"type":"result","subtype":"success","is_error":true,"terminal_reason":"api_error","""
    """"api_error_status":null,"result":"Not logged in · Please run /login"}'; exit 1"""
)
MAX_TURNS = (
    """echo '{"type":"result","subtype":"error_max_turns","is_error":true,"""
    """"terminal_reason":"max_turns","errors":["Reached maximum number of turns (1)"]}'; exit 1"""
)
DENIALS = (
    """echo '{"type":"result","subtype":"success","is_error":false,"result":"todo ok","""
    """"permission_denials":[{"tool_name":"Bash","tool_use_id":"t1","""
    """"tool_input":{"command":"curl --version"}}]}'"""
)
JSON_LIST = """echo '[{"type":"result"}]'"""

# Stands in for the real CLI's handling of a user hook, as the spike observed it: unless user
# settings are excluded, the hook rewrites the command before the allow list is consulted.
HOOK_AWARE_CLAUDE = """#!{python}
import json, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
sources = "user,project,local"
if "--setting-sources" in args:
    sources = args[args.index("--setting-sources") + 1]
command = "uv run pytest -q"
if "user" in sources.split(","):
    settings = json.loads((Path.home() / ".claude" / "settings.json").read_text())
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    payload = json.dumps({{"tool_input": {{"command": command}}}})
    output = subprocess.run(
        hook, shell=True, input=payload, capture_output=True, text=True, check=True
    ).stdout
    command = json.loads(output)["hookSpecificOutput"]["updatedInput"]["command"]
prefixes = [a[len("Bash("):-len(":*)")] for a in args if a.startswith("Bash(") and a.endswith(":*)")]
denials = []
if any(command.startswith(prefix) for prefix in prefixes):
    Path("executed").write_text(command)
else:
    denials = [{{"tool_name": "Bash", "tool_input": {{"command": command}}}}]
print(json.dumps({{
    "type": "result", "subtype": "success", "is_error": False, "result": "hecho",
    "permission_denials": denials,
}}))
"""
USER_HOOK = """#!/bin/sh
cat >/dev/null
echo '{"hookSpecificOutput":{"updatedInput":{"command":"uv run rtk pytest -q"}}}'
"""


def _executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def _task(tmp_path: Path) -> AgentTask:
    return AgentTask(
        role="implement",
        prompt="p",
        prompt_sha="x",
        cwd=tmp_path,
        model="m-1",
        allowed_tools=("Bash(uv run pytest:*)",),
        max_turns=1,
        timeout_s=5,
        raw_path=tmp_path / "raw.json",
    )


def _run_fake_claude(tmp_path: Path, body: str) -> AgentResult:
    binary = _executable(tmp_path / "claude.sh", f"#!/bin/sh\n{body}\n")
    return ClaudeCLI(cli_binary=str(binary)).run(_task(tmp_path))


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(REJECTED_LIMIT, "rate_limited", id="d19-limite-rechazado"),
        pytest.param(JSON_429, "rate_limited", id="d19-json-429"),
        pytest.param(OK_RC_1, "error", id="d19-json-ok-rc1"),
        pytest.param(FALSE_POSITIVE, "error", id="d19-falso-positivo"),
        pytest.param(LOGIN, "auth", id="d19-login"),
        pytest.param(MAX_TURNS, "max_turns", id="d19-turnos-agotados"),
    ],
)
def test_d19_status_from_cli_output(tmp_path: Path, body: str, expected: str) -> None:
    assert _run_fake_claude(tmp_path, body).status == expected


def test_d19_unstructured_output_keeps_the_raw_message(tmp_path: Path) -> None:
    """Plain text carries no structured limit signal, so it must not be guessed from the words."""
    result = _run_fake_claude(tmp_path, PLAIN_LIMIT)

    assert result.status == "error"
    assert "Claude usage limit reached" in result.text


def test_d19_non_object_json_is_an_error_status(tmp_path: Path) -> None:
    try:
        status = _run_fake_claude(tmp_path, JSON_LIST).status
    except AttributeError as exc:
        status = f"excepción: {exc}"
    assert status == "error"


def test_d19_permission_denials_are_reported(tmp_path: Path) -> None:
    result = _run_fake_claude(tmp_path, DENIALS)
    assert getattr(result, "permission_denials", ()), "las denegaciones se pierden"


def test_user_hook_does_not_alter_agent_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    hook = _executable(tmp_path / "hook.sh", USER_HOOK)
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": str(hook)}]}]}}
    write(home, ".claude/settings.json", json.dumps(settings))
    monkeypatch.setenv("HOME", str(home))
    claude = _executable(tmp_path / "claude.py", HOOK_AWARE_CLAUDE.format(python=sys.executable))
    control = shell([str(claude), "-p", "x", "--allowedTools", "Bash(uv run pytest:*)"], tmp_path)
    require("uv run rtk pytest" in control.stdout, f"el hook debía actuar: {control.stdout}")

    result = ClaudeCLI(cli_binary=str(claude)).run(_task(tmp_path))

    executed = tmp_path / "executed"
    assert executed.is_file(), "el comando se denegó tras reescribirlo el hook"
    assert executed.read_text() == "uv run pytest -q"
    assert result.permission_denials == ()


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
