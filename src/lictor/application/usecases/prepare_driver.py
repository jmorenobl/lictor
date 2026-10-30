"""PrepareDriver use case for writing and updating acceptance test drivers."""

import contextlib
import hashlib
import importlib.resources
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lictor.application.ports import Clock, EventLog, Executor, Vcs
from lictor.application.usecases.verify_attempt import IntegritySnapshot
from lictor.domain.model import AgentResult, AgentTask, Event


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


@dataclass(frozen=True)
class PrepareDriverResult:
    """Result of PrepareDriver use case."""

    agent_result: AgentResult
    driver_attempts: int
    prompt_sha: str
    reverted_files: tuple[str, ...] = ()


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

        features_dir = request.project_root / "features"
        feature_texts: list[str] = []
        if features_dir.is_dir():
            for feat_file in sorted(features_dir.rglob("*.feature")):
                feature_texts.append(feat_file.read_text(encoding="utf-8").strip())
        all_features = "\n\n".join(feature_texts)

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
        runs_dir = lictor_dir / "runs" / request.run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        raw_path = runs_dir / f"{request.driver_attempts:03d}-driver.json"

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

        if agent_result.status == "rate_limited":
            new_attempts = request.driver_attempts
        else:
            new_attempts = request.driver_attempts + 1

        rel_raw = str(raw_path)
        with contextlib.suppress(ValueError):
            rel_raw = raw_path.relative_to(request.project_root).as_posix()

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

        return PrepareDriverResult(
            agent_result=agent_result,
            driver_attempts=new_attempts,
            prompt_sha=prompt_sha,
            reverted_files=tuple(reverted),
        )
