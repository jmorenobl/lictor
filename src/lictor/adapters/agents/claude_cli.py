"""Claude CLI adapter executing claude -p non-interactively."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

from lictor.domain.model import (
    AgentResult,
    AgentStatus,
    AgentTask,
    CostKind,
    Health,
    Tokens,
)

AuthKind = Literal["subscription", "api_key"]
_HTTP_TOO_MANY_REQUESTS = 429
_DEFAULT_BASE_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep")
_DEFAULT_DISALLOWED_TOOLS = (
    "Bash(git:*)",
    "Read(.lictor/**)",
    "Edit(.lictor/**)",
    "Write(.lictor/**)",
)


class ClaudeCLI:
    """Executes Claude Code CLI commands non-interactively via subprocess."""

    def __init__(
        self,
        auth: AuthKind = "subscription",
        cli_binary: str = "claude",
    ) -> None:
        self.auth: AuthKind = auth
        self.cli_binary: str = cli_binary

    def health(self) -> Health:
        """Verify CLI availability and authentication configuration."""
        try:
            res = subprocess.run(
                [self.cli_binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if res.returncode != 0:
                return Health(
                    ok=False,
                    cli_version="",
                    auth_status="error",
                    message=f"CLI returned code {res.returncode}: {res.stderr.strip()}",
                )
            version_str = res.stdout.strip()
        except FileNotFoundError:
            return Health(
                ok=False,
                cli_version="",
                auth_status="none",
                message=f"CLI executable '{self.cli_binary}' not found",
            )
        except (subprocess.SubprocessError, OSError) as e:
            return Health(
                ok=False,
                cli_version="",
                auth_status="error",
                message=f"Error checking CLI version: {e}",
            )

        if self.auth == "subscription":
            has_api_var = bool(
                os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            )
            if has_api_var:
                return Health(
                    ok=False,
                    cli_version=version_str,
                    auth_status="api_key_override",
                    message=(
                        "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN present while"
                        " auth is 'subscription'"
                    ),
                )
            return Health(ok=True, cli_version=version_str, auth_status="subscription")

        return Health(ok=True, cli_version=version_str, auth_status="api_key")

    def _build_command(self, task: AgentTask) -> list[str]:
        tools: list[str] = list(_DEFAULT_BASE_TOOLS)
        for tool in task.allowed_tools:
            if tool not in tools:
                tools.append(tool)

        return [
            self.cli_binary,
            "-p",
            task.prompt,
            "--output-format",
            "json",
            "--model",
            task.model,
            "--max-turns",
            str(task.max_turns),
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            *tools,
            "--disallowedTools",
            *_DEFAULT_DISALLOWED_TOOLS,
        ]

    def _prepare_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.auth == "subscription":
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
        env["DISABLE_AUTOUPDATER"] = "1"
        return env

    def _extract_spec_conflict(self, text: str) -> str | None:
        last_conflict: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("LICTOR: SPEC_CONFLICT"):
                reason = stripped[len("LICTOR: SPEC_CONFLICT") :].strip()
                last_conflict = reason if reason else "SPEC_CONFLICT"
        return last_conflict

    def _determine_status(
        self,
        subtype: str,
        is_error: bool,
        api_error_status: Any,
        combined_err_text: str,
    ) -> AgentStatus:
        if subtype == "error_max_turns":
            return "max_turns"
        if subtype == "rate_limited":
            return "rate_limited"

        is_rate_limited = is_error and (
            api_error_status == _HTTP_TOO_MANY_REQUESTS
            or "rate limit" in combined_err_text
            or "usage limit" in combined_err_text
            or "quota exceeded" in combined_err_text
            or "exceeded your quota" in combined_err_text
        )

        if is_rate_limited:
            return "rate_limited"
        if subtype == "error_during_execution":
            return "error"
        if subtype == "success" and not is_error:
            return "ok"
        return "error" if is_error else "ok"

    def _execute_subprocess(
        self,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout_s: int,
    ) -> tuple[str, str, int, bool]:
        start_time = time.monotonic()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(timeout=timeout_s)
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return stdout, stderr, duration_ms, False
        except subprocess.TimeoutExpired:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            _out, err = proc.communicate()
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return "", err, duration_ms, True

    def _parse_agent_data(
        self,
        data: dict[str, Any],
        duration_ms: int,
        cost_kind: CostKind,
        raw_path: Path,
    ) -> AgentResult:
        subtype = str(data.get("subtype", ""))
        is_error = bool(data.get("is_error", False))
        api_error_status = data.get("api_error_status")
        result_text = str(data.get("result", ""))
        combined_err_text = f"{data.get('error', '')} {result_text}".lower()

        status = self._determine_status(subtype, is_error, api_error_status, combined_err_text)
        spec_conflict = self._extract_spec_conflict(result_text)

        usage = data.get("usage") or {}
        tokens = Tokens(
            input=int(usage.get("input_tokens", 0)),
            output=int(usage.get("output_tokens", 0)),
            cache_read=int(usage.get("cache_read_input_tokens", 0)),
            cache_write=int(usage.get("cache_creation_input_tokens", 0)),
        )

        cost_usd = float(data["total_cost_usd"]) if data.get("total_cost_usd") is not None else None
        reported_duration = data.get("duration_ms")
        effective_duration = (
            int(reported_duration) if reported_duration is not None else duration_ms
        )

        session_id = data.get("session_id")
        return AgentResult(
            status=status,
            spec_conflict=spec_conflict,
            text=result_text,
            session_id=str(session_id) if session_id is not None else None,
            num_turns=int(data.get("num_turns", 0)),
            duration_ms=effective_duration,
            tokens=tokens,
            cost_usd=cost_usd,
            cost_kind=cost_kind,
            raw_path=raw_path,
        )

    def run(self, task: AgentTask) -> AgentResult:
        """Run claude -p for the given task and map the output to AgentResult."""
        cmd = self._build_command(task)
        env = self._prepare_env()
        cost_kind: CostKind = (
            "notional_subscription" if self.auth == "subscription" else "estimated"
        )

        stdout, stderr, duration_ms, timed_out = self._execute_subprocess(
            cmd=cmd,
            cwd=task.cwd,
            env=env,
            timeout_s=task.timeout_s,
        )

        task.raw_path.parent.mkdir(parents=True, exist_ok=True)
        if timed_out:
            task.raw_path.write_text(stderr or "TimeoutExpired", encoding="utf-8")
            return AgentResult(
                status="timeout",
                text=stderr or "Process timed out",
                duration_ms=duration_ms,
                cost_kind=cost_kind,
                raw_path=task.raw_path,
            )

        task.raw_path.write_text(stdout, encoding="utf-8")

        try:
            data: dict[str, Any] = json.loads(stdout)
        except json.JSONDecodeError:
            return AgentResult(
                status="error",
                text=f"Invalid JSON from claude CLI. Stderr: {stderr}\nStdout: {stdout}",
                duration_ms=duration_ms,
                cost_kind=cost_kind,
                raw_path=task.raw_path,
            )

        return self._parse_agent_data(
            data=data,
            duration_ms=duration_ms,
            cost_kind=cost_kind,
            raw_path=task.raw_path,
        )
