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
from lictor.domain.api import REFACTOR_API
from lictor.domain.complexity import OverLimit, refactor_complexity_text
from lictor.domain.controls import (
    GREEN_COVERAGE,
    CommandControl,
    Control,
    agent_command,
    agent_tools_with_files,
    code_controls,
    refactor_measures,
    refactor_measures_text,
    runnable_command,
)
from lictor.domain.conventions import conventions_block
from lictor.domain.duplication import Duplication, refactor_duplication_text
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
    # What the green's verification measured: its complexity and its clones name what the
    # refactor should lower (ADR-0013, ADR-0014), as the guard compares with it.
    baseline: Mapping[str, Measure] = field(default_factory=dict)
    # The profile's files, copied next to the call for an `agent_run` that names them.
    agent_files: Sequence[Path] = ()
    # The directory of that verification: the refactor's coverage measure compares with the
    # green's report there (F71). None leaves it to the counts in the prompt.
    baseline_step: Path | None = None
    conventions: str = ""  # the project's rules of code, from `lictor.json` (F79)


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
        green = _green_reports(request)
        template = (
            request.prompt_template
            if request.prompt_template is not None
            else importlib.resources.files("lictor.prompts")
            .joinpath("refactor.md")
            .read_text(encoding="utf-8")
        )
        prompt_sha = f"sha256:{hashlib.sha256(template.encode('utf-8')).hexdigest()}"
        with agent_files_dir(request.project_root, request.agent_files, green) as files_dir:
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
        measures = _runnable_measures(request, files)
        code = code_controls(request.controls)
        own = [cmd for c in code if (cmd := agent_command(c, files)) is not None]
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
            "{medidas}": refactor_measures_text(measures, _green_left(request.baseline)),
            "{medidas_del_codigo}": _code_text(request, code, files, changed),
            "{hints}": request.hints,
            # Last: the person's text mustn't be read as a placeholder.
            "{conventions}": conventions_block(request.conventions, "refactor"),
        }
        prompt = template
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        return prompt


def _code_text(
    request: RefactorScenarioRequest,
    controls: Sequence[CommandControl],
    files: str | None,
    changed: Sequence[str],
) -> str:
    """What the green left of each control that measures the code, and how to measure it.

    One block per control, in the order they were declared: the functions above a complexity
    limit (ADR-0013) and the clones (ADR-0014); then the rule of the public API (ADR-0017).
    """
    texts = []
    for control in controls:
        measure = request.baseline.get(control.id)
        findings = measure.findings if measure else None
        command = agent_command(control, files)
        if control.report == "duplication":
            repeated = findings if isinstance(findings, Duplication) else None
            text = refactor_duplication_text(repeated, command, changed)
        else:
            over = findings if isinstance(findings, OverLimit) else None
            text = refactor_complexity_text(over, command)
        if text:
            texts.append(text)
    # The public API has no findings to name: only what the green already broke (ADR-0017).
    if any(m.breaks is not None for m in request.baseline.values()):
        texts.append(REFACTOR_API)
    return "\n\n".join(texts)


def _green_left(baseline: Mapping[str, Measure]) -> dict[str, dict[str, int]]:
    """What the green left untested, by control and kind, for the refactor's prompt (F71)."""
    return {
        control_id: {str(kind): count for kind, count in measure.misses}
        for control_id, measure in baseline.items()
        if measure.misses is not None and measure.findings is None
    }


def _green_reports(request: RefactorScenarioRequest) -> list[tuple[Path, str]]:
    """The green's coverage report, for the profile's coverage measure to compare with (F71)."""
    if request.baseline_step is None:
        return []
    return [
        (request.baseline_step / f"control_{control.id}.xml", GREEN_COVERAGE)
        for control in request.controls
        if control.report == "coverage"
    ][:1]


def _runnable_measures(
    request: RefactorScenarioRequest, files: str | None
) -> list[tuple[str, str]]:
    """Each control the refactor can measure, with its command as this call can run it."""
    return [
        (control_id, command)
        for control_id, raw in refactor_measures(request.controls, request.installed_plugins)
        if (command := runnable_command(raw, files)) is not None
    ]
