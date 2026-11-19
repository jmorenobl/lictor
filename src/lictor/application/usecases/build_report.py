"""BuildReport use case for generating .lictor/report.md from event log."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from lictor.application.ports import EventLog
from lictor.application.seal import hash_file, hash_tree
from lictor.domain.model import Event, JournalDamage
from lictor.domain.progress import (
    DRIVER_OWNER,
    REFACTOR_ROLE,
    calls_text,
    cost_text,
    duration_text,
    status_text,
)
from lictor.domain.refactor import REFACTORED
from lictor.domain.results import (
    OLDER_EVENTS,
    VERIFY_FINISHED,
    RecordedControl,
    RecordedExecuted,
    RecordedJudged,
    RecordedNotExecuted,
    RecordedRead,
    RecordedSkipped,
    RecordedUnjudged,
    acceptance_from_record,
    failed_run_mark,
    recorded_controls,
    recorded_duration,
    recorded_from_failed_run,
)
from lictor.domain.rules import describe_score, minimum_text

_MS_PER_SECOND: Final[int] = 1000
_SHORT_SHA: Final[int] = 12
_COST_DECIMALS: Final[int] = 4
_CACHE_NOTE = (
    "*La entrada incluye la caché: los tokens leídos y escritos en ella, que son casi toda la "
    "entrada de un agente.*"
)


def _owner_totals(owners: list[dict[str, Any]]) -> dict[str, int | float]:
    return {
        key: sum(owner[key] for owner in owners)
        for key in ("calls", "tokens_in", "tokens_out", "cost_usd", "agent_ms", "verify_ms")
    }


def _seconds_between(start: str, end: str) -> int | None:
    """Milliseconds from one event to another, if both times can be read."""
    try:
        elapsed = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    except ValueError:
        return None
    return int(elapsed.total_seconds() * _MS_PER_SECOND)


def _total_duration(info: dict[str, Any]) -> str:
    """The run's wall clock, from its start to its end, and how much of it went where.

    The agent's and the controls' times leave out what is neither, such as building the
    verification environment, so the wall clock is the one that matches what the person waited.
    """
    owners = _owner_totals(
        [*info["scenarios"].values(), info["driver"], *info["refactors"].values()]
    )
    split = (
        f"agente {duration_text(int(owners['agent_ms']))}, "
        f"verificación {duration_text(int(owners['verify_ms']))}"
    )
    start, end = info["started_at"], info["finished_at"]
    wall = _seconds_between(start, end) if start and end else None
    if wall is None:
        return split
    return f"{duration_text(wall)} ({split})"


def _short(commit: str) -> str:
    return commit[:_SHORT_SHA] if commit and commit != "-" else "-"


def _row(ident: str, name: str, status: str, owner: Mapping[str, Any], refactor: str = "-") -> str:
    """One row of the table: what the agent's calls of one owner cost, or `-` without calls."""
    called = owner["calls"] > 0
    attempts = str(owner["attempts"]) if owner["attempts"] else "-"
    cells = [
        refactor,
        attempts,
        str(owner["calls"]) if called else "-",
        f"{owner['tokens_in']} / {owner['tokens_out']}" if called else "-",
        cost_text(owner["cost_usd"], _COST_DECIMALS) if called else "-",
        duration_text(owner["agent_ms"]) if called else "-",
        duration_text(owner["verify_ms"]) if owner["verify_ms"] else "-",
        _short(owner["commit"]),
    ]
    return f"| {ident} | {name} | {status} | {' | '.join(cells)} |"


_REFACTOR_TEXT: Final[Mapping[str, str]] = {
    "applied": "aplicado",
    "discarded": "descartado",
    "unchanged": "sin cambios",
}


def _refactor_of(info: dict[str, Any], sid: str) -> dict[str, Any]:
    """What the refactor of one scenario ended in and cost, made on first sight."""
    refactors: dict[str, dict[str, Any]] = info["refactors"]
    return refactors.setdefault(sid, {**_new_owner(), "status": "", "failed_at": ""})


def _refactor_text(refactor: Mapping[str, Any] | None) -> str:
    """The refactor's cell: how it ended, and where a discarded one failed."""
    if refactor is None or not refactor["status"]:
        return "-" if refactor is None else "en curso"
    text = _REFACTOR_TEXT.get(refactor["status"], refactor["status"])
    return f"{text} ({refactor['failed_at']})" if refactor["failed_at"] else text


def _refactor_lines(info: Mapping[str, Any]) -> list[str]:
    """Each refactor with how it ended, its commit and what it cost (ADR-0010)."""
    lines = []
    for sid, refactor in sorted(info["refactors"].items()):
        commit = f" en `{_short(refactor['commit'])}`" if refactor["commit"] != "-" else ""
        spent = duration_text(refactor["agent_ms"] + refactor["verify_ms"])
        lines.append(
            f"- **{sid}**: {_refactor_text(refactor)}{commit}; {calls_text(refactor['calls'])}, "
            f"{cost_text(refactor['cost_usd'], _COST_DECIMALS)}, {spent}"
        )
    return lines or ["- *Ningún refactor.*"]


def _add_call(owner: dict[str, Any], data: Mapping[str, Any], *, count_attempt: bool) -> None:
    """Add one agent call to what its owner spent; a refactor's call spends no attempt."""
    owner["calls"] += 1
    tok = data.get("tokens")
    if isinstance(tok, dict):
        # What the model read: its cache is most of an agent's input (F14).
        owner["tokens_in"] += sum(
            int(tok.get(key, 0) or 0) for key in ("input", "cache_read", "cache_write")
        )
        owner["tokens_out"] += int(tok.get("output", 0) or 0)
    raw_cost = data.get("cost_usd")
    if isinstance(raw_cost, (int, float, str)):
        owner["cost_usd"] += float(raw_cost)
    raw_dur = data.get("duration_ms")
    if isinstance(raw_dur, (int, str)):
        owner["agent_ms"] += int(raw_dur)
    raw_att = data.get("attempt")
    if isinstance(raw_att, (int, str)) and count_attempt:
        owner["attempts"] = max(owner["attempts"], int(raw_att))


def _new_owner() -> dict[str, Any]:
    return {
        "status": "pending",
        "attempts": 0,
        "calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "agent_ms": 0,
        "verify_ms": 0,
        "commit": "-",
        "collateral": False,
        "already_met": False,
    }


@dataclass(frozen=True)
class BuildReportRequest:
    """Request payload for BuildReport use case."""

    project_root: Path
    run_id: str | None = None


@dataclass(frozen=True)
class BuildReportResult:
    """Result of BuildReport use case."""

    report_path: Path
    content: str


def _check_control_line(control: Mapping[str, Any]) -> str:
    """A control the run left to `lictor check`, as `run.started` listed it."""
    mode = "mide" if control.get("mode") == "measure" else "bloquea"
    minimum = control.get("min_score")
    # The journal is read as data: a `true` there is no minimum, though Python counts it an int.
    floor = ""
    if isinstance(minimum, int | float) and not isinstance(minimum, bool):
        floor = f", mínimo {minimum_text(minimum)} %"
    return f"- **{control.get('id', '')}** ({control.get('level', '')}, {mode}{floor})"


def _journal_lines(damage: JournalDamage) -> list[str]:
    if damage.total == 0:
        return ["- *Ninguna línea descartada.*"]
    return [
        f"- **Líneas descartadas**: {damage.total}",
        f"  - Corruptas, que no son JSON (escritura cortada o edición): {damage.corrupt}",
        f"  - Ajenas, JSON que no es un evento de Lictor (forjadas): {damage.foreign}",
        f"  - De una versión anterior, sin interpretar: {damage.older}",
        "- Una línea forjada con la forma exacta de un evento no se distingue y no cuenta aquí.",
    ]


_RUNS_DIR = (".lictor", "runs")
_BY_HAND_NOTE = (
    "- *Un verde cuyo commit no lleva `Lictor-Run` lo registró una persona, con `lictor register` "
    "o con git. La marca es informativa: cualquiera puede escribir trailers.*"
)
_EVIDENCE_MATCHES = "- *`runs/` coincide con lo registrado en la bitácora.*"


def _retired_runs(events: list[Event]) -> dict[str, Event]:
    """Map each run whose evidence a later run retired to the event that says so."""
    retired: dict[str, Event] = {}
    for event in events:
        pruned = event.data.get("pruned")
        if event.type == "lictor.runs.pruned.v1" and isinstance(pruned, list):
            retired.update({str(run_id): event for run_id in pruned})
    return retired


def _run_of(evidence_path: str) -> str:
    """Return the run an evidence path belongs to: `.lictor/runs/<run_id>/...`."""
    parts = Path(evidence_path).parts
    root = len(_RUNS_DIR)
    return parts[root] if len(parts) > root and parts[:root] == _RUNS_DIR else ""


def _altered_evidence(
    events: list[Event], project_root: Path, retired: Mapping[str, Event]
) -> list[str]:
    """List the steps in `runs/` that no longer hold what the journal recorded.

    A step missing from a run the retention retired isn't altered: the whole run went at once,
    and a step that differs, or is missing from a run that was kept, still is.
    """
    altered: list[str] = []
    for event in events:
        # A step is a directory; the prompt an agent call was given, a file beside its raw output.
        for key, exists, digest in (
            ("evidence", Path.is_dir, hash_tree),
            ("prompt", Path.is_file, hash_file),
        ):
            recorded = event.data.get(key)
            if not isinstance(recorded, dict) or "path" not in recorded:
                continue
            path = str(recorded["path"])
            if not exists(project_root / path):
                if _run_of(path) not in retired:
                    altered.append(path)
            elif digest(project_root / path) != recorded.get("hash"):
                altered.append(path)
    return sorted(set(altered))


_DENIED_TARGET_CHARS = 120


def _denied_target(denial: Mapping[str, Any]) -> str:
    """The first line of what was refused, short enough for a list and with no code fence."""
    lines = str(denial.get("target", "")).strip().splitlines()
    first = lines[0] if lines else ""
    if len(first) > _DENIED_TARGET_CHARS or len(lines) > 1:
        first = f"{first[:_DENIED_TARGET_CHARS]}…"
    return f"`{first.replace('`', "'")}`" if first else ""


def _green_sections(info: Mapping[str, Any]) -> list[str]:
    """The scenarios that changed or went green some other way than through the agent."""
    by_hand: list[str] = info["by_hand"]
    return [
        "",
        "## Escenarios reabiertos",
        *(info["reopened"] or ["- *Ningún escenario reabierto.*"]),
        "",
        "## Verdes registrados de nuevo",
        *(info["reregistered"] or ["- *Ningún verde registrado de nuevo.*"]),
        "",
        "## Verdes registrados a mano",
        *([*by_hand, _BY_HAND_NOTE] if by_hand else ["- *Ningún verde registrado a mano.*"]),
        "",
        "## Verdes colaterales",
        *(sorted(info["collateral"]) or ["- *Ningún verde colateral.*"]),
        "",
        "## Ya se cumplían",
        *(
            sorted(info["already_met"])
            or ["- *Ningún escenario nuevo que el código ya cumpliera.*"]
        ),
    ]


def _evidence_lines(info: Mapping[str, Any]) -> list[str]:
    altered = [f"- **Alterada tras registrarse**: `{path}`" for path in info["altered_evidence"]]
    retired: list[str] = info["retired_evidence"]
    return [*retired, *altered] or [_EVIDENCE_MATCHES]


def _retirement_notes(events: list[Event], retired: Mapping[str, Event]) -> list[str]:
    runs = sorted({event.runid for event in events if event.runid and event.runid in retired})
    return [
        f"- *La evidencia de la ejecución {run_id} se retiró el {retired[run_id].time} al "
        f"conservar las {retired[run_id].data.get('keep_runs')} más recientes; la bitácora "
        "conserva sus eventos y el hash de cada paso.*"
        for run_id in runs
    ]


def _scenario_table(
    info: Mapping[str, Any], owners: list[dict[str, Any]], totals: Mapping[str, int | float]
) -> list[str]:
    """A row per scenario, one for the driver if it was called, and the total."""
    lines = [
        (
            "| ID | Nombre | Estado | Refactor | Intentos | Llamadas | Tokens (entrada / salida) | "
            "Coste | Tiempo agente | Tiempo verificación | Commit |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    if info["driver_seen"]:
        driver = info["driver"]
        lines.append(_row(DRIVER_OWNER, "Driver de aceptación", driver["status"], driver))
    for sid, s in sorted(info["scenarios"].items()):
        status = status_text(s["status"])
        if s["status"] == "green" and s["already_met"]:
            status = f"{status} (ya se cumplía)"
        elif s["status"] == "green" and s["collateral"]:
            status = f"{status} (colateral)"
        refactor = info["refactors"].get(sid)
        lines.append(_row(sid, s["name"], status, s, _refactor_text(refactor)))
    refactors = list(info["refactors"].values())
    if refactors:
        spent = {**_new_owner(), **_owner_totals(refactors)}
        lines.append(_row("refactor", "Todos los refactors", "-", spent))
    if not owners:
        return lines
    tokens = f"{totals['tokens_in']} / {totals['tokens_out']}"
    return [
        *lines,
        (
            f"| **Total** | | | | | {totals['calls']} | {tokens} | "
            f"{cost_text(float(totals['cost_usd']), _COST_DECIMALS)} | "
            f"{duration_text(int(totals['agent_ms']))} | "
            f"{duration_text(int(totals['verify_ms']))} | |"
        ),
        "",
        _CACHE_NOTE,
    ]


class BuildReport:
    """Use case to aggregate journal events and write report.md."""

    def __init__(self, event_log: EventLog) -> None:
        self.event_log = event_log

    def execute(self, req: BuildReportRequest) -> BuildReportResult:
        """Read journal events, deduplicate, aggregate metrics, and write report.md."""
        raw_events = self.event_log.events(run_id=req.run_id)
        deduped = self._deduplicate_events(raw_events)

        report_md = self._render_report(deduped, req.project_root, req.run_id)

        lictor_dir = req.project_root / ".lictor"
        lictor_dir.mkdir(parents=True, exist_ok=True)
        report_path = lictor_dir / "report.md"
        report_path.write_text(report_md, encoding="utf-8")
        if req.run_id is not None:
            # The one above is the next run's too; this one stays with the run's evidence.
            own = lictor_dir / "runs" / req.run_id / "report.md"
            own.parent.mkdir(parents=True, exist_ok=True)
            own.write_text(report_md, encoding="utf-8")

        return BuildReportResult(report_path=report_path, content=report_md)

    def _deduplicate_events(self, events: list[Event]) -> list[Event]:
        """Keep the last event for each (type, runid, scenarioid, role, attempt, evidence).

        The role tells apart the implementer's call and the refactor's of the same attempt, which
        share scenario and attempt (ADR-0010).

        A node that runs again after a crash repeats its event, and only the last one counts. A
        step that ran twice left two evidence directories, whose names are never reused: both
        happened, so both stay.
        """
        seen: dict[tuple[str, str | None, str | None, Any, Any, Any], Event] = {}
        for ev in events:
            attempt = ev.data.get("attempt")
            evidence = ev.data.get("evidence")
            where = evidence.get("path") if isinstance(evidence, dict) else None
            key = (ev.type, ev.runid, ev.scenarioid, ev.data.get("role"), attempt, where)
            seen[key] = ev
        return list(seen.values())

    def _render_report(self, events: list[Event], project_root: Path, run_id: str | None) -> str:
        data = self._aggregate_metrics(events)
        data["run_id"] = run_id
        retired = _retired_runs(self.event_log.events())
        data["altered_evidence"] = _altered_evidence(events, project_root, retired)
        data["retired_evidence"] = _retirement_notes(events, retired)
        older = sum(1 for ev in events if ev.type in OLDER_EVENTS)
        data["journal_damage"] = replace(self.event_log.damage(), older=older)
        return self._format_markdown(data)

    def _aggregate_metrics(self, events: list[Event]) -> dict[str, Any]:
        info: dict[str, Any] = {
            "lictor_version": "0.1.0",
            "cli_version": "desconocido",
            "model": "desconocido",
            "profile": "desconocido",
            "prompt_shas": {},
            "exit_code": 0,
            "exit_reason": "",
            "totals": {},
            "scenarios": {},
            "quarantines": {},
            "failed_by_level": defaultdict(int),
            "attempts": [],
            "check_controls": [],
            "measures": [],
            "scores": [],
            "skipped_controls": [],
            "rejected_drivers": [],
            "reopened": [],
            "reregistered": [],
            "by_hand": [],
            "collateral": [],
            "already_met": [],
            "denials": [],
            "driver": {**_new_owner(), "status": "-"},
            # What each refactor ended in and cost, by scenario (ADR-0010): apart from its green.
            "refactors": {},
            "driver_seen": False,
            "started_at": "",
            "finished_at": "",
        }

        for ev in events:
            self._process_event(ev, info)

        return info

    def _process_event(self, ev: Event, info: dict[str, Any]) -> None:
        handlers: dict[str, Any] = {
            "lictor.run.started.v1": self._on_run_started,
            "lictor.spec.loaded.v1": self._on_spec_loaded,
            "lictor.agent.finished.v1": self._process_agent_finished,
            "lictor.agent.denied.v1": self._on_agent_denied,
            VERIFY_FINISHED: self._process_verify_finished,
            "lictor.scenario.green.v1": self._on_scenario_green,
            "lictor.scenario.quarantined.v1": self._on_scenario_quarantined,
            "lictor.run.finished.v1": self._on_run_finished,
            "lictor.run.stopped.v1": self._on_run_stopped,
            "lictor.driver.rejected.v1": self._on_driver_rejected,
            "lictor.driver.sealed.v1": self._on_driver_sealed,
            "lictor.driver.checked.v1": self._on_driver_checked,
            "lictor.scenario.reopened.v1": self._on_scenario_reopened,
            "lictor.scenario.reregistered.v1": self._on_scenario_reregistered,
            REFACTORED: self._on_scenario_refactored,
        }
        handler = handlers.get(ev.type)
        if handler is not None:
            handler(ev, info)

    def _on_run_started(self, ev: Event, info: dict[str, Any]) -> None:
        info["started_at"] = ev.time
        info["lictor_version"] = ev.data.get("lictor_version", "0.1.0")
        info["cli_version"] = ev.data.get("cli_version", info["cli_version"])
        info["profile"] = ev.data.get("profile", info["profile"])
        # A journal without the field, from before ADR-0007, had no `check` controls.
        listed = ev.data.get("check_controls", [])
        info["check_controls"] = [
            _check_control_line(c)
            for c in (listed if isinstance(listed, list) else [])
            if isinstance(c, dict)
        ]

    def _on_spec_loaded(self, ev: Event, info: dict[str, Any]) -> None:
        scens = ev.data.get("scenarios", [])
        if isinstance(scens, list):
            for s in scens:
                if isinstance(s, dict):
                    sid = str(s.get("id", ""))
                    if sid:
                        info["scenarios"][sid] = {**_new_owner(), "name": str(s.get("name", ""))}
                        self._previous_green(sid, s.get("green"), info)

    def _previous_green(self, sid: str, green: object, info: dict[str, Any]) -> None:
        """Show a scenario green from an earlier commit, and whether a person registered it."""
        if not isinstance(green, dict):
            return
        commit = str(green.get("commit", "-"))
        info["scenarios"][sid]["status"] = "green"
        info["scenarios"][sid]["commit"] = commit
        if green.get("by_lictor") is False:
            who = green.get("asserted_by")
            by = f" por {who}" if who else ""
            info["by_hand"].append(
                f"- **{sid}**: registrado a mano en `{commit[:12]}`{by}; Lictor no lo ha probado"
            )

    def _on_scenario_green(self, ev: Event, info: dict[str, Any]) -> None:
        sid = ev.scenarioid
        commit = str(ev.data.get("commit", ""))[:12]
        if ev.data.get("proof") == "interface":
            info["already_met"].append(
                f"- **{sid}**: el código ya lo cumplía; su test falla cuando la interfaz devuelve "
                f"otro valor, y pasó sin llamar al agente en `{commit}`"
            )
        elif ev.data.get("collateral"):
            after = ev.data.get("after_refactor")
            how = f"tras el refactor de {after}" if after else "sin llamar al agente"
            info["collateral"].append(
                f"- **{sid}**: pasó {how} en `{str(ev.data.get('commit', ''))[:12]}`"
            )
        if sid and sid in info["scenarios"]:
            info["scenarios"][sid]["status"] = "green"
            info["scenarios"][sid]["commit"] = str(ev.data.get("commit", "-"))
            info["scenarios"][sid]["collateral"] = bool(ev.data.get("collateral"))
            info["scenarios"][sid]["already_met"] = ev.data.get("proof") == "interface"
            raw_attempts = ev.data.get("attempt", ev.data.get("attempts", 1))
            if isinstance(raw_attempts, (int, str)):
                info["scenarios"][sid]["attempts"] = max(
                    info["scenarios"][sid]["attempts"], int(raw_attempts)
                )

    def _on_scenario_quarantined(self, ev: Event, info: dict[str, Any]) -> None:
        sid = ev.scenarioid
        if sid:
            if sid in info["scenarios"]:
                info["scenarios"][sid]["status"] = "quarantined"
            info["quarantines"][sid] = {
                "reason": str(ev.data.get("reason", "cuarentena")),
                "last_error": str(ev.data.get("last_error") or ev.data.get("details") or ""),
            }

    def _on_run_stopped(self, ev: Event, info: dict[str, Any]) -> None:
        info["finished_at"] = ev.time
        raw_exit = ev.data.get("exit_code", 0)
        if isinstance(raw_exit, (int, str)):
            info["exit_code"] = int(raw_exit)
        info["exit_reason"] = str(ev.data.get("reason", "") or "")

    def _on_driver_sealed(self, ev: Event, info: dict[str, Any]) -> None:
        info["driver_seen"] = True
        info["driver"]["status"] = "sellado"
        info["driver"]["commit"] = str(ev.data.get("commit", "-"))

    def _on_driver_checked(self, _: Event, info: dict[str, Any]) -> None:
        info["driver_seen"] = True
        info["driver"]["status"] = "comprobado"

    def _on_driver_rejected(self, ev: Event, info: dict[str, Any]) -> None:
        info["driver_seen"] = True
        if ev.data.get("final"):
            info["driver"]["status"] = "rechazado"
        info["rejected_drivers"].append(
            f"- Intento {ev.data.get('attempt')}: {ev.data.get('reason')} "
            f"(`{ev.data.get('error_path')}`)"
        )

    def _on_scenario_reopened(self, ev: Event, info: dict[str, Any]) -> None:
        info["reopened"].append(
            f"- **{ev.scenarioid}**: su especificación cambió desde el verde de "
            f"`{str(ev.data.get('commit', ''))[:12]}`"
        )

    def _on_scenario_reregistered(self, ev: Event, info: dict[str, Any]) -> None:
        sid = ev.scenarioid
        commit = str(ev.data.get("commit", "-"))
        if sid and sid in info["scenarios"]:
            info["scenarios"][sid]["status"] = "green"
            info["scenarios"][sid]["commit"] = commit
        info["reregistered"].append(
            f"- **{sid}**: sigue pasando con la especificación nueva; registrado sin agente "
            f"en `{commit[:12]}`"
        )

    def _on_run_finished(self, ev: Event, info: dict[str, Any]) -> None:
        self._on_run_stopped(ev, info)
        totals = ev.data.get("totals", {})
        if isinstance(totals, dict):
            info["totals"] = totals

    def _on_agent_denied(self, ev: Event, info: dict[str, Any]) -> None:
        denials = ev.data.get("denials")
        if not isinstance(denials, list):
            return
        # The owner says the role: a scenario's calls implement it, the driver's write it (F15).
        where = f"{ev.scenarioid or DRIVER_OWNER}, intento {ev.data.get('attempt', '?')}"
        for denial in denials:
            if isinstance(denial, dict):
                tool = str(denial.get("tool", "")) or "?"
                line = f"- **{where}**: {tool} {_denied_target(denial)}"
                info["denials"].append(line.rstrip())

    def _on_scenario_refactored(self, ev: Event, info: dict[str, Any]) -> None:
        if ev.scenarioid:
            refactor = _refactor_of(info, ev.scenarioid)
            refactor["status"] = str(ev.data.get("outcome", "") or "")
            refactor["failed_at"] = str(ev.data.get("failed_at") or "")
            refactor["commit"] = str(ev.data.get("commit") or "-")

    def _process_agent_finished(self, ev: Event, info: dict[str, Any]) -> None:
        role = str(ev.data.get("role", "") or "")
        sha = ev.data.get("prompt_sha")
        if role and sha:
            info["prompt_shas"][role] = str(sha)
        if ev.data.get("model"):
            info["model"] = str(ev.data.get("model"))
        if ev.data.get("cli_version"):
            info["cli_version"] = str(ev.data.get("cli_version"))

        sid = ev.scenarioid
        if sid is None:
            info["driver_seen"] = True
        scen = info["driver"] if sid is None else info["scenarios"].get(sid)
        if sid is not None and role == REFACTOR_ROLE:
            scen = _refactor_of(info, sid)
        if scen is not None:
            _add_call(scen, ev.data, count_attempt=role != REFACTOR_ROLE)

    def _process_verify_finished(self, ev: Event, info: dict[str, Any]) -> None:
        sid = ev.scenarioid
        controls = recorded_controls(ev.data.get("controls"))
        acceptance = acceptance_from_record(ev.data.get("acceptance"))
        refactor = ev.data.get("role") == REFACTOR_ROLE
        owner = _refactor_of(info, sid) if refactor and sid else info["scenarios"].get(sid or "")
        if owner is not None:
            owner["verify_ms"] += recorded_duration(controls, acceptance)

        # A refactor's failure costs no attempt: it is told in its own section.
        if not ev.data.get("ok") and not refactor:
            failed_at = ev.data.get("failed_at")
            if failed_at:
                info["failed_by_level"][str(failed_at)] += 1

        attempt = f"{ev.data.get('attempt')} (refactor)" if refactor else ev.data.get("attempt")
        outcome = "verde" if ev.data.get("ok") else str(ev.data.get("failed_at") or "falló")
        error_path = ev.data.get("error_path")
        error_cell = f"`{error_path}`" if error_path else "-"
        info["attempts"].append(f"| {sid} | {attempt} | {outcome} | {error_cell} |")

        for control_id, recorded in controls.items():
            where = f"**{control_id}** ({sid}, intento {attempt})"
            self._process_control(info, where, recorded, controls)

    @staticmethod
    def _process_control(
        info: dict[str, Any],
        where: str,
        recorded: RecordedControl,
        verification: Mapping[str, RecordedControl],
    ) -> None:
        """Show each result by what happened to it: skipped, not run, tampered, judged or read."""
        match recorded:
            case RecordedSkipped(reason=reason):
                info["skipped_controls"].append(f"- {where}: {reason}")
                return
            case RecordedNotExecuted(reason=reason):
                result, score = f"fallo, no se ejecutó: {reason}", ""
            case RecordedExecuted(judgement=RecordedUnjudged()):
                result, score = "manipuló el estado; sin veredicto", ""
            case RecordedExecuted(judgement=RecordedJudged() as judged):
                result = "correcto" if judged.passed else "fallo"
                score = f", {describe_score(judged.score)}" if judged.score else ""
                if score:
                    info["scores"].append(f"- {where}: {result}{score}")
            case RecordedRead(reads=reads) as read:
                verdict = "correcto" if read.passed else f"fallo: {read.reason or 'sin motivo'}"
                result = f"lee {reads}, {verdict}"
                score = f", {describe_score(read.score)}" if read.score else ""
                if score and recorded_from_failed_run(read, verification):
                    score += failed_run_mark(reads)
                if score:
                    # As a command control's: the verdict and the score once. The reason of a
                    # minimum not met already carries the score; it stays in error.txt.
                    passed = "correcto" if read.passed else "fallo"
                    info["scores"].append(f"- {where}: lee {reads}, {passed}{score}")
            case _:
                return
        if recorded.mode == "measure":
            ran = recorded.ran if isinstance(recorded, RecordedExecuted) else None
            how = f", código {ran.exit_code}, salida en `{ran.output_path}`" if ran else ""
            info["measures"].append(f"- {where}: {result}{score}{how}")

    def _format_markdown(self, info: dict[str, Any]) -> str:
        greens = sum(1 for s in info["scenarios"].values() if s["status"] == "green")
        quarantined = len(info["quarantines"])
        pending = len(info["scenarios"]) - greens - quarantined
        refactors = list(info["refactors"].values())
        owners = [
            *info["scenarios"].values(),
            *([info["driver"]] if info["driver_seen"] else []),
            *refactors,
        ]
        totals = _owner_totals(owners)
        driver_calls = info["driver"]["calls"]
        refactor_calls = sum(r["calls"] for r in refactors)

        lines = [
            "# Informe de ejecución de Lictor",
            "",
            "## Resumen",
            *([f"- **Ejecución**: {info['run_id']}"] if info.get("run_id") else []),
            f"- **Escenarios en verde**: {greens}",
            f"- **Escenarios en cuarentena**: {quarantined}",
            f"- **Escenarios pendientes**: {pending}",
            f"- **Código de salida**: {info['exit_code']}",
            *([f"- **Motivo**: {info['exit_reason']}"] if info["exit_reason"] else []),
            (
                f"- **Llamadas al agente**: {totals['calls']} (driver: {driver_calls}, "
                f"refactor: {refactor_calls})"
            ),
            f"- **Coste total**: {cost_text(float(totals['cost_usd']), _COST_DECIMALS)}",
            f"- **Duración total**: {_total_duration(info)}",
            "",
            "## Escenarios",
            *_scenario_table(info, owners, totals),
        ]

        lines.extend(["", "## Cuarentenas"])
        if not info["quarantines"]:
            lines.append("- *Ningún escenario en cuarentena.*")
        else:
            for q_id, q_data in sorted(info["quarantines"].items()):
                lines.append(f"- **{q_id}**: {q_data['reason']}")
                if q_data.get("last_error"):
                    lines.append("  ```")
                    lines.append(f"  {q_data['last_error']}")
                    lines.append("  ```")

        lines.extend(_green_sections(info))
        lines.extend(["", "## Refactors", *_refactor_lines(info)])

        lines.extend(["", "## Denegaciones"])
        lines.extend(info["denials"] or ["- *El backend no denegó nada al agente.*"])

        lines.extend(["", "## Intentos fallidos por nivel"])
        if not info["failed_by_level"]:
            lines.append("- *Sin fallos registrados.*")
        else:
            for lvl, count in sorted(info["failed_by_level"].items()):
                lines.append(f"- **{lvl}**: {count}")

        if info["attempts"]:
            lines.extend(["", "## Verificaciones", "| Escenario | Intento | Resultado | Error |"])
            lines.append("|---|---|---|---|")
            lines.extend(info["attempts"])

        lines.extend(["", "## Controles solo en `lictor check`"])
        lines.extend(
            info["check_controls"] or ["- *Ninguno: todos los controles corren en cada intento.*"]
        )

        lines.extend(["", "## Controles en medición"])
        lines.extend(info["measures"] or ["- *Ningún control en medición.*"])

        lines.extend(
            [
                "",
                "## Puntuaciones",
                *(info["scores"] or ["- *Ningún control registró puntuación.*"]),
            ]
        )

        lines.extend(["", "## Controles omitidos"])
        lines.extend(info["skipped_controls"] or ["- *Ningún control omitido.*"])

        lines.extend(["", "## Drivers rechazados"])
        lines.extend(info["rejected_drivers"] or ["- *Ningún driver rechazado.*"])

        lines.extend(["", "## Integridad de la bitácora"])
        lines.extend(_journal_lines(info["journal_damage"]))

        lines.extend(["", "## Integridad de la evidencia", *_evidence_lines(info)])

        lines.extend(
            [
                "",
                "## Versiones",
                f"- **Lictor**: {info['lictor_version']}",
                f"- **CLI**: {info['cli_version']}",
                f"- **Modelo**: {info['model']}",
                f"- **Perfil**: {info['profile']}",
            ]
        )

        if info["prompt_shas"]:
            lines.append("- **Plantillas de prompt**:")
            for role, sha in sorted(info["prompt_shas"].items()):
                lines.append(f"  - `{role}`: {sha}")

        lines.append("")
        return "\n".join(lines)
