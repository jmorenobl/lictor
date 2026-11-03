"""Unit tests for ClaudeCLI agent adapter.

Result shapes come from the spike with the real CLI 2.1.316; `auth status` fields too.
`fixtures/claude_stream.jsonl` is a stream of that spike, cut down to a few events.
"""

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from lictor.adapters.agents.claude_cli import ClaudeCLI
from lictor.adapters.runner.fake import FakeRunner
from lictor.adapters.runner.subprocess_runner import SubprocessRunner
from lictor.application.ports import Executor
from lictor.domain.model import AgentResult, AgentTask, CommandOutput, PermissionDenial, Tokens

CAPTURE = Path(__file__).resolve().parents[1] / "fixtures" / "claude_stream.jsonl"


def _sample_task(cwd: Path, raw_path: Path, prompt: str = "Responde OK") -> AgentTask:
    return AgentTask(
        role="implement",
        prompt=prompt,
        prompt_sha="sha256:abc",
        cwd=cwd,
        model="claude-opus-5-5",
        allowed_tools=["Read", "Edit", "Write", "Bash(uv run pytest:*)"],
        max_turns=30,
        timeout_s=1800,
        raw_path=raw_path,
    )


def _stream(*events: dict[str, Any]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def _result(**fields: Any) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "is_error": False, "result": "x", **fields}


def _output(
    stdout: str = "", returncode: int = 0, stderr: str = "", **fields: Any
) -> CommandOutput:
    return CommandOutput(returncode=returncode, stdout=stdout, stderr=stderr, **fields)


def _cli(*outputs: CommandOutput, **kwargs: Any) -> tuple[ClaudeCLI, FakeRunner]:
    runner = FakeRunner()
    for output in outputs:
        runner.queue_response(output)
    return ClaudeCLI(runner, **kwargs), runner


def _run(tmp_path: Path, stdout: str, returncode: int = 0, stderr: str = "") -> AgentResult:
    cli, _ = _cli(_output(stdout, returncode, stderr))
    return cli.run(_sample_task(tmp_path, tmp_path / "raw.json"))


def _health_with_session(stdout: str) -> tuple[CommandOutput, CommandOutput]:
    return _output("2.1.316 (Claude Code)\n"), _output(stdout)


class TestClaudeCLIHealth:
    def test_implements_executor_protocol(self) -> None:
        cli, _ = _cli()
        assert isinstance(cli, Executor)

    def test_health_success(self) -> None:
        cli, runner = _cli(*_health_with_session('{"loggedIn": true, "authMethod": "claude.ai"}'))
        with patch.dict(os.environ, {}, clear=True):
            health = cli.health()
        assert health.ok is True
        assert health.cli_version == "2.1.316 (Claude Code)"
        assert health.auth_status == "subscription"
        assert [call["cmd"] for call in runner.calls] == [
            ["claude", "--version"],
            ["claude", "auth", "status", "--json"],
        ]
        assert [call["timeout_s"] for call in runner.calls] == [10, 10]

    def test_health_without_session_is_not_ok(self) -> None:
        cli, _ = _cli(*_health_with_session('{"loggedIn": false, "authMethod": "none"}'))
        with patch.dict(os.environ, {}, clear=True):
            health = cli.health()
        assert health.ok is False
        assert health.auth_status == "none"

    @pytest.mark.parametrize("stdout", ["no es json", "[]", '{"loggedIn": "si"}', "{}"])
    def test_health_is_not_ok_when_the_session_is_unknown(self, stdout: str) -> None:
        cli, _ = _cli(*_health_with_session(stdout))
        with patch.dict(os.environ, {}, clear=True):
            health = cli.health()
        assert health.ok is False
        assert health.auth_status == "error"

    def test_health_api_key_auth_does_not_ask_for_a_session(self) -> None:
        cli, runner = _cli(_output("2.1.316\n"), auth="api_key")
        health = cli.health()
        assert health.ok is True
        assert len(runner.calls) == 1

    def test_health_api_key_override_when_subscription(self) -> None:
        cli, _ = _cli(_output("2.1.312\n"))
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            health = cli.health()
        assert health.ok is False
        assert health.auth_status == "api_key_override"
        assert "ANTHROPIC_API_KEY" in (health.message or "")

    def test_health_cli_not_found(self) -> None:
        health = ClaudeCLI(SubprocessRunner(), cli_binary="nonexistent-claude-binary").health()
        assert health.ok is False
        assert health.auth_status == "none"
        assert "No se pudo ejecutar el CLI 'nonexistent-claude-binary'" in (health.message or "")

    def test_health_cli_that_fails_is_an_error_not_a_missing_cli(self) -> None:
        cli, _ = _cli(_output(returncode=1, stderr="exec error"))
        health = cli.health()
        assert health.ok is False
        assert health.auth_status == "error"
        assert "exec error" in (health.message or "")

    def test_health_cli_that_hangs_is_an_error(self) -> None:
        cli, _ = _cli(_output(returncode=-9, stderr="Sin tiempo tras 10 s", timed_out=True))
        health = cli.health()
        assert (health.ok, health.auth_status) == (False, "error")


class TestClaudeCLIRun:
    def test_command_line_and_env_construction(self, tmp_path: Path) -> None:
        task = _sample_task(tmp_path, tmp_path / "raw.json")

        sample_output = _result(
            session_id="sess-uuid",
            total_cost_usd=0.05,
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 30,
            },
            num_turns=2,
            duration_ms=1200,
            result="Task completed successfully",
        )

        cli, runner = _cli(_output(json.dumps(sample_output)), auth="subscription")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret", "OTHER_VAR": "ok"}):
            res = cli.run(task)

        assert len(runner.calls) == 1
        call = runner.calls[0]
        cmd = call["cmd"]
        assert isinstance(cmd, list)
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in cmd
        assert "--permission-mode" in cmd
        assert "acceptEdits" in cmd
        assert "--allowedTools" in cmd
        assert "Bash(uv run pytest:*)" in cmd
        assert (call["cwd"], call["timeout_s"]) == (tmp_path, 1800)

        env = call["env"]
        assert isinstance(env, dict)
        assert "ANTHROPIC_API_KEY" not in env
        assert env["DISABLE_AUTOUPDATER"] == "1"
        assert env["OTHER_VAR"] == "ok"

        assert res.status == "ok"
        assert res.exit_code == 0
        assert res.session_id == "sess-uuid"
        assert res.cost_usd == 0.05
        assert res.cost_kind == "notional_subscription"
        assert res.tokens == Tokens(input=100, output=50, cache_read=200, cache_write=30)
        assert res.num_turns == 2
        assert res.duration_ms == 1200
        assert res.text == "Task completed successfully"
        assert task.raw_path.read_text(encoding="utf-8") == json.dumps(sample_output)

    def test_command_denies_every_protected_tool(self, tmp_path: Path) -> None:
        cmd = _cli()[0]._build_command(_sample_task(tmp_path, tmp_path / "raw.json"))

        start = cmd.index("--disallowedTools") + 1
        denied = cmd[start : cmd.index("--setting-sources")]

        assert denied == [
            "Bash(git:*)",
            "Read(.lictor/**)",
            "Edit(.lictor/**)",
            "Write(.lictor/**)",
        ]

    def test_command_loads_no_user_or_project_configuration(self, tmp_path: Path) -> None:
        cmd = _cli()[0]._build_command(_sample_task(tmp_path, tmp_path / "raw.json"))

        assert cmd[cmd.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in cmd
        assert "--no-session-persistence" in cmd
        assert "--bare" not in cmd

    def test_spec_conflict_extraction(self, tmp_path: Path) -> None:
        conflict_msg = (
            "Analizando el problema...\n"
            "LICTOR: SPEC_CONFLICT El escenario exige IVA al 50% que no existe"
        )

        res = _run(tmp_path, _stream(_result(result=conflict_msg)))

        assert res.status == "ok"
        assert res.spec_conflict == "El escenario exige IVA al 50% que no existe"

    def test_max_turns_reports_the_cli_errors(self, tmp_path: Path) -> None:
        data = {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "terminal_reason": "max_turns",
            "errors": ["Reached maximum number of turns (1)"],
        }

        res = _run(tmp_path, _stream(data), returncode=1)

        assert res.status == "max_turns"
        assert res.text == "Reached maximum number of turns (1)"

    def test_error_during_execution(self, tmp_path: Path) -> None:
        data = _result(subtype="error_during_execution", is_error=True)
        assert _run(tmp_path, _stream(data)).status == "error"

    def test_rate_limit_from_status_429(self, tmp_path: Path) -> None:
        data = _result(is_error=True, api_error_status=429)
        assert _run(tmp_path, _stream(data)).status == "rate_limited"

    def test_status_429_on_a_successful_call_keeps_the_work(self, tmp_path: Path) -> None:
        data = _result(api_error_status=429)
        assert _run(tmp_path, _stream(data)).status == "ok"

    def test_status_429_counts_when_only_the_exit_code_failed(self, tmp_path: Path) -> None:
        data = _result(api_error_status=429)
        assert _run(tmp_path, _stream(data), returncode=1).status == "rate_limited"

    def test_rejected_counts_when_only_the_exit_code_failed(self, tmp_path: Path) -> None:
        event = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}
        stdout = _stream(event, _result())
        assert _run(tmp_path, stdout, returncode=1).status == "rate_limited"

    def test_rejected_without_a_result_event_is_a_rate_limit(self, tmp_path: Path) -> None:
        event = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}

        res = _run(tmp_path, _stream(event), returncode=1, stderr="sin cuota")

        assert res.status == "rate_limited"
        assert res.exit_code == 1
        assert "rate_limit_event" in res.text
        assert "sin cuota" in res.text

    def test_allowed_without_a_result_event_stays_an_error(self, tmp_path: Path) -> None:
        event = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}}
        assert _run(tmp_path, _stream(event), returncode=1).status == "error"

    def test_rate_limit_is_not_read_from_the_text(self, tmp_path: Path) -> None:
        data = _result(is_error=True, result="You have exceeded your usage limit")
        assert _run(tmp_path, _stream(data)).status == "error"

    @pytest.mark.parametrize(
        ("limit_status", "is_error", "expected"),
        [
            ("rejected", True, "rate_limited"),
            ("rejected", False, "ok"),
            ("allowed_warning", True, "error"),
            ("allowed", True, "error"),
        ],
    )
    def test_rate_limit_event_only_counts_on_a_failed_call(
        self, tmp_path: Path, limit_status: str, is_error: bool, expected: str
    ) -> None:
        event = {"type": "rate_limit_event", "rate_limit_info": {"status": limit_status}}
        stdout = _stream(event, _result(is_error=is_error))
        assert _run(tmp_path, stdout).status == expected

    def test_only_the_last_rate_limit_event_counts(self, tmp_path: Path) -> None:
        rejected = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}
        allowed = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}
        stdout = _stream(rejected, allowed, _result(is_error=True))
        assert _run(tmp_path, stdout).status == "error"

    @pytest.mark.parametrize("info", [None, {}, {"status": 1}])
    def test_malformed_rate_limit_event_is_ignored(self, tmp_path: Path, info: Any) -> None:
        stdout = _stream({"type": "rate_limit_event", "rate_limit_info": info}, _result())
        assert _run(tmp_path, stdout).status == "ok"

    def test_auth_from_status_401(self, tmp_path: Path) -> None:
        data = _result(is_error=True, api_error_status=401)
        assert _run(tmp_path, _stream(data), returncode=1).status == "auth"

    @pytest.mark.parametrize(
        ("session", "expected"),
        [('{"loggedIn": false}', "auth"), ('{"loggedIn": true}', "error"), ("roto", "error")],
    )
    def test_api_error_asks_the_cli_for_the_session(
        self, tmp_path: Path, session: str, expected: str
    ) -> None:
        data = _result(is_error=True, terminal_reason="api_error", api_error_status=404)
        cli, runner = _cli(_output(_stream(data), 1), _output(session, 1))

        assert cli.run(_sample_task(tmp_path, tmp_path / "raw.json")).status == expected
        assert runner.calls[1]["cmd"] == ["claude", "auth", "status", "--json"]

    def test_failure_other_than_api_error_does_not_ask_for_the_session(
        self, tmp_path: Path
    ) -> None:
        cli, runner = _cli(_output(_stream(_result(is_error=True))))

        assert cli.run(_sample_task(tmp_path, tmp_path / "raw.json")).status == "error"
        assert len(runner.calls) == 1

    def test_exit_code_overrides_a_successful_result(self, tmp_path: Path) -> None:
        res = _run(tmp_path, _stream(_result()), returncode=3)
        assert res.status == "error"
        assert res.exit_code == 3

    def test_noise_in_the_stream_is_skipped(self, tmp_path: Path) -> None:
        stdout = "ruido\n[1, 2]\n" + _stream({"type": "system", "subtype": "init"}, _result())
        assert _run(tmp_path, stdout).status == "ok"

    def test_permission_denials_keep_tool_and_target(self, tmp_path: Path) -> None:
        denials = [
            {"tool_name": "Bash", "tool_use_id": "1", "tool_input": {"command": "curl -V"}},
            {"tool_name": "Read", "tool_use_id": "2", "tool_input": {"file_path": "/a/b"}},
            {"tool_name": "Task"},
            "forma desconocida",
        ]

        res = _run(tmp_path, _stream(_result(permission_denials=denials)))

        assert res.status == "ok"
        assert res.permission_denials == (
            PermissionDenial(tool="Bash", target="curl -V"),
            PermissionDenial(tool="Read", target="/a/b"),
            PermissionDenial(tool="Task"),
            PermissionDenial(tool="", target="forma desconocida"),
        )

    @pytest.mark.parametrize("denials", [None, "x", {}])
    def test_malformed_denials_are_ignored(self, tmp_path: Path, denials: Any) -> None:
        res = _run(tmp_path, _stream(_result(permission_denials=denials)))
        assert res.permission_denials == ()

    def test_timeout_after_the_model_answered_keeps_the_partial_stream(
        self, tmp_path: Path
    ) -> None:
        partial = "\n".join(CAPTURE.read_text(encoding="utf-8").splitlines()[:5]) + "\n"
        cli, _ = _cli(_output(partial, -9, "Sin tiempo tras 1800 s", timed_out=True))
        task = _sample_task(tmp_path, tmp_path / "raw.json")

        res = cli.run(task)

        assert (res.status, res.text) == ("timeout", "Sin tiempo tras 1800 s")
        assert task.raw_path.read_text(encoding="utf-8") == partial
        assert res.stderr_path == tmp_path / "raw.stderr.log"
        assert res.cli_version == "2.1.316"
        assert res.exit_code is None

    @pytest.mark.parametrize(
        "events",
        [
            [],
            [{"type": "system", "subtype": "init", "claude_code_version": "2.1.316"}],
            [{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}],
            [{"type": "assistant", "message": {"model": "<synthetic>", "content": []}}],
        ],
    )
    def test_timeout_before_the_model_answered_is_an_error(
        self, tmp_path: Path, events: list[dict[str, Any]]
    ) -> None:
        cli, _ = _cli(_output(_stream(*events), -9, "", timed_out=True))

        res = cli.run(_sample_task(tmp_path, tmp_path / "raw.json"))

        assert (res.status, res.text) == ("error", "El modelo no respondió en 1800 s")

    def test_timeout_after_a_rejected_rate_limit_is_a_rate_limit(self, tmp_path: Path) -> None:
        events = [
            {"type": "system", "subtype": "init", "claude_code_version": "2.1.316"},
            {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}},
        ]
        cli, _ = _cli(_output(_stream(*events), -9, "", timed_out=True))

        res = cli.run(_sample_task(tmp_path, tmp_path / "raw.json"))

        assert res.status == "rate_limited"
        assert res.text == "Límite de uso alcanzado; el modelo no respondió en 1800 s"

    def test_timeout_after_the_model_answered_is_a_timeout_even_if_rejected(
        self, tmp_path: Path
    ) -> None:
        events = [
            {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}},
            {"type": "assistant", "message": {"model": "claude-opus-5-5", "content": []}},
        ]
        cli, _ = _cli(_output(_stream(*events), -9, "", timed_out=True))

        assert cli.run(_sample_task(tmp_path, tmp_path / "raw.json")).status == "timeout"

    def test_a_real_stream_is_read_whole(self, tmp_path: Path) -> None:
        res = _run(tmp_path, CAPTURE.read_text(encoding="utf-8"))

        assert res.status == "ok"
        assert res.cli_version == "2.1.316"
        assert res.num_turns > 0
        assert res.tokens.output > 0

    def test_stderr_is_kept_beside_the_raw_output_without_a_result_event(
        self, tmp_path: Path
    ) -> None:
        res = _run(tmp_path, "salida cruda", returncode=7, stderr="Fatal: se cayó")

        assert res.stderr_path == tmp_path / "raw.stderr.log"
        assert res.stderr_path.read_text(encoding="utf-8") == "Fatal: se cayó"
        assert (tmp_path / "raw.json").read_text(encoding="utf-8") == "salida cruda"

    def test_stderr_is_kept_with_a_result_event(self, tmp_path: Path) -> None:
        res = _run(tmp_path, _stream(_result()), stderr="aviso")

        assert res.stderr_path == tmp_path / "raw.stderr.log"

    def test_no_stderr_leaves_no_file(self, tmp_path: Path) -> None:
        res = _run(tmp_path, _stream(_result()))

        assert res.stderr_path is None
        assert not (tmp_path / "raw.stderr.log").exists()

    def test_each_call_keeps_its_own_stderr(self, tmp_path: Path) -> None:
        cli, _ = _cli(_output("", 1, "primero"), _output("", 1, "segundo"))
        first = cli.run(_sample_task(tmp_path, tmp_path / "001-implement.json"))
        second = cli.run(_sample_task(tmp_path, tmp_path / "001-implement-r1.json"))

        assert first.stderr_path is not None
        assert second.stderr_path is not None
        assert first.stderr_path.read_text(encoding="utf-8") == "primero"
        assert second.stderr_path.read_text(encoding="utf-8") == "segundo"

    def test_invalid_json_output(self, tmp_path: Path) -> None:
        task = _sample_task(tmp_path, tmp_path / "raw.json")
        cli, _ = _cli(_output("Not a JSON output", 7, "Fatal error occurred"))

        res = cli.run(task)

        assert res.status == "error"
        assert res.exit_code == 7
        assert "El CLI no emitió el evento result (código de salida 7)" in res.text
        assert "Not a JSON output" in res.text
        assert "Fatal error occurred" in res.text
        assert task.raw_path.exists()


ORPHAN_CLAUDE = """#!{python}
import os, subprocess, sys
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(6)"], start_new_session=True)
os.write(1, open({capture!r}, "rb").read().replace(b'"result":"', b'"result":"\\xff', 1))
"""


def test_a_real_process_with_bad_bytes_and_an_orphan_does_not_hang_the_call(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "claude.py"
    binary.write_text(ORPHAN_CLAUDE.format(python=sys.executable, capture=str(CAPTURE)))
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    task = _sample_task(tmp_path, tmp_path / "raw.json")
    started = time.monotonic()

    res = ClaudeCLI(SubprocessRunner(), cli_binary=str(binary)).run(task)

    assert time.monotonic() - started < 4
    assert res.status == "ok"
    assert res.text.startswith("\ufffd")
    assert res.cli_version == "2.1.316"
