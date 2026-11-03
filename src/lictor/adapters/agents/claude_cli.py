"""Claude CLI adapter executing claude -p non-interactively."""

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from lictor.application.ports import CommandRunner
from lictor.domain.model import (
    AgentResult,
    AgentStatus,
    AgentTask,
    CostKind,
    Health,
    PermissionDenial,
    Tokens,
)
from lictor.domain.rules import model_responded

AuthKind = Literal["subscription", "api_key"]
_HTTP_UNAUTHORIZED = 401
_HTTP_TOO_MANY_REQUESTS = 429
_AUTH_CHECK_TIMEOUT_S = 10
_VERSION_TIMEOUT_S = 10
_DEFAULT_BASE_TOOLS = ("Read", "Edit", "Write", "Glob", "Grep")
_DEFAULT_DISALLOWED_TOOLS = (
    "Bash(git:*)",
    "Read(.lictor/**)",
    "Edit(.lictor/**)",
    "Write(.lictor/**)",
)


@dataclass(frozen=True)
class _Stream:
    """What a call's stream of events says, read once."""

    events: tuple[dict[str, Any], ...]
    result: dict[str, Any] | None
    rate_limit_status: str | None
    cli_version: str


class ClaudeCLI:
    """Executes Claude Code CLI commands non-interactively through a command runner."""

    def __init__(
        self,
        runner: CommandRunner,
        auth: AuthKind = "subscription",
        cli_binary: str = "claude",
    ) -> None:
        self.runner = runner
        self.auth: AuthKind = auth
        self.cli_binary: str = cli_binary

    def health(self) -> Health:
        """Verify CLI availability and authentication configuration."""
        res = self.runner.run(
            [self.cli_binary, "--version"], cwd=Path.cwd(), timeout_s=_VERSION_TIMEOUT_S
        )
        if not res.started:
            return Health(
                ok=False,
                cli_version="",
                auth_status="none",
                message=f"No se pudo ejecutar el CLI '{self.cli_binary}': {res.stderr.strip()}",
            )
        if res.timed_out or res.returncode != 0:
            return Health(
                ok=False,
                cli_version="",
                auth_status="error",
                message=f"El CLI devolvió el código {res.returncode}: {res.stderr.strip()}",
            )
        version_str = res.stdout.strip()

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
        res = self.runner.run(
            [self.cli_binary, "auth", "status", "--json"],
            cwd=Path.cwd(),
            timeout_s=_AUTH_CHECK_TIMEOUT_S,
            env=self._prepare_env(),
        )
        try:
            data = json.loads(res.stdout)
        except json.JSONDecodeError:
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

    def _cost_kind(self) -> CostKind:
        return "notional_subscription" if self.auth == "subscription" else "estimated"

    def _read_stream(self, stdout: str) -> _Stream:
        """Read the events of the stream, skipping what isn't one."""
        events: list[dict[str, Any]] = []
        result: dict[str, Any] | None = None
        rate_limit_status: str | None = None
        cli_version = ""
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            events.append(event)
            kind = event.get("type")
            if kind == "result":
                result = event
            elif kind == "rate_limit_event":
                info = event.get("rate_limit_info")
                if isinstance(info, dict) and isinstance(info.get("status"), str):
                    rate_limit_status = info["status"]
            elif kind == "system" and event.get("subtype") == "init":
                cli_version = str(event.get("claude_code_version") or "")
        return _Stream(tuple(events), result, rate_limit_status, cli_version)

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

    def _save_stderr(self, raw_path: Path, stderr: str) -> Path | None:
        """Keep stderr beside the raw stdout, which each call already has for itself."""
        if not stderr:
            return None
        stderr_path = raw_path.with_suffix(".stderr.log")
        stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
        return stderr_path

    def run(self, task: AgentTask) -> AgentResult:
        """Run claude -p for the given task and map the output to AgentResult."""
        out = self.runner.run(
            self._build_command(task),
            cwd=task.cwd,
            timeout_s=task.timeout_s,
            env=self._prepare_env(),
        )

        task.raw_path.parent.mkdir(parents=True, exist_ok=True)
        # Whatever the call left, even cut short by the timeout, is the evidence of what it did.
        task.raw_path.write_text(out.stdout, encoding="utf-8")
        stderr_path = self._save_stderr(task.raw_path, out.stderr)
        stream = self._read_stream(out.stdout)
        common: dict[str, Any] = {
            "duration_ms": out.duration_ms,
            "cost_kind": self._cost_kind(),
            "raw_path": task.raw_path,
            "stderr_path": stderr_path,
            "cli_version": stream.cli_version,
        }
        if out.timed_out:
            return self._timed_out(task, stream, common)

        exit_code = out.returncode
        if stream.result is None:
            return AgentResult(
                status="rate_limited" if stream.rate_limit_status == "rejected" else "error",
                text=(
                    f"El CLI no emitió el evento result (código de salida {exit_code}). "
                    f"Stderr: {out.stderr}\nStdout: {out.stdout}"
                ),
                exit_code=exit_code,
                **common,
            )

        return replace(
            self._parse_agent_data(
                data=stream.result,
                rate_limit_status=stream.rate_limit_status,
                exit_code=exit_code,
                duration_ms=out.duration_ms,
                raw_path=task.raw_path,
            ),
            stderr_path=stderr_path,
            cli_version=stream.cli_version,
        )

    @staticmethod
    def _timed_out(task: AgentTask, stream: _Stream, common: dict[str, Any]) -> AgentResult:
        """A model that wrote something ran out of time; one that never answered is the backend.

        A backend that rejected the call for its rate limit before answering is that limit.
        """
        if model_responded(stream.events):
            return AgentResult(
                status="timeout", text=f"Sin tiempo tras {task.timeout_s} s", **common
            )
        if stream.rate_limit_status == "rejected":
            return AgentResult(
                status="rate_limited",
                text=f"Límite de uso alcanzado; el modelo no respondió en {task.timeout_s} s",
                **common,
            )
        return AgentResult(
            status="error", text=f"El modelo no respondió en {task.timeout_s} s", **common
        )
