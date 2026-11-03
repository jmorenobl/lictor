"""ImplementScenario use case for executing an implementation attempt for a scenario."""

import hashlib
import importlib.resources
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.run_files import (
    agent_workspace,
    attempt_prefix,
    unused_path,
)
from lictor.application.usecases.agent_events import (
    AgentCall,
    agent_finished_event,
    denial_event,
    tamper_event,
)
from lictor.application.usecases.verify_attempt import (
    AgentCopyOutcome,
    IntegritySnapshot,
)
from lictor.domain.errors import CandidateError, SealTamperedError
from lictor.domain.model import AgentResult, AgentSignal, AgentStatus, AgentTask, Scenario
from lictor.domain.rules import classify_agent, count_agent_failures, keeps_attempt

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
    attempt_slot: int = 1
    tree: str | None = None
    failed_at: str | None = None
    error: str | None = None


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
        """Verify seal, invoke agent on a copy of HEAD with integrity snapshot, read its result."""
        if not self.spec_source.verify_seal(request.project_root, request.paths_driver):
            raise SealTamperedError(
                "La especificación o el driver cambiaron fuera de Lictor. "
                "Edición manual detectada; ejecución terminada."
            )

        raw_template = (
            request.prompt_template
            if request.prompt_template is not None
            else importlib.resources.files("lictor.prompts")
            .joinpath("implement.md")
            .read_text(encoding="utf-8")
        )
        prompt_sha = f"sha256:{hashlib.sha256(raw_template.encode('utf-8')).hexdigest()}"

        gherkin_text = self.spec_source.scenario_text(request.project_root, request.scenario)

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

        prefix = attempt_prefix(
            request.project_root, request.run_id, request.scenario.id.value, request.attempt + 1
        )
        prefix.parent.mkdir(parents=True, exist_ok=True)
        # A call that doesn't use up the attempt must not overwrite the evidence of the last one.
        raw_path = unused_path(
            prefix.with_name(f"{prefix.name}-implement.json"), request.agent_failures
        )

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
        outcome = self._run_in_copy(task, request.project_root)
        agent_result = outcome.agent_result

        agent_failures = count_agent_failures(agent_result.status, request.agent_failures)
        agent_signal = classify_agent(
            agent_result.status,
            agent_failures,
            spec_conflict=bool(agent_result.spec_conflict),
            tampered=bool(outcome.tampered),
            unreadable=bool(outcome.unreadable),
        )
        keeps = keeps_attempt(agent_signal, agent_failures)
        new_attempt = request.attempt if keeps else request.attempt + 1

        call = AgentCall(
            run_id=request.run_id,
            scenario_id=request.scenario.id.value,
            role="implement",
            attempt=new_attempt,
            signal=agent_signal,
            model=request.model,
            cli_version=agent_result.cli_version,
            prompt_sha=prompt_sha,
            raw_path=raw_path,
            project_root=request.project_root,
        )
        event = agent_finished_event(self.clock, call, agent_result)
        self.event_log.append(event)
        if agent_result.permission_denials:
            self.event_log.append(denial_event(agent_result, event))
        if outcome.tampered:
            self.event_log.append(tamper_event(event, outcome.tampered))

        return ImplementScenarioResult(
            agent_result=agent_result,
            agent_signal=agent_signal,
            attempt=new_attempt,
            prompt_sha=prompt_sha,
            agent_failures=agent_failures,
            attempt_slot=request.attempt + 1,
            tree=outcome.tree,
            failed_at=outcome.failed_at if agent_signal == "rejected" else None,
            error=outcome.error if agent_signal == "rejected" else None,
        )

    def _run_in_copy(self, task: AgentTask, project_root: Path) -> AgentCopyOutcome:
        snapshot = IntegritySnapshot(project_root / ".lictor", project_root, self.vcs)
        with agent_workspace(self.vcs, project_root) as workspace:
            agent_result = self.executor.run(replace(task, cwd=workspace))
            snapshot.verify_and_restore()
            if snapshot.changes:
                return AgentCopyOutcome(agent_result, tampered=tuple(snapshot.changes))
            if agent_result.spec_conflict or agent_result.status == "rate_limited":
                return AgentCopyOutcome(agent_result)
            try:
                return AgentCopyOutcome(agent_result, tree=self.vcs.capture(workspace))
            except CandidateError as exc:
                return AgentCopyOutcome(agent_result, unreadable=str(exc))
