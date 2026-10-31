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
    PermissionDenial,
    Tokens,
)

AuthKind = Literal["subscription", "api_key"]
_HTTP_UNAUTHORIZED = 401
_HTTP_TOO_MANY_REQUESTS = 429
_AUTH_CHECK_TIMEOUT_S = 10
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
            return self._subscription_health(version_str)

        return Health(ok=True, cli_version=version_str, auth_status="api_key")

    def _subscription_health(self, version: str) -> Health:
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return Health(
                ok=False,
                cli_version=version,
                auth_status="api_key_override",
                message=(
                    "ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN present while auth is 'subscription'"
                ),
            )
        logged_in = self._logged_in()
        if logged_in is None:
            return Health(
                ok=False,
                cli_version=version,
                auth_status="error",
                message="No se pudo comprobar la sesión del CLI",
            )
        if not logged_in:
            return Health(
                ok=False,
                cli_version=version,
                auth_status="none",
                message="El CLI no tiene sesión iniciada",
            )
        return Health(ok=True, cli_version=version, auth_status="subscription")

    def _logged_in(self) -> bool | None:
        """Ask the CLI for its session, which costs no model call; None if it can't tell."""
        try:
            res = subprocess.run(
                [self.cli_binary, "auth", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=_AUTH_CHECK_TIMEOUT_S,
                check=False,
                env=self._prepare_env(),
            )
            data = json.loads(res.stdout)
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
            return None
        logged_in = data.get("loggedIn") if isinstance(data, dict) else None
        return logged_in if isinstance(logged_in, bool) else None

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
            "stream-json",
            "--verbose",
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
            # No user, project or local settings: their hooks, permissions and CLAUDE.md would
            # make the run depend on the machine, and the agent can write the project's ones.
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
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

    def _is_auth_failure(self, data: dict[str, Any]) -> bool:
        if data.get("api_error_status") == _HTTP_UNAUTHORIZED:
            return True
        return data.get("terminal_reason") == "api_error" and self._logged_in() is False

    def _determine_status(
        self,
        data: dict[str, Any],
        rate_limit_status: str | None,
        exit_code: int,
    ) -> AgentStatus:
        subtype = str(data.get("subtype", ""))
        is_error = bool(data.get("is_error", False))
        failed = is_error or exit_code != 0 or subtype == "error_during_execution"

        if subtype == "error_max_turns":
            return "max_turns"
        if subtype == "rate_limited":
            return "rate_limited"
        if failed and data.get("api_error_status") == _HTTP_TOO_MANY_REQUESTS:
            return "rate_limited"
        if failed and rate_limit_status == "rejected":
            return "rate_limited"
        if is_error and self._is_auth_failure(data):
            return "auth"
        return "error" if failed else "ok"

    def _execute_subprocess(
        self,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout_s: int,
    ) -> tuple[str, str, int | None, int, bool]:
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
            return stdout, stderr, proc.returncode, duration_ms, False
        except subprocess.TimeoutExpired:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            _out, err = proc.communicate()
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return "", err, None, duration_ms, True

    def _cost_kind(self) -> CostKind:
        return "notional_subscription" if self.auth == "subscription" else "estimated"

    def _read_events(self, stdout: str) -> tuple[dict[str, Any] | None, str | None]:
        """Return the result event and the last rate limit status from the stream."""
        result: dict[str, Any] | None = None
        rate_limit_status: str | None = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "result":
                result = event
            elif event.get("type") == "rate_limit_event":
                info = event.get("rate_limit_info")
                if isinstance(info, dict) and isinstance(info.get("status"), str):
                    rate_limit_status = info["status"]
        return result, rate_limit_status

    def _read_denials(self, data: dict[str, Any]) -> tuple[PermissionDenial, ...]:
        denials = data.get("permission_denials")
        if not isinstance(denials, list):
            return ()
        parsed: list[PermissionDenial] = []
        for denial in denials:
            if not isinstance(denial, dict):
                parsed.append(PermissionDenial(tool="", target=str(denial)))
                continue
            tool_input = denial.get("tool_input")
            fields = tool_input if isinstance(tool_input, dict) else {}
            target = fields.get("command") or fields.get("file_path") or ""
            parsed.append(
                PermissionDenial(tool=str(denial.get("tool_name", "")), target=str(target))
            )
        return tuple(parsed)

    def _parse_agent_data(
        self,
        data: dict[str, Any],
        rate_limit_status: str | None,
        exit_code: int,
        duration_ms: int,
        raw_path: Path,
    ) -> AgentResult:
        # error_max_turns has no `result`, only an `errors` list.
        errors = data.get("errors")
        fallback = "; ".join(str(e) for e in errors) if isinstance(errors, list) else ""
        result_text = str(data.get("result") or fallback)

        status = self._determine_status(data, rate_limit_status, exit_code)
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
            cost_kind=self._cost_kind(),
            raw_path=raw_path,
            permission_denials=self._read_denials(data),
            exit_code=exit_code,
        )

    def run(self, task: AgentTask) -> AgentResult:
        """Run claude -p for the given task and map the output to AgentResult."""
        cmd = self._build_command(task)
        env = self._prepare_env()
        cost_kind = self._cost_kind()

        stdout, stderr, exit_code, duration_ms, timed_out = self._execute_subprocess(
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

        data, rate_limit_status = self._read_events(stdout)
        if data is None:
            return AgentResult(
                status="rate_limited" if rate_limit_status == "rejected" else "error",
                text=(
                    f"No result event from claude CLI (exit code {exit_code}). "
                    f"Stderr: {stderr}\nStdout: {stdout}"
                ),
                duration_ms=duration_ms,
                cost_kind=cost_kind,
                raw_path=task.raw_path,
                exit_code=exit_code,
            )

        return self._parse_agent_data(
            data=data,
            rate_limit_status=rate_limit_status,
            exit_code=exit_code or 0,
            duration_ms=duration_ms,
            raw_path=task.raw_path,
        )
