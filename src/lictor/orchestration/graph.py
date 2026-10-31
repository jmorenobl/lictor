"""LangGraph state graph orchestration for Lictor v0.1 execution cycle."""

import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.usecases.build_report import BuildReport, BuildReportRequest
from lictor.application.usecases.check_red import CheckRed, CheckRedRequest
from lictor.application.usecases.commit_green import CommitGreen, CommitGreenRequest
from lictor.application.usecases.implement_scenario import (
    ImplementScenario,
    ImplementScenarioRequest,
)
from lictor.application.usecases.prepare_driver import PrepareDriver, PrepareDriverRequest
from lictor.application.usecases.quarantine import Quarantine, QuarantineRequest
from lictor.application.usecases.verify_attempt import VerifyAttempt, VerifyAttemptRequest
from lictor.domain.errors import DriverError, ExitCode, SealTamperedError, SpecError
from lictor.domain.model import AgentStatus, Event, Scenario, ScenarioId
from lictor.domain.routing import (
    route_implement,
    route_load_spec,
    route_pause,
    route_red_check,
    route_verify,
)


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
    last_error: str | None
    last_failed_at: str | None
    last_verify: dict[str, Any] | None
    agent_signal: str | None
    agent_failures: int
    last_agent_status: AgentStatus | None
    agent_detail: str
    totals: Annotated[dict[str, Any], reduce_totals]
    exit_code: int | None
    seal_ok: bool
    resume_to: str | None


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
    controls: Sequence[Any]
    prepare_driver: PrepareDriver
    check_red: CheckRed
    implement_scenario: ImplementScenario
    verify_attempt: VerifyAttempt
    commit_green: CommitGreen
    quarantine: Quarantine
    spec_source: SpecSource
    vcs: Vcs
    executor: Executor
    event_log: EventLog
    clock: Clock
    profile_name: str = ""
    build_report: BuildReport | None = None


class _GraphNodes:
    """Encapsulates node execution methods with access to GraphContext."""

    def __init__(self, ctx: GraphContext) -> None:
        self.ctx = ctx

    def preflight(self, state: GraphState) -> dict[str, Any]:
        if state.get("exit_code") is not None:
            return {}
        if self.ctx.vcs.status():
            return {"exit_code": 10}
        health = self.ctx.executor.health()
        if not health.ok:
            return {
                "exit_code": int(ExitCode.ENVIRONMENT_UNAVAILABLE),
                "agent_detail": health.message or health.auth_status,
            }

        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.run.started.v1",
            source="lictor/0.1.0",
            time=self.ctx.clock.now_iso(),
            runid=state["run_id"],
            data={
                "lictor_version": "0.1.0",
                "cli_version": health.cli_version,
                "profile": self.ctx.profile_name,
                "config_hash": state.get("config_hash", ""),
            },
        )
        self.ctx.event_log.append(event)
        return {}

    def load_spec(self, state: GraphState) -> dict[str, Any]:
        features_dir = self.ctx.project_root / "features"
        try:
            scenarios = self.ctx.spec_source.load(features_dir)
        except SpecError:
            return {"exit_code": 10, "seal_ok": False}

        if not scenarios:
            return {"exit_code": 10, "seal_ok": False}

        seal_ok = self.ctx.spec_source.verify_seal(self.ctx.project_root, self.ctx.paths_driver)

        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.spec.loaded.v1",
            source="lictor/0.1.0",
            time=self.ctx.clock.now_iso(),
            runid=state["run_id"],
            data={
                "scenario_count": len(scenarios),
                "scenarios": [
                    {"id": s.id.value, "name": s.name, "file": s.file, "line": s.line}
                    for s in scenarios
                ],
            },
        )
        self.ctx.event_log.append(event)

        return {
            "scenarios": [
                {"id": s.id.value, "name": s.name, "file": s.file, "line": s.line}
                for s in scenarios
            ],
            "seal_ok": seal_ok,
            "driver_fresh": not seal_ok,
        }

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
                driver_attempts=state.get("driver_attempts", 0),
                last_error=state.get("last_error"),
                agent_failures=state.get("agent_failures", 0),
            )
        )

        return {
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
                )
            )
        except DriverError:
            return {"exit_code": 10, "last_verify": {"ok": False}}

        if res.ok:
            return {
                "last_verify": {"ok": True},
                "status": res.scenario_status,
                "last_error": None,
                "seal_ok": True,
            }
        return {
            "last_verify": {"ok": False},
            "last_error": res.last_error,
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
            return {"exit_code": 10}

        scenarios = state.get("scenarios", [])
        scen_dict = next((s for s in scenarios if s["id"] == current_id), None)
        if not scen_dict:
            return {"exit_code": 1}

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
                )
            )
        except SealTamperedError:
            return {"exit_code": 10}

        return {
            "attempt": res.attempt,
            "agent_signal": res.agent_signal,
            "agent_failures": res.agent_failures,
            "agent_detail": res.agent_result.text,
            "last_agent_status": res.agent_result.status,
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

    def verify(self, state: GraphState) -> dict[str, Any]:
        current_id = state.get("current")
        if not current_id:
            return {}

        green_ids = [sid for sid, st in state.get("status", {}).items() if st == "green"]
        all_sids = [s["id"] for s in state.get("scenarios", [])]

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
                all_scenario_ids=all_sids,
                error_max_chars=self.ctx.error_max_chars,
            )
        )

        verify_duration = sum(cr.duration_ms for cr in res.control_results.values())
        control_durations = {k: v.duration_ms for k, v in res.control_results.items()}
        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.verify.finished.v1",
            source="lictor/0.1.0",
            time=self.ctx.clock.now_iso(),
            runid=state["run_id"],
            scenarioid=current_id,
            data={
                "attempt": state.get("attempt", 0),
                "ok": res.ok,
                "failed_at": res.failed_at,
                "control_durations": control_durations,
                "reverted_files": list(res.reverted_files),
            },
        )
        self.ctx.event_log.append(event)
        return {
            "last_verify": {
                "ok": res.ok,
                "failed_at": res.failed_at,
                "condensed_error": res.condensed_error,
            },
            "last_error": res.condensed_error,
            "last_failed_at": res.failed_at,
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

        self.ctx.commit_green.execute(
            CommitGreenRequest(
                scenario_id=current_id,
                run_id=state["run_id"],
                attempt=state.get("attempt", 1),
                paths_writable=self.ctx.paths_writable,
            )
        )
        return {"status": {current_id: "green"}}

    def quarantine(self, state: GraphState) -> dict[str, Any]:
        current_id = state.get("current")
        if not current_id:
            return {}

        reason: Literal["intentos agotados", "spec_conflict"] = (
            "spec_conflict" if state.get("agent_signal") == "spec_conflict" else "intentos agotados"
        )
        details = state.get("last_error") or ""

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
        signal = state.get("agent_signal")
        reason = "rate_limited" if signal == "rate_limited" else "agent_unavailable"
        interrupt(
            {
                "reason": reason,
                "resume_to": state.get("resume_to"),
                "detail": state.get("agent_detail", ""),
            }
        )
        return {"agent_failures": 0}

    def report(self, state: GraphState) -> dict[str, Any]:
        status = state.get("status", {})
        reasons = state.get("reasons", {})

        code: int
        exit_val = state.get("exit_code")
        if exit_val is not None:
            code = exit_val
        elif any(r == "spec_conflict" for r in reasons.values()):
            code = 11
        elif any(r == "intentos agotados" for r in reasons.values()) or any(
            s == "quarantined" for s in status.values()
        ):
            code = 30
        elif all(s == "green" for s in status.values()) and status:
            code = 0
        else:
            code = 0

        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.run.finished.v1",
            source="lictor/0.1.0",
            time=self.ctx.clock.now_iso(),
            runid=state["run_id"],
            data={
                "exit_code": code,
                "totals": state.get("totals", {}),
            },
        )
        self.ctx.event_log.append(event)
        if self.ctx.build_report is not None:
            self.ctx.build_report.execute(
                BuildReportRequest(project_root=self.ctx.project_root, run_id=state["run_id"])
            )
        return {"exit_code": code}


def get_sqlite_saver(db_path: Path) -> SqliteSaver:
    """Instantiate and configure SqliteSaver with synchronous durability."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA synchronous = FULL")
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


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

    def route_build_driver_edge(state: GraphState) -> str:
        if state.get("agent_signal") in ("rate_limited", "unavailable"):
            return "pause"
        return "red_check"

    def route_pick_next_edge(state: GraphState) -> str:
        if state.get("current") is None:
            return "report"
        return "implement"

    def route_verify_edge(state: GraphState) -> str:
        return route_verify(state, max_attempts=context.max_attempts)

    builder = StateGraph(GraphState)

    builder.add_node("preflight", nodes.preflight)
    builder.add_node("load_spec", nodes.load_spec)
    builder.add_node("build_driver", nodes.build_driver)
    builder.add_node("red_check", nodes.red_check)
    builder.add_node("pick_next", nodes.pick_next)
    builder.add_node("implement", nodes.implement)
    builder.add_node("verify", nodes.verify)
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
        route_build_driver_edge,
        {"pause": "pause", "red_check": "red_check"},
    )
    builder.add_conditional_edges(
        "red_check",
        route_red_check,
        {"build_driver": "build_driver", "pick_next": "pick_next", END: END},
    )
    builder.add_conditional_edges(
        "pick_next",
        route_pick_next_edge,
        {"implement": "implement", "report": "report"},
    )
    builder.add_conditional_edges(
        "implement",
        route_implement,
        {"pause": "pause", "quarantine": "quarantine", "verify": "verify", END: END},
    )
    builder.add_conditional_edges(
        "verify",
        route_verify_edge,
        {"commit": "commit", "implement": "implement", "quarantine": "quarantine"},
    )
    builder.add_edge("commit", "pick_next")
    builder.add_edge("quarantine", "pick_next")
    builder.add_conditional_edges(
        "pause",
        route_pause,
        {"implement": "implement", "build_driver": "build_driver"},
    )
    builder.add_edge("report", END)

    return builder.compile(checkpointer=checkpointer)
