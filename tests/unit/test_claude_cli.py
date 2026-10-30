"""Unit tests for ClaudeCLI agent adapter."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from lictor.adapters.agents.claude_cli import ClaudeCLI
from lictor.application.ports import Executor
from lictor.domain.model import AgentTask, Tokens


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


class TestClaudeCLIHealth:
    def test_implements_executor_protocol(self) -> None:
        cli = ClaudeCLI()
        assert isinstance(cli, Executor)

    def test_health_success(self) -> None:
        cli = ClaudeCLI(auth="subscription")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="2.1.312 (Claude Code)\n", stderr=""
            )
            with patch.dict(os.environ, {}, clear=True):
                health = cli.health()
                assert health.ok is True
                assert health.cli_version == "2.1.312 (Claude Code)"
                assert health.auth_status == "subscription"

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

        sample_output = {
            "subtype": "success",
            "is_error": False,
            "session_id": "sess-uuid",
            "total_cost_usd": 0.05,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 30,
            },
            "num_turns": 2,
            "duration_ms": 1200,
            "result": "Task completed successfully",
        }

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (json.dumps(sample_output), "")
            mock_popen.return_value = mock_proc

            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret", "OTHER_VAR": "ok"}):
                res = cli.run(task)

            mock_popen.assert_called_once()
            call_args, call_kwargs = mock_popen.call_args
            cmd = call_args[0]
            assert cmd[0] == "claude"
            assert "-p" in cmd
            assert "--output-format" in cmd
            assert "json" in cmd
            assert "--permission-mode" in cmd
            assert "acceptEdits" in cmd
            assert "--allowedTools" in cmd
            assert "Bash(uv run pytest:*)" in cmd
            assert "--disallowedTools" in cmd
            assert "Bash(git:*)" in cmd
            assert call_kwargs["start_new_session"] is True

            env = call_kwargs["env"]
            assert "ANTHROPIC_API_KEY" not in env
            assert env["DISABLE_AUTOUPDATER"] == "1"
            assert env["OTHER_VAR"] == "ok"

            assert res.status == "ok"
            assert res.session_id == "sess-uuid"
            assert res.cost_usd == 0.05
            assert res.cost_kind == "notional_subscription"
            assert res.tokens == Tokens(input=100, output=50, cache_read=200, cache_write=30)
            assert res.num_turns == 2
            assert res.duration_ms == 1200
            assert res.text == "Task completed successfully"
            assert task.raw_path.exists()

    def test_spec_conflict_extraction(self, tmp_path: Path) -> None:
        cli = ClaudeCLI()
        task = _sample_task(tmp_path, tmp_path / "raw.json")

        conflict_msg = (
            "Analizando el problema...\n"
            "LICTOR: SPEC_CONFLICT El escenario exige IVA al 50% que no existe"
        )
        sample_output = {
            "subtype": "success",
            "is_error": False,
            "result": conflict_msg,
        }

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (json.dumps(sample_output), "")
            mock_popen.return_value = mock_proc

            res = cli.run(task)
            assert res.status == "ok"
            assert res.spec_conflict == "El escenario exige IVA al 50% que no existe"

    def test_max_turns_and_errors(self, tmp_path: Path) -> None:
        cli = ClaudeCLI()
        task = _sample_task(tmp_path, tmp_path / "raw.json")

        # error_max_turns
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            data = {
                "subtype": "error_max_turns",
                "is_error": True,
                "result": "Calculando quota de disco",
            }
            mock_proc.communicate.return_value = (json.dumps(data), "")
            mock_popen.return_value = mock_proc
            assert cli.run(task).status == "max_turns"

        # error_during_execution
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            data = {"subtype": "error_during_execution", "is_error": True}
            mock_proc.communicate.return_value = (json.dumps(data), "")
            mock_popen.return_value = mock_proc
            assert cli.run(task).status == "error"

        # rate_limited via 429
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            data = {"is_error": True, "api_error_status": 429}
            mock_proc.communicate.return_value = (json.dumps(data), "")
            mock_popen.return_value = mock_proc
            assert cli.run(task).status == "rate_limited"

        # rate_limited via message
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            data = {
                "is_error": True,
                "result": "You have exceeded your usage limit",
            }
            mock_proc.communicate.return_value = (json.dumps(data), "")
            mock_popen.return_value = mock_proc
            assert cli.run(task).status == "rate_limited"

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
            mock_popen.return_value = mock_proc

            res = cli.run(task)
            assert res.status == "error"
            assert "JSON" in res.text
            assert task.raw_path.exists()
