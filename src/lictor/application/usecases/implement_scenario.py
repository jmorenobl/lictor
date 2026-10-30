"""ImplementScenario use case for executing an implementation attempt for a scenario."""

import contextlib
import hashlib
import importlib.resources
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.usecases.verify_attempt import IntegritySnapshot
from lictor.domain.errors import SealTamperedError
from lictor.domain.model import AgentResult, AgentTask, Event, Scenario

AgentSignal = Literal["ok", "spec_conflict", "rate_limited", "error"]


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


@dataclass(frozen=True)
class ImplementScenarioResult:
    """Result of ImplementScenario use case."""

    agent_result: AgentResult
    agent_signal: AgentSignal
    attempt: int
    prompt_sha: str


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

        if request.attempt > 0 and request.last_error:
            error_block = (
                f"El intento anterior falló en {request.failed_at or 'verificación'}:\n"
                f"{request.last_error}"
            )
        else:
            error_block = ""

        replacements = {
            "{repo}": request.project_root.name,
            "{id}": request.scenario.id.value,
            "{gherkin_del_escenario}": gherkin_text,
            "{writable}": ", ".join(request.paths_writable),
            "{protected}": ", ".join(request.paths_protected),
            "{suppressions}": ", ".join(request.suppressions),
            "{green_ids}": ", ".join(request.green_ids) if request.green_ids else "ninguno",
            "{check_commands}": ", ".join(request.check_commands),
            "{hints}": request.hints,
            "{bloque_de_error}": error_block,
        }
        prompt = raw_template
        for k, v in replacements.items():
            prompt = prompt.replace(k, v)

        lictor_dir = request.project_root / ".lictor"
        runs_dir = lictor_dir / "runs" / request.run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        raw_path = runs_dir / f"{request.attempt + 1:03d}-implement.json"

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

        if not integrity_ok and agent_result.status == "ok":
            agent_result = AgentResult(
                status="error",
                spec_conflict=agent_result.spec_conflict,
                text=agent_result.text,
                session_id=agent_result.session_id,
                num_turns=agent_result.num_turns,
                duration_ms=agent_result.duration_ms,
                tokens=agent_result.tokens,
                cost_usd=agent_result.cost_usd,
                cost_kind=agent_result.cost_kind,
                raw_path=agent_result.raw_path,
            )

        # Normalize signal
        agent_signal: AgentSignal
        if agent_result.status == "rate_limited":
            agent_signal = "rate_limited"
            new_attempt = request.attempt
        elif agent_result.spec_conflict:
            agent_signal = "spec_conflict"
            new_attempt = request.attempt + 1
        elif agent_result.status == "ok":
            agent_signal = "ok"
            new_attempt = request.attempt + 1
        else:
            agent_signal = "error"
            new_attempt = request.attempt + 1

        rel_raw = str(raw_path)
        with contextlib.suppress(ValueError):
            rel_raw = raw_path.relative_to(request.project_root).as_posix()

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
                "model": request.model,
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

        return ImplementScenarioResult(
            agent_result=agent_result,
            agent_signal=agent_signal,
            attempt=new_attempt,
            prompt_sha=prompt_sha,
        )
