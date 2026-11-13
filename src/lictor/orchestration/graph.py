"""LangGraph state graph orchestration for Lictor v0.1 execution cycle."""

import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from lictor import __version__
from lictor.application.anchor import observe_anchor
from lictor.application.events import new_event
from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.spec_history import GreenRecord, SpecHistory
from lictor.application.usecases.build_report import BuildReport, BuildReportRequest
from lictor.application.usecases.check_red import CheckRed, CheckRedRequest
from lictor.application.usecases.commit_green import CommitGreen, CommitGreenRequest
from lictor.application.usecases.implement_scenario import (
    ImplementScenario,
    ImplementScenarioRequest,
)
from lictor.application.usecases.preconditions import CheckPreconditions, PreconditionsRequest
from lictor.application.usecases.prepare_driver import PrepareDriver, PrepareDriverRequest
from lictor.application.usecases.quarantine import (
    Quarantine,
    QuarantineReason,
    QuarantineRequest,
)
from lictor.application.usecases.verify_attempt import (
    VerifyAttempt,
    VerifyAttemptRequest,
    control_summaries,
)
from lictor.domain.errors import (
    CandidateError,
    DriverError,
    ExitCode,
    SealTamperedError,
    SpecError,
)
from lictor.domain.model import AgentStatus, Candidate, Scenario, ScenarioId
from lictor.domain.routing import (
    route_build_driver,
    route_commit,
    route_implement,
    route_load_spec,
    route_pause,
    route_red_check,
    route_verify,
)
from lictor.domain.rules import (
    Impostors,
    MapLink,
    RedOracle,
    paths_only_a_person_can_change,
    person_only_reason,
    precondition_exit_code,
    run_outcome,
    suspicious_driver,
)
from lictor.domain.stages import committed_greens, final_reason


def merge_dict(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer to merge dictionaries."""
    res = dict(a or {})
    if b:
        res.update(b)
    return res


def reduce_totals(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer to accumulate execution totals field by field."""
    base = dict(a or {})
    if not b:
        return base
    tok_a = base.get("tokens") or {}
    tok_b = b.get("tokens") or {}
    base["tokens"] = {
        "input": tok_a.get("input", 0) + tok_b.get("input", 0),
        "output": tok_a.get("output", 0) + tok_b.get("output", 0),
        "cache_read": tok_a.get("cache_read", 0) + tok_b.get("cache_read", 0),
        "cache_write": tok_a.get("cache_write", 0) + tok_b.get("cache_write", 0),
    }
    base["cost_usd"] = round(float(base.get("cost_usd", 0.0)) + float(b.get("cost_usd", 0.0)), 6)
    base["agent_duration_ms"] = int(base.get("agent_duration_ms", 0)) + int(
        b.get("agent_duration_ms", 0)
    )
    base["verify_duration_ms"] = int(base.get("verify_duration_ms", 0)) + int(
        b.get("verify_duration_ms", 0)
    )
    return base


class GraphState(TypedDict, total=False):
    """LangGraph state for Lictor v0.1 sequential execution graph."""

    run_id: str
    config_hash: str
    scenarios: list[dict[str, Any]]
    status: Annotated[dict[str, str], merge_dict]
    reasons: Annotated[dict[str, str], merge_dict]
    driver_fresh: bool
    driver_attempts: int
    current: str | None
    attempt: int
    attempt_slot: int
    last_error: str | None
    last_failed_at: str | None
    last_verify: dict[str, Any] | None
    attempt_tree: str | None
    anchor: dict[str, str]
    agent_signal: str | None
    agent_failures: int
    last_agent_status: AgentStatus | None
    agent_detail: str
    totals: Annotated[dict[str, Any], reduce_totals]
    exit_code: int | None
    exit_reason: str
    spec_conflict: str | None
    seal_ok: bool
    resume_to: str | None
    reopened: list[str]
    # Green when the spec loaded: their commits aren't this run's.
    green_at_load: list[str]
    failures: list[list[str] | None]
    rejected_paths: list[list[str] | None]


@dataclass(frozen=True)
class GraphContext:
    """Dependency injection container for the LangGraph orchestration layer."""

    project_root: Path
    paths_writable: Sequence[str]
    paths_protected: Sequence[str]
    paths_driver: Sequence[str]
    suppressions: Sequence[str]
    driver_hints: str
    hints: str
    check_commands: Sequence[str]
    model: str
    allowed_tools: Sequence[str]
    max_turns: int
    timeout_s: int
    max_attempts: int
    max_driver_attempts: int
    error_max_chars: int
    acceptance_run: str
    acceptance_report: str
    acceptance_timeout_s: int
    # Only the `attempt` controls: the `check` ones never run in a run (ADR-0007).
    controls: Sequence[Any]
    prepare_driver: PrepareDriver
    check_red: CheckRed
    implement_scenario: ImplementScenario
    verify_attempt: VerifyAttempt
    commit_green: CommitGreen
    quarantine: Quarantine
    preconditions: CheckPreconditions
    spec_source: SpecSource
    spec_history: SpecHistory
    vcs: Vcs
    executor: Executor
    event_log: EventLog
    clock: Clock
    profile_name: str = ""
    # The branch of a linked worktree, which the person integrates; "" in a normal checkout.
    result_branch: str = ""
    build_report: BuildReport | None = None
    acceptance_failure_exit_codes: Sequence[int] = (1,)
    forbidden_paths: Sequence[str] = ()
    driver_exempt_paths: Sequence[str] = ()
    banned_patterns: Sequence[str] = ()
    oracle: RedOracle = field(default_factory=RedOracle)
    map_link: MapLink = "name"
    impostors: Impostors | None = None
    # The controls only `lictor check` runs: a run names them, in `run.started` and the reason.
    check_controls: Sequence[Any] = ()


# Each step is on disk before the next one starts. With the default, a background thread writes the
# checkpoint while the agent runs: a kill loses it, and `IntegritySnapshot` sees it as tampering.
DURABILITY: Literal["sync"] = "sync"

_SUSPICIOUS_DRIVER = (
    "Posible driver defectuoso, no seguro: en cada intento el test de {sid} falló con un error "
    "lanzado desde el propio driver ({origin}), no desde la implementación. Revisa el driver; "
    "al cambiarlo y confirmarlo, la siguiente ejecución lo reconstruye y lo vuelve a validar."
)
_PAUSE_REASONS = {"rate_limited": "rate_limited", "blocked": "branch_blocked"}


class _GraphNodes:
    """Encapsulates node execution methods with access to GraphContext."""

    def __init__(self, ctx: GraphContext) -> None:
        self.ctx = ctx

    def _finish(
        self,
        state: GraphState,
        code: int,
        reason: str,
        *,
        report: bool = True,
        reached_end: bool = False,
    ) -> dict[str, Any]:
        """End the run leaving its reason in the journal, the report and the state.

        A run that stops before starting (`report=False`) leaves `report.md` alone: it still holds
        the last run that did something. The reason stays in the journal and on stderr.

        Every exit comes through here, so here the reason tells what the run left to
        `lictor check` (ADR-0007), and, at the end of a run in a linked worktree, how its branch
        gets in.
        """
        reason = final_reason(
            reason,
            check_ids=[control.id for control in self.ctx.check_controls],
            committed=committed_greens(state.get("status", {}), state.get("green_at_load", [])),
            branch=self.ctx.result_branch if reached_end else "",
            worktree=str(self.ctx.project_root),
        )
        event = new_event(
            self.ctx.clock,
            "lictor.run.finished.v1",
            state["run_id"],
            data={
                "exit_code": code,
                "reason": reason,
                "totals": state.get("totals", {}),
            },
        )
        self.ctx.event_log.append(event)
        if report and self.ctx.build_report is not None:
            self.ctx.build_report.execute(
                BuildReportRequest(project_root=self.ctx.project_root, run_id=state["run_id"])
            )
        return {"exit_code": code, "exit_reason": reason}

    def preflight(self, state: GraphState) -> dict[str, Any]:
        if state.get("exit_code") is not None:
            return {}
        # The verification environment is built here, before any agent call can be spent.
        report = self.ctx.preconditions.execute(PreconditionsRequest(run_id=state["run_id"]))
        problems = report.problems
        if problems:
            reason = " ".join(problem.message for problem in problems)
            return self._finish(state, precondition_exit_code(problems), reason, report=False)

        event = new_event(
            self.ctx.clock,
            "lictor.run.started.v1",
            state["run_id"],
            data={
                "lictor_version": __version__,
                "cli_version": report.cli_version,
                "profile": self.ctx.profile_name,
                "config_hash": state.get("config_hash", ""),
                "check_controls": [
                    {
                        "id": control.id,
                        "level": control.level,
                        "mode": control.mode,
                        "min_score": control.min_score,
                    }
                    for control in self.ctx.check_controls
                ],
            },
        )
        self.ctx.event_log.append(event)
        return {"anchor": self._anchor()}

    def _anchor(self) -> dict[str, str]:
        return observe_anchor(self.ctx.vcs, self.ctx.project_root)

    def load_spec(self, state: GraphState) -> dict[str, Any]:
        features_dir = self.ctx.project_root / "features"
        try:
            scenarios = self.ctx.spec_source.load(features_dir)
        except SpecError as exc:
            return {
                **self._finish(state, int(ExitCode.PRECONDITION), f"Spec inválida: {exc}"),
                "seal_ok": False,
            }

        seal_ok = self.ctx.spec_source.verify_seal(self.ctx.project_root, self.ctx.paths_driver)
        records = self.ctx.spec_history.records()
        classes = self.ctx.spec_history.classify({s.id.value: s.spec_hash for s in scenarios})
        reopened = [sid for sid, cls in classes.items() if cls == "reopened"]
        for sid in reopened:
            self._scenario_event(state, "lictor.scenario.reopened.v1", sid, records[sid].commit)

        event = new_event(
            self.ctx.clock,
            "lictor.spec.loaded.v1",
            state["run_id"],
            data={
                "scenario_count": len(scenarios),
                "scenarios": [
                    {
                        "id": s.id.value,
                        "name": s.name,
                        "file": s.file,
                        "line": s.line,
                        **_green_of(records, classes, s.id.value),
                    }
                    for s in scenarios
                ],
            },
        )
        self.ctx.event_log.append(event)

        return {
            "scenarios": [
                {
                    "id": s.id.value,
                    "name": s.name,
                    "file": s.file,
                    "line": s.line,
                    "spec_hash": s.spec_hash,
                }
                for s in scenarios
            ],
            "status": {
                sid: "green" if cls == "green" else "pending" for sid, cls in classes.items()
            },
            "reopened": reopened,
            "green_at_load": [sid for sid, cls in classes.items() if cls == "green"],
            "seal_ok": seal_ok,
            "driver_fresh": not seal_ok,
        }

    def _scenario_event(self, state: GraphState, kind: str, sid: str, commit: str) -> None:
        self.ctx.event_log.append(
            new_event(
                self.ctx.clock,
                kind,
                state["run_id"],
                scenario_id=sid,
                data={"commit": commit},
            )
        )

    def build_driver(self, state: GraphState) -> dict[str, Any]:
        green_ids = [sid for sid, st in state.get("status", {}).items() if st == "green"]
        res = self.ctx.prepare_driver.execute(
            PrepareDriverRequest(
                project_root=self.ctx.project_root,
                run_id=state["run_id"],
                model=self.ctx.model,
                allowed_tools=self.ctx.allowed_tools,
                max_turns=self.ctx.max_turns,
                timeout_s=self.ctx.timeout_s,
                paths_writable=self.ctx.paths_writable,
                paths_driver=self.ctx.paths_driver,
                paths_protected=self.ctx.paths_protected,
                driver_hints=self.ctx.driver_hints,
                hints=self.ctx.hints,
                green_ids=green_ids,
                reopened_ids=state.get("reopened", []),
                driver_attempts=state.get("driver_attempts", 0),
                last_error=state.get("last_error"),
                failed_at=state.get("last_failed_at"),
                agent_failures=state.get("agent_failures", 0),
                forbidden_paths=self.ctx.forbidden_paths,
                driver_exempt_paths=self.ctx.driver_exempt_paths,
                max_driver_attempts=self.ctx.max_driver_attempts,
                check_commands=self.ctx.check_commands,
                suppressions=self.ctx.suppressions,
            )
        )

        rejected: dict[str, Any] = {}
        if res.agent_signal == "rejected":
            rejected["last_error"] = res.error
            rejected["last_failed_at"] = res.failed_at
            if res.driver_attempts >= self.ctx.max_driver_attempts:
                reason = f"Driver rechazado tras {res.driver_attempts} intentos: {res.error}"
                rejected.update(self._finish(state, int(ExitCode.PRECONDITION), reason))

        return {
            **rejected,
            "attempt_tree": res.tree,
            "driver_attempts": res.driver_attempts,
            "agent_signal": res.agent_signal,
            "agent_failures": res.agent_failures,
            "agent_detail": res.agent_result.text,
            "totals": {
                "tokens": {
                    "input": res.agent_result.tokens.input,
                    "output": res.agent_result.tokens.output,
                    "cache_read": res.agent_result.tokens.cache_read,
                    "cache_write": res.agent_result.tokens.cache_write,
                },
                "cost_usd": res.agent_result.cost_usd or 0.0,
                "agent_duration_ms": res.agent_result.duration_ms,
                "verify_duration_ms": 0,
            },
            "resume_to": "build_driver",
        }

    def red_check(self, state: GraphState) -> dict[str, Any]:
        all_sids = [ScenarioId(s["id"]) for s in state.get("scenarios", [])]
        try:
            res = self.ctx.check_red.execute(
                CheckRedRequest(
                    project_root=self.ctx.project_root,
                    run_id=state["run_id"],
                    acceptance_run=self.ctx.acceptance_run,
                    driver_fresh=state.get("driver_fresh", True),
                    driver_attempts=state.get("driver_attempts", 1),
                    max_driver_attempts=self.ctx.max_driver_attempts,
                    driver_paths=self.ctx.paths_driver,
                    all_scenario_ids=all_sids,
                    acceptance_report=self.ctx.acceptance_report,
                    acceptance_timeout_s=self.ctx.acceptance_timeout_s,
                    error_max_chars=self.ctx.error_max_chars,
                    acceptance_failure_exit_codes=self.ctx.acceptance_failure_exit_codes,
                    tree=state.get("attempt_tree"),
                    scenario_hashes={
                        s["id"]: s.get("spec_hash", "") for s in state.get("scenarios", [])
                    },
                    scenario_files=_scenario_files(state),
                    oracle=self.ctx.oracle,
                    link=self.ctx.map_link,
                    impostors=self.ctx.impostors,
                    suppressions=self.ctx.suppressions,
                    banned_patterns=self.ctx.banned_patterns,
                )
            )
        except DriverError as exc:
            return {
                **self._finish(state, int(ExitCode.PRECONDITION), str(exc)),
                "last_verify": {"ok": False},
            }
        except CandidateError as exc:
            return {"agent_signal": "blocked", "agent_detail": str(exc), "resume_to": "red_check"}

        if res.ok:
            return {
                "last_verify": {"ok": True},
                "status": res.scenario_status,
                "last_error": None,
                "last_failed_at": None,
                "seal_ok": True,
                "agent_signal": None,
                "anchor": self._anchor(),
            }
        return {
            "last_verify": {"ok": False},
            "last_error": res.last_error,
            "last_failed_at": res.failed_at,
        }

    def pick_next(self, state: GraphState) -> dict[str, Any]:
        status = state.get("status", {})
        scenarios = state.get("scenarios", [])
        for s in scenarios:
            sid = s["id"]
            if status.get(sid) == "pending" or sid not in status:
                return {
                    "current": sid,
                    "attempt": 0,
                    "failures": [],
                    "rejected_paths": [],
                    "last_error": None,
                    "last_failed_at": None,
                    "last_agent_status": None,
                }
        return {"current": None}

    def implement(self, state: GraphState) -> dict[str, Any]:
        current_id = state.get("current")
        if not current_id:
            return {}

        if state.get("seal_ok") and not self.ctx.spec_source.verify_seal(
            self.ctx.project_root, self.ctx.paths_driver
        ):
            return self._finish(
                state,
                int(ExitCode.PRECONDITION),
                "La especificación o el driver cambiaron a mitad de ejecución.",
            )

        scenarios = state.get("scenarios", [])
        scen_dict = next((s for s in scenarios if s["id"] == current_id), None)
        if not scen_dict:
            reason = f"El escenario {current_id} no está en la spec cargada"
            return self._finish(state, int(ExitCode.INTERNAL_ERROR), reason)

        scenario = Scenario(
            id=ScenarioId(scen_dict["id"]),
            name=scen_dict["name"],
            file=scen_dict["file"],
            line=scen_dict["line"],
        )
        green_ids = [sid for sid, st in state.get("status", {}).items() if st == "green"]

        try:
            res = self.ctx.implement_scenario.execute(
                ImplementScenarioRequest(
                    project_root=self.ctx.project_root,
                    run_id=state["run_id"],
                    scenario=scenario,
                    attempt=state.get("attempt", 0),
                    model=self.ctx.model,
                    allowed_tools=self.ctx.allowed_tools,
                    max_turns=self.ctx.max_turns,
                    timeout_s=self.ctx.timeout_s,
                    paths_writable=self.ctx.paths_writable,
                    paths_protected=self.ctx.paths_protected,
                    paths_driver=self.ctx.paths_driver,
                    suppressions=self.ctx.suppressions,
                    green_ids=green_ids,
                    check_commands=self.ctx.check_commands,
                    hints=self.ctx.hints,
                    last_error=state.get("last_error"),
                    failed_at=state.get("last_failed_at"),
                    agent_failures=state.get("agent_failures", 0),
                    last_agent_status=state.get("last_agent_status"),
                    forbidden_paths=self.ctx.forbidden_paths,
                    max_attempts=self.ctx.max_attempts,
                )
            )
        except SealTamperedError as exc:
            return self._finish(state, int(ExitCode.PRECONDITION), str(exc))

        rejected: dict[str, Any] = {}
        if res.agent_signal == "rejected":
            rejected = self._reject_attempt(state, res.attempt, res.failed_at, res.error)

        return {
            **rejected,
            "attempt_tree": res.tree,
            "attempt": res.attempt,
            "attempt_slot": res.attempt_slot,
            "agent_signal": res.agent_signal,
            "agent_failures": res.agent_failures,
            "agent_detail": res.agent_result.text,
            "last_agent_status": res.agent_result.status,
            "spec_conflict": res.agent_result.spec_conflict,
            "totals": {
                "tokens": {
                    "input": res.agent_result.tokens.input,
                    "output": res.agent_result.tokens.output,
                    "cache_read": res.agent_result.tokens.cache_read,
                    "cache_write": res.agent_result.tokens.cache_write,
                },
                "cost_usd": res.agent_result.cost_usd or 0.0,
                "agent_duration_ms": res.agent_result.duration_ms,
                "verify_duration_ms": 0,
            },
            "resume_to": "implement",
        }

    def _reject_attempt(
        self, state: GraphState, attempt: int, failed_at: str | None, error: str | None
    ) -> dict[str, Any]:
        """Fail an attempt that can't even be verified, leaving the same record as verify."""
        self.ctx.event_log.append(
            new_event(
                self.ctx.clock,
                "lictor.verify.finished.v1",
                state["run_id"],
                scenario_id=state.get("current"),
                data={
                    "attempt": attempt,
                    "ok": False,
                    "failed_at": failed_at,
                    "control_durations": {},
                    "controls": {},
                    "reverted_files": [],
                    "error": error,
                    "error_path": None,
                    "candidate": None,
                    "tree": None,
                },
            )
        )
        return {
            "last_verify": {"ok": False, "failed_at": failed_at, "condensed_error": error},
            "last_error": error,
            "last_failed_at": failed_at,
            "failures": [*state.get("failures", []), None],
            "rejected_paths": [*state.get("rejected_paths", []), None],
        }

    def verify(self, state: GraphState) -> dict[str, Any]:
        current_id = state.get("current")
        if not current_id:
            return {}

        status = state.get("status", {})
        green_ids = [sid for sid, st in status.items() if st == "green"]
        all_sids = [s["id"] for s in state.get("scenarios", [])]
        pending_ids = [sid for sid in all_sids if status.get(sid, "pending") == "pending"]

        res = self.ctx.verify_attempt.execute(
            VerifyAttemptRequest(
                scenario_id=current_id,
                run_id=state["run_id"],
                role="implement",
                paths_writable=self.ctx.paths_writable,
                paths_driver=self.ctx.paths_driver,
                suppressions=self.ctx.suppressions,
                controls=self.ctx.controls,
                acceptance_run=self.ctx.acceptance_run,
                acceptance_report=self.ctx.acceptance_report,
                acceptance_timeout_s=self.ctx.acceptance_timeout_s,
                green_scenario_ids=green_ids,
                pending_scenario_ids=pending_ids,
                all_scenario_ids=all_sids,
                scenario_files=_scenario_files(state),
                error_max_chars=self.ctx.error_max_chars,
                attempt=state.get("attempt_slot") or max(state.get("attempt", 1), 1),
                acceptance_failure_exit_codes=self.ctx.acceptance_failure_exit_codes,
                forbidden_paths=self.ctx.forbidden_paths,
                banned_patterns=self.ctx.banned_patterns,
                tree=state.get("attempt_tree"),
                link=self.ctx.map_link,
            )
        )

        verify_duration = sum(cr.duration_ms for cr in res.control_results.values())
        control_durations = {k: v.duration_ms for k, v in res.control_results.items()}
        event = new_event(
            self.ctx.clock,
            "lictor.verify.finished.v1",
            state["run_id"],
            scenario_id=current_id,
            data={
                "attempt": state.get("attempt", 0),
                "ok": res.ok,
                "failed_at": res.failed_at,
                "control_durations": control_durations,
                "controls": control_summaries(res),
                "reverted_files": list(res.reverted_files),
                "error": res.condensed_error,
                "error_path": res.error_path,
                "evidence": res.evidence,
                "candidate": res.candidate.sha if res.candidate else None,
                "tree": res.candidate.tree if res.candidate else None,
                "collateral": list(res.collateral),
            },
        )
        self.ctx.event_log.append(event)
        discarded = list(res.reverted_files) if res.failed_at == "L0-rutas" else None
        rejected_paths = [*state.get("rejected_paths", []), discarded]
        needed = (
            [] if res.ok else paths_only_a_person_can_change(rejected_paths, self.ctx.paths_driver)
        )
        stop = (
            self._finish(state, int(ExitCode.PRECONDITION), person_only_reason(current_id, needed))
            if needed
            else {}
        )
        return {
            **stop,
            "rejected_paths": rejected_paths,
            "last_verify": {
                "ok": res.ok,
                "failed_at": res.failed_at,
                "condensed_error": res.condensed_error,
                "candidate": asdict(res.candidate) if res.candidate else None,
                "collateral": list(res.collateral),
            },
            "last_error": res.condensed_error,
            "last_failed_at": res.failed_at,
            "failures": [*state.get("failures", []), list(res.failure) if res.failure else None],
            "totals": {
                "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                "cost_usd": 0.0,
                "agent_duration_ms": 0,
                "verify_duration_ms": verify_duration,
            },
        }

    def commit(self, state: GraphState) -> dict[str, Any]:
        current_id = state.get("current")
        if not current_id:
            return {}

        last_verify = state.get("last_verify") or {}
        verified = last_verify.get("candidate")
        collateral = list(last_verify.get("collateral") or [])
        try:
            self.ctx.commit_green.execute(
                CommitGreenRequest(
                    scenario_id=current_id,
                    run_id=state["run_id"],
                    attempt=state.get("attempt", 1),
                    candidate=Candidate(**verified) if verified else None,
                    collateral=collateral,
                )
            )
        except CandidateError as exc:
            return {"agent_signal": "blocked", "agent_detail": str(exc), "resume_to": "commit"}
        greens = dict.fromkeys([current_id, *collateral], "green")
        return {"status": greens, "agent_signal": None, "anchor": self._anchor()}

    def quarantine(self, state: GraphState) -> dict[str, Any]:
        current_id = state.get("current")
        if not current_id:
            return {}

        conflict = state.get("agent_signal") == "spec_conflict"
        failures = [(f[0], f[1]) if f else None for f in state.get("failures", [])]
        reason: QuarantineReason = "intentos agotados"
        details = state.get("last_error") or ""
        if conflict:
            reason, details = "spec_conflict", state.get("spec_conflict") or ""
        elif suspicious_driver(failures, self.ctx.paths_driver):
            reason = "driver_sospechoso"
            origin = next(f[1] for f in reversed(failures) if f is not None)
            details = f"{_SUSPICIOUS_DRIVER.format(sid=current_id, origin=origin)}\n{details}"

        self.ctx.quarantine.execute(
            QuarantineRequest(
                scenario_id=current_id,
                run_id=state["run_id"],
                reason=reason,
                details=details,
            )
        )
        return {
            "status": {current_id: "quarantined"},
            "reasons": {current_id: reason},
        }

    def pause(self, state: GraphState) -> dict[str, Any]:
        reason = _PAUSE_REASONS.get(state.get("agent_signal") or "", "agent_unavailable")
        interrupt(
            {
                "reason": reason,
                "resume_to": state.get("resume_to"),
                "detail": state.get("agent_detail", ""),
            }
        )
        return {"agent_failures": 0, "agent_signal": None}

    def report(self, state: GraphState) -> dict[str, Any]:
        exit_val = state.get("exit_code")
        if exit_val is not None:
            return self._finish(state, exit_val, state.get("exit_reason", ""))
        code, reason = run_outcome(state.get("status", {}), state.get("reasons", {}))
        return self._finish(state, code, reason, reached_end=True)


def _green_of(
    records: Mapping[str, GreenRecord], classes: Mapping[str, str], sid: str
) -> dict[str, Any]:
    """Say which commit a green scenario is green from, and whether Lictor made it."""
    if classes.get(sid) != "green":
        return {}
    record = records[sid]
    green = {"commit": record.commit, "by_lictor": record.by_lictor}
    return {"green": {**green, "asserted_by": record.asserted_by}}


def _scenario_files(state: GraphState) -> dict[str, str]:
    """Each scenario's feature file: the one its test must execute it from."""
    return {s["id"]: s["file"] for s in state.get("scenarios", [])}


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def get_sqlite_saver(db_path: Path) -> SqliteSaver:
    """Instantiate and configure SqliteSaver with synchronous durability."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(_connect(db_path))
    saver.setup()
    return saver


def _database_file(conn: sqlite3.Connection) -> Path | None:
    """The file behind the connection's main database, or None if it lives in memory."""
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row is not None and row[2] else None


@contextmanager
def _reconnected(saver: SqliteSaver) -> Iterator[None]:
    """Read the checkpoint through a new connection once code that isn't trusted has run (F19).

    `IntegritySnapshot` restores a tampered checkpoint by writing its old bytes back. The run's
    connection kept descriptors to whatever the tampering left, unlinked `-wal` and `-shm`
    included, and reading the restored main file through them gave `no such table`: the run died
    instead of failing the attempt. So that connection is never read again: it is closed after the
    node and a new one opened. It stays open during the node on purpose: while it lives, a reader
    that may write can't be the last connection, so closing can't fold the WAL into the main file.

    Closing it must leave the files as they are, or the WAL would go into the main file and `-wal`
    would be deleted. The caller holds the saver's lock, so nothing is writing meanwhile.
    """
    try:
        yield
    finally:
        db_path = _database_file(saver.conn)
        if db_path is not None:
            saver.conn.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
            saver.conn.close()
            saver.conn = _connect(db_path)


def read_only_sqlite_saver(db_path: Path) -> SqliteSaver | None:
    """Open the checkpoint for reading only, or None if there is none: nothing here may write.

    A run in progress guards these files, so whoever only looks at them must leave them as they
    were. A connection that may write runs `setup()` and, when it closes, can move the WAL into the
    main file and delete it; read-only, SQLite can do neither. It may still create `-shm`, the
    index readers share, which `IntegritySnapshot` doesn't count.
    """
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
    )
    conn.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    saver = SqliteSaver(conn)
    saver.is_setup = True  # its tables exist: the run that wrote them created them
    return saver


class _Node(Protocol):
    """A graph node; LangGraph calls `state` by name, which a plain Callable can't say."""

    def __call__(self, state: GraphState) -> dict[str, Any]: ...


def _holding_checkpoint_writes(
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> Callable[[_Node], _Node]:
    """Keep the saver from writing while a node runs code that isn't trusted.

    `DURABILITY` waits for checkpoints, but not for `put_writes`, which LangGraph leaves to a thread
    of its own. Inside a node's `IntegritySnapshot` that write would read as tampering, and copying
    the WAL halfway through it would restore a torn file. The saver takes this lock for every
    write, so one in progress ends before the node starts and one not started waits for it to end.
    Nothing is lost if the run dies meanwhile: the checkpoint after those writes is already stored.
    Still under the lock, the saver reads through a new connection once the node ends.
    """

    def guarded() -> AbstractContextManager[object]:
        if not isinstance(checkpointer, SqliteSaver):
            return nullcontext()
        stack = ExitStack()
        stack.enter_context(checkpointer.lock)
        stack.enter_context(_reconnected(checkpointer))
        return stack

    def holding(node: _Node) -> _Node:
        def run(state: GraphState) -> dict[str, Any]:
            with guarded():
                return node(state)

        return run

    return holding


def create_graph(
    context: GraphContext,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build and compile the 11-node LangGraph execution graph."""
    nodes = _GraphNodes(context)

    def route_preflight(state: GraphState) -> str:
        if state.get("exit_code") is not None:
            return END
        return "load_spec"

    def route_implement_edge(state: GraphState) -> str:
        return route_implement(state, max_attempts=context.max_attempts)

    def route_pick_next_edge(state: GraphState) -> str:
        if state.get("current") is None:
            return "report"
        return "implement"

    def route_verify_edge(state: GraphState) -> str:
        return route_verify(state, max_attempts=context.max_attempts)

    untrusted = _holding_checkpoint_writes(checkpointer)
    builder = StateGraph(GraphState)

    # It builds the verification environment, which runs code the project's lock file brings in.
    builder.add_node("preflight", untrusted(nodes.preflight))
    builder.add_node("load_spec", nodes.load_spec)
    builder.add_node("build_driver", untrusted(nodes.build_driver))
    builder.add_node("red_check", untrusted(nodes.red_check))
    builder.add_node("pick_next", nodes.pick_next)
    builder.add_node("implement", untrusted(nodes.implement))
    builder.add_node("verify", untrusted(nodes.verify))
    builder.add_node("commit", nodes.commit)
    builder.add_node("quarantine", nodes.quarantine)
    builder.add_node("pause", nodes.pause)
    builder.add_node("report", nodes.report)

    builder.set_entry_point("preflight")

    builder.add_conditional_edges(
        "preflight",
        route_preflight,
        {"load_spec": "load_spec", END: END},
    )
    builder.add_conditional_edges(
        "load_spec",
        route_load_spec,
        {"red_check": "red_check", "build_driver": "build_driver", END: END},
    )
    builder.add_conditional_edges(
        "build_driver",
        route_build_driver,
        {"pause": "pause", "red_check": "red_check", "build_driver": "build_driver", END: END},
    )
    builder.add_conditional_edges(
        "red_check",
        route_red_check,
        {"build_driver": "build_driver", "pick_next": "pick_next", "pause": "pause", END: END},
    )
    builder.add_conditional_edges(
        "pick_next",
        route_pick_next_edge,
        {"implement": "implement", "report": "report"},
    )
    builder.add_conditional_edges(
        "implement",
        route_implement_edge,
        {
            "pause": "pause",
            "quarantine": "quarantine",
            "verify": "verify",
            "implement": "implement",
            END: END,
        },
    )
    builder.add_conditional_edges(
        "verify",
        route_verify_edge,
        {"commit": "commit", "implement": "implement", "quarantine": "quarantine", END: END},
    )
    builder.add_conditional_edges(
        "commit",
        route_commit,
        {"pick_next": "pick_next", "pause": "pause"},
    )
    builder.add_edge("quarantine", "pick_next")
    builder.add_conditional_edges(
        "pause",
        route_pause,
        {
            "implement": "implement",
            "build_driver": "build_driver",
            "commit": "commit",
            "red_check": "red_check",
        },
    )
    builder.add_edge("report", END)

    return builder.compile(checkpointer=checkpointer)
