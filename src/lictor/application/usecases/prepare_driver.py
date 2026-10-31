"""PrepareDriver use case for writing and updating acceptance test drivers."""

import contextlib
import hashlib
import importlib.resources
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal

from lictor.application.ports import Clock, EventLog, Executor, Vcs
from lictor.application.run_files import (
    DRIVER_OWNER,
    attempt_prefix,
    relative_to_project,
    unused_path,
)
from lictor.application.usecases.agent_events import denial_event, denial_targets
from lictor.application.usecases.verify_attempt import IntegritySnapshot
from lictor.domain.model import AgentResult, AgentTask, Event
from lictor.domain.rules import count_agent_failures, is_agent_unavailable

DriverSignal = Literal["ok", "rate_limited", "unavailable", "error"]


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
    last_error: str | None = None
    prompt_template: str | None = None
    agent_failures: int = 0


@dataclass(frozen=True)
class PrepareDriverResult:
    """Result of PrepareDriver use case."""

    agent_result: AgentResult
    driver_attempts: int
    prompt_sha: str
    reverted_files: tuple[str, ...] = ()
    agent_signal: DriverSignal = "ok"
    agent_failures: int = 0


def _is_path_allowed(rel_path: str, allowed_roots: Sequence[str]) -> bool:
    norm_rel = rel_path.strip('"').strip("/")
    path_obj = PurePosixPath(norm_rel)
    for root in allowed_roots:
        norm_root = root.strip('"').strip("/")
        if not norm_root:
            return True
        root_obj = PurePosixPath(norm_root)
        if path_obj == root_obj or root_obj in path_obj.parents:
            return True
    return False


def _extract_cli_version(executor: Executor) -> str:
    health_fn = getattr(executor, "health", None)
    if callable(health_fn):
        with contextlib.suppress(Exception):
            return str(health_fn().cli_version)
    return ""


def _collect_features_text(features_dir: Path) -> str:
    if not features_dir.is_dir():
        return ""
    feature_texts: list[str] = []
    for feat_file in sorted(features_dir.rglob("*.feature")):
        feature_texts.append(feat_file.read_text(encoding="utf-8").strip())
    return "\n\n".join(feature_texts)


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
        """Reset repo, prepare driver task, invoke agent with integrity snapshot, and enforce L0."""
        self.vcs.reset_to_head()

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
            "{driver_hints}": request.driver_hints,
            "{hints}": request.hints,
            "{bloque_de_error}": error_block,
        }
        prompt = raw_template
        for k, v in replacements.items():
            prompt = prompt.replace(k, v)

        lictor_dir = request.project_root / ".lictor"
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

        snapshot = IntegritySnapshot(lictor_dir, request.project_root)
        agent_result = self.executor.run(task)
        integrity_ok = snapshot.verify_and_restore()

        # L0-rutas enforcement: allowed are paths.driver + paths.writable
        allowed_paths = tuple(request.paths_driver) + tuple(request.paths_writable)
        reverted: list[str] = []
        status_files = self.vcs.status()
        for f in status_files:
            if not _is_path_allowed(f, allowed_paths):
                reverted.append(f)

        if reverted:
            self.vcs.revert_paths(reverted)

        agent_failures = count_agent_failures(agent_result.status, request.agent_failures)
        if not integrity_ok and agent_result.status == "ok":
            agent_result = replace(agent_result, status="error")

        agent_signal: DriverSignal
        new_attempts = request.driver_attempts
        if agent_result.status == "rate_limited":
            agent_signal = "rate_limited"
        elif is_agent_unavailable(agent_result.status, agent_failures):
            agent_signal = "unavailable"
        else:
            agent_signal = "ok" if agent_result.status == "ok" else "error"
            new_attempts = request.driver_attempts + 1

        rel_raw = relative_to_project(raw_path, request.project_root)
        stderr_path = agent_result.stderr_path

        cli_version = _extract_cli_version(self.executor)

        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.agent.finished.v1",
            source="lictor/0.1.0",
            time=self.clock.now_iso(),
            runid=request.run_id,
            scenarioid=None,
            data={
                "role": "driver",
                "attempt": new_attempts,
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
                "stderr": (
                    relative_to_project(stderr_path, request.project_root) if stderr_path else None
                ),
                "reverted_files": reverted,
            },
        )
        self.event_log.append(event)
        if agent_result.permission_denials:
            self.event_log.append(denial_event(agent_result, event))

        return PrepareDriverResult(
            agent_result=agent_result,
            driver_attempts=new_attempts,
            prompt_sha=prompt_sha,
            reverted_files=tuple(reverted),
            agent_signal=agent_signal,
            agent_failures=agent_failures,
        )
