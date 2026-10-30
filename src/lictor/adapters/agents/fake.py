"""Deterministic FakeExecutor adapter for testing without LLM quota."""

import json
import re
from pathlib import Path
from typing import Any, Literal

from lictor.domain.model import (
    AgentResult,
    AgentRole,
    AgentStatus,
    AgentTask,
    Health,
    Tokens,
)

FakeMode = Literal["good", "fail", "cheat", "conflict", "rate_limit", "crash"]
_SCENARIO_TAG_RE = re.compile(r"([A-Z][A-Z0-9]*-[0-9]+)")


class FakeExecutor:
    """Deterministic agent test double simulating LLM runs with configured scripts and modes."""

    def __init__(  # noqa: PLR0913
        self,
        mode: FakeMode = "good",
        *,
        scenario_modes: dict[str, FakeMode] | None = None,
        fixtures_dir: Path | None = None,
        files_to_write: dict[str, dict[str, str]] | None = None,
        crash_on_role: AgentRole | None = None,
        crash_on_scenario: str | None = None,
        tokens_per_call: Tokens | None = None,
        cost_per_call: float = 0.005,
        duration_ms: int = 150,
    ) -> None:
        self.default_mode = mode
        self.scenario_modes = scenario_modes or {}
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else None
        self.files_to_write = files_to_write or {}
        self.crash_on_role = crash_on_role
        self.crash_on_scenario = crash_on_scenario
        self.tokens_per_call = tokens_per_call or Tokens(
            input=100, output=50, cache_read=200, cache_write=20
        )
        self.cost_per_call = cost_per_call
        self.duration_ms = duration_ms

    def health(self) -> Health:
        """Return healthy fake status."""
        return Health(
            ok=True,
            cli_version="fake-1.0.0",
            auth_status="subscription",
            message=None,
        )

    def _detect_scenario_id(self, prompt: str) -> str | None:
        match = _SCENARIO_TAG_RE.search(prompt)
        return match.group(1) if match else None

    def _apply_filesystem_effects(self, mode: FakeMode, cwd: Path) -> None:
        mode_files = self.files_to_write.get(mode, {})
        for rel_path, content in mode_files.items():
            dest = cwd / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        if self.fixtures_dir:
            src_mode_dir = self.fixtures_dir / mode
            if src_mode_dir.is_dir():
                for p in src_mode_dir.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(src_mode_dir)
                        dest = cwd / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(p.read_bytes())

    def _build_mode_outcome(self, mode: FakeMode) -> tuple[AgentStatus, str | None, str]:
        if mode == "conflict":
            spec_conflict = "Escenario imposible o contradictorio con la especificación"
            return "ok", spec_conflict, f"Analizando...\nLICTOR: SPEC_CONFLICT {spec_conflict}"
        if mode == "rate_limit":
            return "rate_limited", None, "API rate limit reached"
        return "ok", None, "Fake execution completed"

    def run(self, task: AgentTask) -> AgentResult:
        """Execute a deterministic fake agent run according to active mode."""
        scenario_id = self._detect_scenario_id(task.prompt)
        mode: FakeMode = self.default_mode
        if scenario_id and scenario_id in self.scenario_modes:
            mode = self.scenario_modes[scenario_id]

        if self.crash_on_role and task.role == self.crash_on_role:
            raise RuntimeError(f"Simulated FakeExecutor crash on role {task.role}")

        if self.crash_on_scenario and scenario_id and scenario_id == self.crash_on_scenario:
            raise RuntimeError(f"Simulated FakeExecutor crash on scenario {scenario_id}")

        if mode == "crash":
            raise RuntimeError("Simulated FakeExecutor crash")

        self._apply_filesystem_effects(mode, task.cwd)
        status, spec_conflict, result_text = self._build_mode_outcome(mode)

        tokens = self.tokens_per_call if status != "rate_limited" else Tokens()
        cost = self.cost_per_call if status != "rate_limited" else 0.0

        raw_data: dict[str, Any] = {
            "mode": mode,
            "status": status,
            "spec_conflict": spec_conflict,
            "result": result_text,
            "num_turns": 1 if status != "rate_limited" else 0,
            "duration_ms": self.duration_ms,
            "usage": {
                "input_tokens": tokens.input,
                "output_tokens": tokens.output,
                "cache_read_input_tokens": tokens.cache_read,
                "cache_creation_input_tokens": tokens.cache_write,
            },
            "total_cost_usd": cost,
        }

        task.raw_path.parent.mkdir(parents=True, exist_ok=True)
        task.raw_path.write_text(json.dumps(raw_data, indent=2), encoding="utf-8")

        return AgentResult(
            status=status,
            spec_conflict=spec_conflict,
            text=result_text,
            session_id="fake-session-id",
            num_turns=1 if status != "rate_limited" else 0,
            duration_ms=self.duration_ms,
            tokens=tokens,
            cost_usd=cost,
            cost_kind="notional_subscription",
            raw_path=task.raw_path,
        )
