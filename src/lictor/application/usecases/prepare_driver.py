"""PrepareDriver use case for writing and updating acceptance test drivers."""

import contextlib
import hashlib
import importlib.resources
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from lictor.application.ports import Clock, EventLog, Executor, Vcs
from lictor.application.run_files import (
    DRIVER_OWNER,
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
from lictor.application.usecases.verify_attempt import AgentCopyOutcome, IntegritySnapshot
from lictor.domain.errors import CandidateError
from lictor.domain.model import AgentResult, AgentSignal, AgentTask, TreeChange
from lictor.domain.rules import (
    classify_agent,
    count_agent_failures,
    forbidden_paths,
    keeps_attempt,
    path_within,
)


@dataclass(frozen=True)
class PrepareDriverRequest:
    """Request payload for PrepareDriver use case."""

    project_root: Path
    run_id: str
    model: str
    allowed_tools: Sequence[str]
    max_turns: int
    timeout_s: int
    paths_writable: Sequence[str]
    paths_driver: Sequence[str]
    paths_protected: Sequence[str]
    driver_hints: str
    hints: str
    green_ids: Sequence[str]
    driver_attempts: int
    reopened_ids: Sequence[str] = ()
    last_error: str | None = None
    prompt_template: str | None = None
    agent_failures: int = 0
    forbidden_paths: Sequence[str] = ()


@dataclass(frozen=True)
class PrepareDriverResult:
    """Result of PrepareDriver use case."""

    agent_result: AgentResult
    driver_attempts: int
    prompt_sha: str
    reverted_files: tuple[str, ...] = ()
    agent_signal: AgentSignal = "ok"
    agent_failures: int = 0
    tree: str | None = None
    error: str | None = None


def _extract_cli_version(executor: Executor) -> str:
    health_fn = getattr(executor, "health", None)
    if callable(health_fn):
        with contextlib.suppress(Exception):
            return str(health_fn().cli_version)
    return ""


def _reopened_line(reopened_ids: Sequence[str]) -> str:
    if not reopened_ids:
        return ""
    return (
        f"- Los escenarios {', '.join(reopened_ids)} cambiaron desde que se implementaron: "
        "actualiza sus tests para que comprueben lo que piden ahora."
    )


def _collect_features_text(features_dir: Path) -> str:
    if not features_dir.is_dir():
        return ""
    feature_texts: list[str] = []
    for feat_file in sorted(features_dir.rglob("*.feature")):
        feature_texts.append(feat_file.read_text(encoding="utf-8").strip())
    return "\n\n".join(feature_texts)


def _discarded_paths(changes: Sequence[TreeChange], request: PrepareDriverRequest) -> list[str]:
    """Return what the driver may not write: L0-rutas, allowing paths.driver and paths.writable."""
    allowed_paths = tuple(request.paths_driver) + tuple(request.paths_writable)
    changed = [change.path for change in changes]
    forbidden = set(forbidden_paths(changed, request.forbidden_paths))
    discarded: list[str] = []
    for change in changes:
        # The driver may need its own config; what it writes in project code may not.
        in_project_code = change.path in forbidden and not path_within(
            change.path, request.paths_driver
        )
        if change.is_link or in_project_code or not path_within(change.path, allowed_paths):
            discarded.append(change.path)
    return discarded


class PrepareDriver:
    """Use case to call agent for building or extending acceptance driver."""

    def __init__(
        self,
        vcs: Vcs,
        executor: Executor,
        event_log: EventLog,
        clock: Clock,
    ) -> None:
        self.vcs = vcs
        self.executor = executor
        self.event_log = event_log
        self.clock = clock

    def execute(self, request: PrepareDriverRequest) -> PrepareDriverResult:
        """Run the agent on a copy of HEAD with integrity snapshot and keep what L0 allows."""
        raw_template = (
            request.prompt_template
            if request.prompt_template is not None
            else importlib.resources.files("lictor.prompts")
            .joinpath("driver.md")
            .read_text(encoding="utf-8")
        )
        prompt_sha = f"sha256:{hashlib.sha256(raw_template.encode('utf-8')).hexdigest()}"

        all_features = _collect_features_text(request.project_root / "features")

        # protected without driver paths
        driver_set = set(request.paths_driver)
        protected_without_driver = [p for p in request.paths_protected if p not in driver_set]

        # error block if attempt > 0 and last_error exists
        if request.driver_attempts > 0 and request.last_error:
            error_block = f"El intento anterior falló:\n{request.last_error}"
        else:
            error_block = ""

        replacements = {
            "{repo}": request.project_root.name,
            "{features}": all_features,
            "{driver_paths}": ", ".join(request.paths_driver),
            "{writable}": ", ".join(request.paths_writable),
            "{protected_sin_driver}": ", ".join(protected_without_driver),
            "{green_ids}": ", ".join(request.green_ids) if request.green_ids else "ninguno",
            "{reabiertos}": _reopened_line(request.reopened_ids),
            "{driver_hints}": request.driver_hints,
            "{hints}": request.hints,
            "{bloque_de_error}": error_block,
        }
        prompt = raw_template
        for k, v in replacements.items():
            prompt = prompt.replace(k, v)

        prefix = attempt_prefix(
            request.project_root, request.run_id, DRIVER_OWNER, request.driver_attempts + 1
        )
        prefix.parent.mkdir(parents=True, exist_ok=True)
        raw_path = unused_path(
            prefix.with_name(f"{prefix.name}-driver.json"), request.agent_failures
        )

        task = AgentTask(
            role="driver",
            prompt=prompt,
            prompt_sha=prompt_sha,
            cwd=request.project_root,
            model=request.model,
            allowed_tools=request.allowed_tools,
            max_turns=request.max_turns,
            timeout_s=request.timeout_s,
            raw_path=raw_path,
        )
        outcome = self._run_in_copy(task, request)
        agent_result = outcome.agent_result
        reverted = list(outcome.reverted)

        agent_failures = count_agent_failures(agent_result.status, request.agent_failures)

        agent_signal = classify_agent(
            agent_result.status,
            agent_failures,
            tampered=bool(outcome.tampered),
            unreadable=outcome.unreadable is not None,
        )
        keeps = keeps_attempt(agent_signal, agent_failures)
        new_attempts = request.driver_attempts if keeps else request.driver_attempts + 1

        call = AgentCall(
            run_id=request.run_id,
            scenario_id=None,
            role="driver",
            attempt=new_attempts,
            signal=agent_signal,
            model=request.model,
            cli_version=_extract_cli_version(self.executor),
            prompt_sha=prompt_sha,
            raw_path=raw_path,
            project_root=request.project_root,
        )
        event = agent_finished_event(
            self.clock, call, agent_result, extra={"reverted_files": reverted}
        )
        self.event_log.append(event)
        if agent_result.permission_denials:
            self.event_log.append(denial_event(agent_result, event))
        if outcome.tampered:
            self.event_log.append(tamper_event(event, outcome.tampered))

        return PrepareDriverResult(
            agent_result=agent_result,
            driver_attempts=new_attempts,
            prompt_sha=prompt_sha,
            reverted_files=tuple(reverted),
            agent_signal=agent_signal,
            agent_failures=agent_failures,
            tree=outcome.tree,
            error=outcome.error if agent_signal == "rejected" else None,
        )

    def _run_in_copy(self, task: AgentTask, request: PrepareDriverRequest) -> AgentCopyOutcome:
        lictor_dir = request.project_root / ".lictor"
        snapshot = IntegritySnapshot(lictor_dir, request.project_root, self.vcs)
        with agent_workspace(self.vcs, request.project_root) as workspace:
            agent_result = self.executor.run(replace(task, cwd=workspace))
            snapshot.verify_and_restore()
            if snapshot.changes:
                return AgentCopyOutcome(agent_result, tampered=tuple(snapshot.changes))
            if agent_result.status == "rate_limited":
                return AgentCopyOutcome(agent_result)
            try:
                tree = self.vcs.capture(workspace)
                reverted = _discarded_paths(self.vcs.changes(tree), request)
                if reverted:
                    tree = self.vcs.capture(workspace, skip=reverted)
            except CandidateError as exc:
                return AgentCopyOutcome(agent_result, unreadable=str(exc))
        return AgentCopyOutcome(agent_result, tree=tree, reverted=tuple(reverted))
