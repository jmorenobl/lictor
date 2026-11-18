"""ImplementScenario use case for executing an implementation attempt for a scenario."""

import hashlib
import importlib.resources
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.run_files import attempt_prefix, save_prompt, unused_path
from lictor.application.usecases.agent_events import AgentCall
from lictor.application.usecases.agent_turn import AgentTurns
from lictor.domain.controls import Control, agent_measures, agent_measures_text
from lictor.domain.errors import SealTamperedError
from lictor.domain.model import AgentResult, AgentSignal, AgentStatus, AgentTask, Scenario
from lictor.domain.rules import error_for_agent

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
    forbidden_paths: Sequence[str] = ()
    max_attempts: int = 0  # only to tell whoever watches; the graph decides when they run out
    # The controls the run verifies, and the plugins its environment loads: the minimums of the
    # ones that run are told to the agent (F55); a skipped control demands nothing.
    controls: Sequence[Control] = ()
    installed_plugins: Sequence[str] = ()


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
                f"{error_for_agent(request.failed_at, request.last_error)}"
            )
            error_block = f"{error_block}\n{failure}".strip()

        measures = agent_measures(request.controls, request.installed_plugins)
        commands = list(dict.fromkeys([*request.check_commands, *(m.command for m in measures)]))

        replacements = {
            "{repo}": request.project_root.name,
            "{id}": request.scenario.id.value,
            "{gherkin_del_escenario}": gherkin_text,
            "{writable}": ", ".join(request.paths_writable),
            "{protected}": ", ".join(request.paths_protected),
            "{forbidden}": ", ".join(request.forbidden_paths) or "nada más",
            "{suppressions}": ", ".join(request.suppressions),
            "{green_ids}": ", ".join(request.green_ids) if request.green_ids else "ninguno",
            "{check_commands}": ", ".join(commands) or "los tests del proyecto",
            "{medidas}": agent_measures_text(measures),
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
        prompt_file = save_prompt(raw_path, prompt, request.project_root)
        started = AgentCall(
            run_id=request.run_id,
            scenario_id=request.scenario.id.value,
            role="implement",
            attempt=request.attempt + 1,
            signal="",
            model=request.model,
            cli_version="",
            prompt_sha=prompt_sha,
            raw_path=raw_path,
            project_root=request.project_root,
            prompt=prompt_file,
        )
        turn = AgentTurns(self.vcs, self.executor, self.event_log, self.clock).take(
            task,
            started,
            max_attempts=request.max_attempts,
            agent_failures=request.agent_failures,
            attempt_if_kept=request.attempt,
        )
        outcome, agent_signal = turn.outcome, turn.signal
        new_attempt = request.attempt if turn.keeps else request.attempt + 1

        return ImplementScenarioResult(
            agent_result=outcome.agent_result,
            agent_signal=agent_signal,
            attempt=new_attempt,
            prompt_sha=prompt_sha,
            agent_failures=turn.failures,
            attempt_slot=request.attempt + 1,
            tree=outcome.tree,
            failed_at=outcome.failed_at if agent_signal == "rejected" else None,
            error=outcome.error if agent_signal == "rejected" else None,
        )
