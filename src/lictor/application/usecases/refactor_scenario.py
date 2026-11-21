"""RefactorScenario: the agent improves what an implemented scenario left, keeping behaviour.

It runs once after each implemented green (ADR-0010), in a fresh copy of HEAD like any attempt.
What it left is verified by `VerifyAttempt` with the `refactor` role; this use case only makes the
call and says what it left.
"""

import hashlib
import importlib.resources
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.run_files import (
    agent_files_dir,
    attempt_prefix,
    save_prompt,
    unused_path,
)
from lictor.application.usecases.agent_events import AgentCall
from lictor.application.usecases.agent_turn import AgentTurns
from lictor.domain.complexity import refactor_complexity_text
from lictor.domain.controls import (
    Control,
    agent_command,
    agent_tools_with_files,
    complexity_controls,
    refactor_measures,
    refactor_measures_text,
)
from lictor.domain.errors import SealTamperedError
from lictor.domain.model import AgentResult, AgentRole, AgentSignal, AgentTask, Scenario
from lictor.domain.refactor import Measure

ROLE: Final[AgentRole] = "refactor"


@dataclass(frozen=True)
class RefactorScenarioRequest:
    """What the refactor of one scenario needs; paths, hints and controls come from the profile."""

    project_root: Path
    run_id: str
    scenario: Scenario
    attempt_slot: int  # the attempt that turned the scenario green: its evidence goes beside it
    refactor_of: str  # the scenario's green commit
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
    refactor_hints: str
    forbidden_paths: Sequence[str] = ()
    controls: Sequence[Control] = ()
    installed_plugins: Sequence[str] = ()
    agent_failures: int = 0
    prompt_template: str | None = None
    # What the green's verification measured: its complexity names what the refactor should
    # lower (ADR-0013), as the guard compares with it.
    baseline: Mapping[str, Measure] = field(default_factory=dict)
    # The profile's files, copied next to the call for an `agent_run` that names them.
    agent_files: Sequence[Path] = ()


@dataclass(frozen=True)
class RefactorScenarioResult:
    """What the call left: its tree, whether it changes anything, and what it means for the run."""

    agent_result: AgentResult
    agent_signal: AgentSignal
    agent_failures: int
    keeps: bool
    tree: str | None = None
    changed: bool = False
    failed_at: str | None = None
    error: str | None = None


class RefactorScenario:
    """Use case to make the refactor call after a scenario turned green."""

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

    def execute(self, request: RefactorScenarioRequest) -> RefactorScenarioResult:
        """Verify the seal, call the agent in a copy of HEAD and read what it left."""
        if not self.spec_source.verify_seal(request.project_root, request.paths_driver):
            raise SealTamperedError(
                "La especificación o el driver cambiaron fuera de Lictor. "
                "Edición manual detectada; ejecución terminada."
            )
        template = (
            request.prompt_template
            if request.prompt_template is not None
            else importlib.resources.files("lictor.prompts")
            .joinpath("refactor.md")
            .read_text(encoding="utf-8")
        )
        prompt_sha = f"sha256:{hashlib.sha256(template.encode('utf-8')).hexdigest()}"
        with agent_files_dir(request.project_root, request.agent_files) as files_dir:
            files = str(files_dir) if files_dir is not None else None
            prompt = self._prompt(request, template, files)

            sid = request.scenario.id.value
            prefix = attempt_prefix(request.project_root, request.run_id, sid, request.attempt_slot)
            prefix.parent.mkdir(parents=True, exist_ok=True)
            raw_path = unused_path(prefix.with_name(f"{prefix.name}-refactor.json"))
            task = AgentTask(
                role=ROLE,
                prompt=prompt,
                prompt_sha=prompt_sha,
                cwd=request.project_root,
                model=request.model,
                allowed_tools=agent_tools_with_files(request.allowed_tools, files),
                max_turns=request.max_turns,
                timeout_s=request.timeout_s,
                raw_path=raw_path,
            )
            started = AgentCall(
                run_id=request.run_id,
                scenario_id=sid,
                role=ROLE,
                attempt=request.attempt_slot,
                signal="",
                model=request.model,
                cli_version="",
                prompt_sha=prompt_sha,
                raw_path=raw_path,
                project_root=request.project_root,
                prompt=save_prompt(raw_path, prompt, request.project_root),
            )
            # A single attempt: the call is always recorded under the green's, whatever it means.
            turn = AgentTurns(self.vcs, self.executor, self.event_log, self.clock).take(
                task,
                started,
                max_attempts=1,
                agent_failures=request.agent_failures,
                attempt_if_kept=request.attempt_slot,
            )
        outcome = turn.outcome
        rejected = turn.signal == "rejected"
        return RefactorScenarioResult(
            agent_result=outcome.agent_result,
            agent_signal=turn.signal,
            agent_failures=turn.failures,
            keeps=turn.keeps,
            tree=outcome.tree,
            changed=bool(outcome.tree and self.vcs.changes(outcome.tree)),
            failed_at=outcome.failed_at if rejected else None,
            error=outcome.error if rejected else None,
        )

    def _prompt(self, request: RefactorScenarioRequest, template: str, files: str | None) -> str:
        measures = refactor_measures(request.controls, request.installed_plugins)
        complexity = complexity_controls(request.controls)
        own = [cmd for c in complexity if (cmd := agent_command(c, files)) is not None]
        commands = list(
            dict.fromkeys([*request.check_commands, *(cmd for _, cmd in measures), *own])
        )
        parent = self.vcs.parent_of(request.refactor_of)
        changed = self.vcs.changed_paths(parent, request.refactor_of) if parent else []
        replacements = {
            "{repo}": request.project_root.name,
            "{id}": request.scenario.id.value,
            "{gherkin_del_escenario}": self.spec_source.scenario_text(
                request.project_root, request.scenario
            ),
            "{cambiados}": ", ".join(changed) or "nada",
            "{refactor_hints}": request.refactor_hints,
            "{writable}": ", ".join(request.paths_writable),
            "{protected}": ", ".join(request.paths_protected),
            "{forbidden}": ", ".join(request.forbidden_paths) or "nada más",
            "{suppressions}": ", ".join(request.suppressions),
            "{green_ids}": ", ".join(request.green_ids) if request.green_ids else "ninguno",
            "{check_commands}": ", ".join(commands) or "los tests del proyecto",
            "{medidas}": refactor_measures_text(measures),
            "{complejidad}": _complexity_text(request, complexity, files),
            "{hints}": request.hints,
        }
        prompt = template
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        return prompt


def _complexity_text(
    request: RefactorScenarioRequest, controls: Sequence[Control], files: str | None
) -> str:
    """What the green left above each complexity control's limit, and how to measure it."""
    texts = []
    for control in controls:
        measure = request.baseline.get(control.id)
        text = refactor_complexity_text(
            measure.over if measure else None, agent_command(control, files)
        )
        if text:
            texts.append(text)
    return "\n\n".join(texts)
