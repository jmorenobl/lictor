"""BuildReport use case for generating .lictor/report.md from event log."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from lictor.application.ports import EventLog
from lictor.application.seal import hash_file, hash_tree
from lictor.domain.model import Event, JournalDamage, MutationScore
from lictor.domain.progress import DRIVER_OWNER, cost_text, duration_text, status_text
from lictor.domain.rules import describe_score

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
    owners = _owner_totals([*info["scenarios"].values(), info["driver"]])
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


def _row(ident: str, name: str, status: str, owner: Mapping[str, Any]) -> str:
    """One row of the table: what the agent's calls of one owner cost, or `-` without calls."""
    called = owner["calls"] > 0
    attempts = str(owner["attempts"]) if owner["attempts"] else "-"
    cells = [
        attempts,
        str(owner["calls"]) if called else "-",
        f"{owner['tokens_in']} / {owner['tokens_out']}" if called else "-",
        cost_text(owner["cost_usd"], _COST_DECIMALS) if called else "-",
        duration_text(owner["agent_ms"]) if called else "-",
        duration_text(owner["verify_ms"]) if owner["verify_ms"] else "-",
        _short(owner["commit"]),
    ]
    return f"| {ident} | {name} | {status} | {' | '.join(cells)} |"


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
    }


def _score_text(raw: object) -> str:
    """The score a control recorded, if it recorded one the journal can still read."""
    if not isinstance(raw, dict) or not raw:
        return ""
    try:
        return f", {describe_score(MutationScore(**raw))}"
    except TypeError:
        return ""


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


def _journal_lines(damage: JournalDamage) -> list[str]:
    if damage.total == 0:
        return ["- *Ninguna línea descartada.*"]
    return [
        f"- **Líneas descartadas**: {damage.total}",
        f"  - Corruptas, que no son JSON (escritura cortada o edición): {damage.corrupt}",
        f"  - Ajenas, JSON que no es un evento de Lictor (forjadas): {damage.foreign}",
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
        *(info["collateral"] or ["- *Ningún verde colateral.*"]),
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
            "| ID | Nombre | Estado | Intentos | Llamadas | Tokens (entrada / salida) | "
            "Coste | Tiempo agente | Tiempo verificación | Commit |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    if info["driver_seen"]:
        driver = info["driver"]
        lines.append(_row(DRIVER_OWNER, "Driver de aceptación", driver["status"], driver))
    for sid, s in sorted(info["scenarios"].items()):
        status = status_text(s["status"])
        if s["status"] == "green" and s["collateral"]:
            status = f"{status} (colateral)"
        lines.append(_row(sid, s["name"], status, s))
    if not owners:
        return lines
    tokens = f"{totals['tokens_in']} / {totals['tokens_out']}"
    return [
        *lines,
        (
            f"| **Total** | | | | {totals['calls']} | {tokens} | "
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
        """Keep the last event for each (type, runid, scenarioid, attempt, evidence).

        A node that runs again after a crash repeats its event, and only the last one counts. A
        step that ran twice left two evidence directories, whose names are never reused: both
        happened, so both stay.
        """
        seen: dict[tuple[str, str | None, str | None, Any, Any], Event] = {}
        for ev in events:
            attempt = ev.data.get("attempt")
            evidence = ev.data.get("evidence")
            where = evidence.get("path") if isinstance(evidence, dict) else None
            key = (ev.type, ev.runid, ev.scenarioid, attempt, where)
            seen[key] = ev
        return list(seen.values())

    def _render_report(self, events: list[Event], project_root: Path, run_id: str | None) -> str:
        data = self._aggregate_metrics(events)
        data["run_id"] = run_id
        retired = _retired_runs(self.event_log.events())
        data["altered_evidence"] = _altered_evidence(events, project_root, retired)
        data["retired_evidence"] = _retirement_notes(events, retired)
        data["journal_damage"] = self.event_log.damage()
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
            "measures": [],
            "scores": [],
            "skipped_controls": [],
            "rejected_drivers": [],
            "reopened": [],
            "reregistered": [],
            "by_hand": [],
            "collateral": [],
            "denials": [],
            "driver": {**_new_owner(), "status": "-"},
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
            "lictor.verify.finished.v1": self._process_verify_finished,
            "lictor.scenario.green.v1": self._on_scenario_green,
            "lictor.scenario.quarantined.v1": self._on_scenario_quarantined,
            "lictor.run.finished.v1": self._on_run_finished,
            "lictor.run.stopped.v1": self._on_run_stopped,
            "lictor.driver.rejected.v1": self._on_driver_rejected,
            "lictor.driver.sealed.v1": self._on_driver_sealed,
            "lictor.driver.checked.v1": self._on_driver_checked,
            "lictor.scenario.reopened.v1": self._on_scenario_reopened,
            "lictor.scenario.reregistered.v1": self._on_scenario_reregistered,
        }
        handler = handlers.get(ev.type)
        if handler is not None:
            handler(ev, info)

    def _on_run_started(self, ev: Event, info: dict[str, Any]) -> None:
        info["started_at"] = ev.time
        info["lictor_version"] = ev.data.get("lictor_version", "0.1.0")
        info["cli_version"] = ev.data.get("cli_version", info["cli_version"])
        info["profile"] = ev.data.get("profile", info["profile"])

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
        if ev.data.get("collateral"):
            info["collateral"].append(
                f"- **{sid}**: pasó sin llamar al agente en `{str(ev.data.get('commit', ''))[:12]}`"
            )
        if sid and sid in info["scenarios"]:
            info["scenarios"][sid]["status"] = "green"
            info["scenarios"][sid]["commit"] = str(ev.data.get("commit", "-"))
            info["scenarios"][sid]["collateral"] = bool(ev.data.get("collateral"))
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
        if scen is not None:
            scen["calls"] += 1
            tok = ev.data.get("tokens")
            if isinstance(tok, dict):
                # What the model read: its cache is most of an agent's input (F14).
                scen["tokens_in"] += sum(
                    int(tok.get(key, 0) or 0) for key in ("input", "cache_read", "cache_write")
                )
                scen["tokens_out"] += int(tok.get("output", 0) or 0)
            raw_cost = ev.data.get("cost_usd")
            if isinstance(raw_cost, (int, float, str)):
                scen["cost_usd"] += float(raw_cost)
            raw_dur = ev.data.get("duration_ms")
            if isinstance(raw_dur, (int, str)):
                scen["agent_ms"] += int(raw_dur)
            raw_att = ev.data.get("attempt")
            if isinstance(raw_att, (int, str)):
                scen["attempts"] = max(scen["attempts"], int(raw_att))

    def _process_verify_finished(self, ev: Event, info: dict[str, Any]) -> None:
        sid = ev.scenarioid
        if sid and sid in info["scenarios"]:
            scen = info["scenarios"][sid]
            durations = ev.data.get("control_durations")
            if isinstance(durations, dict):
                scen["verify_ms"] += sum(
                    int(v) for v in durations.values() if isinstance(v, (int, float, str))
                )

        if not ev.data.get("ok"):
            failed_at = ev.data.get("failed_at")
            if failed_at:
                info["failed_by_level"][str(failed_at)] += 1

        attempt = ev.data.get("attempt")
        outcome = "verde" if ev.data.get("ok") else str(ev.data.get("failed_at") or "falló")
        error_path = ev.data.get("error_path")
        error_cell = f"`{error_path}`" if error_path else "-"
        info["attempts"].append(f"| {sid} | {attempt} | {outcome} | {error_cell} |")

        controls = ev.data.get("controls")
        for control_id, control in controls.items() if isinstance(controls, dict) else ():
            if not isinstance(control, dict):
                continue
            if control.get("skipped"):
                info["skipped_controls"].append(
                    f"- **{control_id}** ({sid}, intento {attempt}): {control['skipped']}"
                )
                continue
            result = "correcto" if control.get("ok") else "fallo"
            score = _score_text(control.get("score"))
            if score:
                info["scores"].append(
                    f"- **{control_id}** ({sid}, intento {attempt}): {result}{score}"
                )
            if control.get("mode") == "measure":
                info["measures"].append(
                    f"- **{control_id}** ({sid}, intento {attempt}): {result}{score}, "
                    f"código {control.get('exit_code')}, salida en `{control.get('output')}`"
                )

    def _format_markdown(self, info: dict[str, Any]) -> str:
        greens = sum(1 for s in info["scenarios"].values() if s["status"] == "green")
        quarantined = len(info["quarantines"])
        pending = len(info["scenarios"]) - greens - quarantined
        owners = [*info["scenarios"].values(), *([info["driver"]] if info["driver_seen"] else [])]
        totals = _owner_totals(owners)
        driver_calls = info["driver"]["calls"]

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
            f"- **Llamadas al agente**: {totals['calls']} (driver: {driver_calls})",
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
