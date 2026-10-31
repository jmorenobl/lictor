"""ImplementScenario use case for executing an implementation attempt for a scenario."""

import contextlib
import hashlib
import importlib.resources
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.usecases.agent_events import denial_event, denial_targets
from lictor.application.usecases.verify_attempt import IntegritySnapshot
from lictor.domain.errors import SealTamperedError
from lictor.domain.model import AgentResult, AgentStatus, AgentTask, Event, Scenario
from lictor.domain.rules import count_agent_failures, is_agent_unavailable

AgentSignal = Literal["ok", "spec_conflict", "rate_limited", "unavailable", "error"]

_STOPPED_EARLY_NOTES: dict[str, str] = {
    "max_turns": "El intento anterior se quedó sin turnos antes de terminar.",
    "timeout": "El intento anterior se quedó sin tiempo antes de terminar.",
}


@dataclass(frozen=True)
class ImplementScenarioRequest:
    """Request payload for ImplementScenario use case."""

    project_root: Path
    run_id: str
    scenario: Scenario
    attempt: int
    model: str
    allowed_tools: Sequence[str]
    max_turns: int
    timeout_s: int
    paths_writable: Sequence[str]
    paths_protected: Sequence[str]
    paths_driver: Sequence[str]
    suppressions: Sequence[str]
    green_ids: Sequence[str]
    check_commands: Sequence[str]
    hints: str
    last_error: str | None = None
    failed_at: str | None = None
    prompt_template: str | None = None
    agent_failures: int = 0
    last_agent_status: AgentStatus | None = None


@dataclass(frozen=True)
class ImplementScenarioResult:
    """Result of ImplementScenario use case."""

    agent_result: AgentResult
    agent_signal: AgentSignal
    attempt: int
    prompt_sha: str
    agent_failures: int = 0


def _extract_background(lines: Sequence[str]) -> str:
    bg_lines: list[str] = []
    in_bg = False
    for line in lines:
        stripped = line.strip().lower()
        if (
            stripped.startswith("background:")
            or stripped.startswith("antecedentes:")
            or stripped in {"background", "antecedentes"}
        ):
            in_bg = True
            bg_lines.append(line)
        elif in_bg:
            if (
                stripped.startswith("@")
                or stripped.startswith("scenario:")
                or stripped.startswith("escenario:")
            ):
                break
            bg_lines.append(line)
    return "\n".join(bg_lines).strip()


def _find_tag_start_index(lines: Sequence[str], target_tag: str, scen_line_idx: int) -> int:
    for idx in range(scen_line_idx, -1, -1):
        line_str = lines[idx].strip()
        if target_tag in line_str:
            return idx
        is_scen = line_str.startswith("Scenario:") or line_str.startswith("Escenario:")
        if is_scen and idx != scen_line_idx:
            break
    return scen_line_idx


def _extract_scenario_body(lines: Sequence[str], target_tag: str, start_idx: int) -> str:
    scen_lines: list[str] = []
    started = False
    seen_header = False
    for line in lines[start_idx:]:
        stripped = line.strip()
        if target_tag in stripped:
            started = True
        elif started:
            is_header = stripped.startswith("Scenario:") or stripped.startswith("Escenario:")
            if is_header and seen_header:
                break
            if is_header:
                seen_header = True
            elif stripped.startswith("@") and not stripped.startswith(target_tag):
                break
        if started:
            scen_lines.append(line)
    return "\n".join(scen_lines).strip()


def _extract_scenario_gherkin(project_root: Path, scenario: Scenario) -> str:
    path = Path(scenario.file)
    if not path.is_absolute():
        path = project_root / path
    if not path.is_file():
        return f"Scenario: {scenario.name} (@{scenario.id.value})"

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return ""

    bg_text = _extract_background(lines)
    target_tag = f"@{scenario.id.value}"
    scen_line_idx = scenario.line - 1 if 0 < scenario.line <= len(lines) else 0
    start_idx = _find_tag_start_index(lines, target_tag, scen_line_idx)
    scen_text = _extract_scenario_body(lines, target_tag, start_idx)

    if bg_text and scen_text:
        return f"{bg_text}\n\n{scen_text}"
    if scen_text:
        return scen_text
    return "\n".join(lines)


def _extract_cli_version(executor: Executor) -> str:
    health_fn = getattr(executor, "health", None)
    if callable(health_fn):
        with contextlib.suppress(Exception):
            return str(health_fn().cli_version)
    return ""


def _classify_agent(result: AgentResult, agent_failures: int) -> AgentSignal:
    if result.status == "rate_limited":
        return "rate_limited"
    if is_agent_unavailable(result.status, agent_failures):
        return "unavailable"
    if result.spec_conflict:
        return "spec_conflict"
    return "ok" if result.status == "ok" else "error"


class ImplementScenario:
    """Use case to execute an implementation attempt on a single scenario."""

    def __init__(
        self,
        vcs: Vcs,
        executor: Executor,
        spec_source: SpecSource,
        event_log: EventLog,
        clock: Clock,
    ) -> None:
        self.vcs = vcs
        self.executor = executor
        self.spec_source = spec_source
        self.event_log = event_log
        self.clock = clock

    def execute(self, request: ImplementScenarioRequest) -> ImplementScenarioResult:
        """Verify seal, reset repo, invoke agent with integrity snapshot, and emit CloudEvent."""
        # Check seal first: if modified, terminate without resetting working tree
        if not self.spec_source.verify_seal(request.project_root, request.paths_driver):
            raise SealTamperedError(
                "La especificación o el driver cambiaron fuera de Lictor. "
                "Edición manual detectada; ejecución terminada sin resetear."
            )

        self.vcs.reset_to_head()

        raw_template = (
            request.prompt_template
            if request.prompt_template is not None
            else importlib.resources.files("lictor.prompts")
            .joinpath("implement.md")
            .read_text(encoding="utf-8")
        )
        prompt_sha = f"sha256:{hashlib.sha256(raw_template.encode('utf-8')).hexdigest()}"

        gherkin_text = _extract_scenario_gherkin(request.project_root, request.scenario)

        error_block = _STOPPED_EARLY_NOTES.get(request.last_agent_status or "", "")
        if request.last_error:
            failure = (
                f"El intento anterior falló en {request.failed_at or 'verificación'}:\n"
                f"{request.last_error}"
            )
            error_block = f"{error_block}\n{failure}".strip()

        replacements = {
            "{repo}": request.project_root.name,
            "{id}": request.scenario.id.value,
            "{gherkin_del_escenario}": gherkin_text,
            "{writable}": ", ".join(request.paths_writable),
            "{protected}": ", ".join(request.paths_protected),
            "{suppressions}": ", ".join(request.suppressions),
            "{green_ids}": ", ".join(request.green_ids) if request.green_ids else "ninguno",
            "{check_commands}": ", ".join(request.check_commands) or "los tests del proyecto",
            "{hints}": request.hints,
            "{bloque_de_error}": error_block,
        }
        prompt = raw_template
        for k, v in replacements.items():
            prompt = prompt.replace(k, v)

        lictor_dir = request.project_root / ".lictor"
        runs_dir = lictor_dir / "runs" / request.run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        # A failed call doesn't use up the attempt: the next one must not overwrite its evidence.
        retry = f"-r{request.agent_failures}" if request.agent_failures else ""
        raw_path = runs_dir / f"{request.attempt + 1:03d}-implement{retry}.json"

        task = AgentTask(
            role="implement",
            prompt=prompt,
            prompt_sha=prompt_sha,
            cwd=request.project_root,
            model=request.model,
            allowed_tools=request.allowed_tools,
            max_turns=request.max_turns,
            timeout_s=request.timeout_s,
            raw_path=raw_path,
        )

        snapshot = IntegritySnapshot(lictor_dir, request.project_root)
        agent_result = self.executor.run(task)
        integrity_ok = snapshot.verify_and_restore()

        agent_failures = count_agent_failures(agent_result.status, request.agent_failures)
        if not integrity_ok and agent_result.status == "ok":
            agent_result = replace(agent_result, status="error")

        agent_signal = _classify_agent(agent_result, agent_failures)
        keeps_attempt = agent_signal in ("rate_limited", "unavailable") or (
            agent_signal == "error" and agent_failures > 0
        )
        new_attempt = request.attempt if keeps_attempt else request.attempt + 1

        rel_raw = str(raw_path)
        with contextlib.suppress(ValueError):
            rel_raw = raw_path.relative_to(request.project_root).as_posix()

        cli_version = _extract_cli_version(self.executor)

        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.agent.finished.v1",
            source="lictor/0.1.0",
            time=self.clock.now_iso(),
            runid=request.run_id,
            scenarioid=request.scenario.id.value,
            data={
                "role": "implement",
                "attempt": new_attempt,
                "status": agent_result.status,
                "signal": agent_signal,
                "exit_code": agent_result.exit_code,
                "permission_denials": denial_targets(agent_result),
                "model": request.model,
                "cli_version": cli_version,
                "tokens": {
                    "input": agent_result.tokens.input,
                    "output": agent_result.tokens.output,
                    "cache_read": agent_result.tokens.cache_read,
                    "cache_write": agent_result.tokens.cache_write,
                },
                "cost_usd": agent_result.cost_usd or 0.0,
                "cost_kind": agent_result.cost_kind,
                "num_turns": agent_result.num_turns,
                "duration_ms": agent_result.duration_ms,
                "prompt_sha": prompt_sha,
                "raw": rel_raw,
            },
        )
        self.event_log.append(event)
        if agent_result.permission_denials:
            self.event_log.append(denial_event(agent_result, event))

        return ImplementScenarioResult(
            agent_result=agent_result,
            agent_signal=agent_signal,
            attempt=new_attempt,
            prompt_sha=prompt_sha,
            agent_failures=agent_failures,
        )
