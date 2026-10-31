"""Unit tests for ClaudeCLI agent adapter.

Result shapes come from the spike with the real CLI 2.1.316; `auth status` fields too.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lictor.adapters.agents.claude_cli import ClaudeCLI
from lictor.application.ports import Executor
from lictor.domain.model import AgentResult, AgentTask, PermissionDenial, Tokens


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


def _run(tmp_path: Path, stdout: str, returncode: int = 0, stderr: str = "") -> AgentResult:
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (stdout, stderr)
        mock_proc.returncode = returncode
        mock_popen.return_value = mock_proc
        return ClaudeCLI().run(_sample_task(tmp_path, tmp_path / "raw.json"))


def _health_with_session(stdout: str) -> Any:
    version = MagicMock(returncode=0, stdout="2.1.316 (Claude Code)\n", stderr="")
    session = MagicMock(returncode=0, stdout=stdout, stderr="")
    return [version, session]


class TestClaudeCLIHealth:
    def test_implements_executor_protocol(self) -> None:
        cli = ClaudeCLI()
        assert isinstance(cli, Executor)

    def test_health_success(self) -> None:
        cli = ClaudeCLI(auth="subscription")
        session = '{"loggedIn": true, "authMethod": "claude.ai"}'
        with patch("subprocess.run", side_effect=_health_with_session(session)) as mock_run:
            with patch.dict(os.environ, {}, clear=True):
                health = cli.health()
        assert health.ok is True
        assert health.cli_version == "2.1.316 (Claude Code)"
        assert health.auth_status == "subscription"
        assert mock_run.call_args.args[0] == ["claude", "auth", "status", "--json"]

    def test_health_without_session_is_not_ok(self) -> None:
        cli = ClaudeCLI(auth="subscription")
        session = '{"loggedIn": false, "authMethod": "none"}'
        with patch("subprocess.run", side_effect=_health_with_session(session)):
            with patch.dict(os.environ, {}, clear=True):
                health = cli.health()
        assert health.ok is False
        assert health.auth_status == "none"

    @pytest.mark.parametrize("stdout", ["no es json", "[]", '{"loggedIn": "si"}', "{}"])
    def test_health_is_not_ok_when_the_session_is_unknown(self, stdout: str) -> None:
        cli = ClaudeCLI(auth="subscription")
        with patch("subprocess.run", side_effect=_health_with_session(stdout)):
            with patch.dict(os.environ, {}, clear=True):
                health = cli.health()
        assert health.ok is False
        assert health.auth_status == "error"

    def test_health_api_key_auth_does_not_ask_for_a_session(self) -> None:
        cli = ClaudeCLI(auth="api_key")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="2.1.316\n", stderr="")
            health = cli.health()
        assert health.ok is True
        mock_run.assert_called_once()

    def test_health_api_key_override_when_subscription(self) -> None:
        cli = ClaudeCLI(auth="subscription")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="2.1.312\n", stderr="")
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
                health = cli.health()
                assert health.ok is False
                assert health.auth_status == "api_key_override"
                assert "ANTHROPIC_API_KEY" in (health.message or "")

    def test_health_cli_not_found(self) -> None:
        cli = ClaudeCLI(cli_binary="nonexistent-claude-binary")
        health = cli.health()
        assert health.ok is False
        assert health.auth_status == "none"
        assert "not found" in (health.message or "").lower()

    def test_health_subprocess_error(self) -> None:
        cli = ClaudeCLI()
        with patch("subprocess.run", side_effect=subprocess.SubprocessError("exec error")):
            health = cli.health()
            assert health.ok is False
            assert health.auth_status == "error"
            assert "exec error" in (health.message or "")


class TestClaudeCLIRun:
    def test_command_line_and_env_construction(self, tmp_path: Path) -> None:
        cli = ClaudeCLI(auth="subscription")
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

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (json.dumps(sample_output), "")
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret", "OTHER_VAR": "ok"}):
                res = cli.run(task)

            mock_popen.assert_called_once()
            call_args, call_kwargs = mock_popen.call_args
            cmd = call_args[0]
            assert cmd[0] == "claude"
            assert "-p" in cmd
            assert cmd[cmd.index("--output-format") + 1] == "stream-json"
            assert "--verbose" in cmd
            assert "--permission-mode" in cmd
            assert "acceptEdits" in cmd
            assert "--allowedTools" in cmd
            assert "Bash(uv run pytest:*)" in cmd
            assert call_kwargs["start_new_session"] is True

            env = call_kwargs["env"]
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
        cmd = ClaudeCLI()._build_command(_sample_task(tmp_path, tmp_path / "raw.json"))

        start = cmd.index("--disallowedTools") + 1
        denied = cmd[start : cmd.index("--setting-sources")]

        assert denied == [
            "Bash(git:*)",
            "Read(.lictor/**)",
            "Edit(.lictor/**)",
            "Write(.lictor/**)",
        ]

    def test_command_loads_no_user_or_project_configuration(self, tmp_path: Path) -> None:
        cmd = ClaudeCLI()._build_command(_sample_task(tmp_path, tmp_path / "raw.json"))

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
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout=session, stderr="")
            assert _run(tmp_path, _stream(data), returncode=1).status == expected

    def test_failure_other_than_api_error_does_not_ask_for_the_session(
        self, tmp_path: Path
    ) -> None:
        with patch("subprocess.run") as mock_run:
            assert _run(tmp_path, _stream(_result(is_error=True))).status == "error"
        mock_run.assert_not_called()

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

    def test_timeout_handling(self, tmp_path: Path) -> None:
        cli = ClaudeCLI()
        task = _sample_task(tmp_path, tmp_path / "raw.json")

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.communicate.side_effect = [
                subprocess.TimeoutExpired(cmd=["claude"], timeout=60),
                ("", "Process killed due to timeout"),
            ]
            mock_popen.return_value = mock_proc

            with patch("os.killpg") as mock_killpg, patch("os.getpgid", return_value=12345):
                res = cli.run(task)
                mock_killpg.assert_called_once()
                assert res.status == "timeout"
                assert task.raw_path.exists()

    def test_invalid_json_output(self, tmp_path: Path) -> None:
        cli = ClaudeCLI()
        task = _sample_task(tmp_path, tmp_path / "raw.json")

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (
                "Not a JSON output",
                "Fatal error occurred",
            )
            mock_proc.returncode = 7
            mock_popen.return_value = mock_proc

            res = cli.run(task)
            assert res.status == "error"
            assert res.exit_code == 7
            assert "JSON" in res.text
            assert "Not a JSON output" in res.text
            assert "Fatal error occurred" in res.text
            assert task.raw_path.exists()
