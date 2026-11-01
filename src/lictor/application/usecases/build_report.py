"""BuildReport use case for generating .lictor/report.md from event log."""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from lictor.application.ports import EventLog
from lictor.application.seal import hash_tree
from lictor.domain.model import Event

_MS_PER_SECOND: Final[int] = 1000


def _calculate_total_duration(info: dict[str, Any]) -> str:
    total_ms = 0
    if info.get("totals") and isinstance(info["totals"], dict):
        t_agent = int(info["totals"].get("agent_duration_ms", 0) or 0)
        t_verify = int(info["totals"].get("verify_duration_ms", 0) or 0)
        total_ms = t_agent + t_verify
    if total_ms == 0:
        total_ms = sum(
            int(s.get("agent_ms", 0) or 0) + int(s.get("verify_ms", 0) or 0)
            for s in info["scenarios"].values()
        )
    return f"{total_ms}ms" if total_ms < _MS_PER_SECOND else f"{total_ms / _MS_PER_SECOND:.2f}s"


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


def _altered_evidence(events: list[Event], project_root: Path) -> list[str]:
    """List the steps in `runs/` that no longer hold what the journal recorded."""
    altered: list[str] = []
    for event in events:
        evidence = event.data.get("evidence")
        if not isinstance(evidence, dict) or "path" not in evidence:
            continue
        step_dir = project_root / str(evidence["path"])
        if not step_dir.is_dir() or hash_tree(step_dir) != evidence.get("hash"):
            altered.append(str(evidence["path"]))
    return sorted(set(altered))


class BuildReport:
    """Use case to aggregate journal events and write report.md."""

    def __init__(self, event_log: EventLog) -> None:
        self.event_log = event_log

    def execute(self, req: BuildReportRequest) -> BuildReportResult:
        """Read journal events, deduplicate, aggregate metrics, and write report.md."""
        raw_events = self.event_log.events(run_id=req.run_id)
        deduped = self._deduplicate_events(raw_events)

        report_md = self._render_report(deduped, req.project_root)

        lictor_dir = req.project_root / ".lictor"
        lictor_dir.mkdir(parents=True, exist_ok=True)
        report_path = lictor_dir / "report.md"
        report_path.write_text(report_md, encoding="utf-8")

        return BuildReportResult(report_path=report_path, content=report_md)

    def _deduplicate_events(self, events: list[Event]) -> list[Event]:
        """Keep the last event for each (type, runid, scenarioid, attempt)."""
        seen: dict[tuple[str, str | None, str | None, Any], Event] = {}
        for ev in events:
            attempt = ev.data.get("attempt")
            key = (ev.type, ev.runid, ev.scenarioid, attempt)
            seen[key] = ev
        return list(seen.values())

    def _render_report(self, events: list[Event], project_root: Path) -> str:
        data = self._aggregate_metrics(events)
        data["altered_evidence"] = _altered_evidence(events, project_root)
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
            "rejected_drivers": [],
        }

        for ev in events:
            self._process_event(ev, info)

        return info

    def _process_event(self, ev: Event, info: dict[str, Any]) -> None:
        handlers: dict[str, Any] = {
            "lictor.run.started.v1": self._on_run_started,
            "lictor.spec.loaded.v1": self._on_spec_loaded,
            "lictor.agent.finished.v1": self._process_agent_finished,
            "lictor.verify.finished.v1": self._process_verify_finished,
            "lictor.scenario.green.v1": self._on_scenario_green,
            "lictor.scenario.quarantined.v1": self._on_scenario_quarantined,
            "lictor.run.finished.v1": self._on_run_finished,
            "lictor.run.stopped.v1": self._on_run_stopped,
            "lictor.driver.rejected.v1": self._on_driver_rejected,
        }
        handler = handlers.get(ev.type)
        if handler is not None:
            handler(ev, info)

    def _on_run_started(self, ev: Event, info: dict[str, Any]) -> None:
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
                        info["scenarios"][sid] = {
                            "name": str(s.get("name", "")),
                            "status": "pending",
                            "attempts": 0,
                            "tokens_in": 0,
                            "tokens_out": 0,
                            "cost_usd": 0.0,
                            "agent_ms": 0,
                            "verify_ms": 0,
                            "commit": "-",
                        }

    def _on_scenario_green(self, ev: Event, info: dict[str, Any]) -> None:
        sid = ev.scenarioid
        if sid and sid in info["scenarios"]:
            info["scenarios"][sid]["status"] = "green"
            info["scenarios"][sid]["commit"] = str(ev.data.get("commit", "-"))
            raw_attempts = ev.data.get("attempt") or ev.data.get("attempts", 1)
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
        raw_exit = ev.data.get("exit_code", 0)
        if isinstance(raw_exit, (int, str)):
            info["exit_code"] = int(raw_exit)
        info["exit_reason"] = str(ev.data.get("reason", "") or "")

    def _on_driver_rejected(self, ev: Event, info: dict[str, Any]) -> None:
        info["rejected_drivers"].append(
            f"- Intento {ev.data.get('attempt')}: {ev.data.get('reason')} "
            f"(`{ev.data.get('error_path')}`)"
        )

    def _on_run_finished(self, ev: Event, info: dict[str, Any]) -> None:
        self._on_run_stopped(ev, info)
        totals = ev.data.get("totals", {})
        if isinstance(totals, dict):
            info["totals"] = totals

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
        if sid and sid in info["scenarios"]:
            scen = info["scenarios"][sid]
            tok = ev.data.get("tokens")
            if isinstance(tok, dict):
                scen["tokens_in"] += int(tok.get("input", 0) or 0)
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
            if isinstance(control, dict) and control.get("mode") == "measure":
                result = "correcto" if control.get("ok") else "fallo"
                info["measures"].append(
                    f"- **{control_id}** ({sid}, intento {attempt}): {result}, "
                    f"código {control.get('exit_code')}, salida en `{control.get('output')}`"
                )

    def _format_markdown(self, info: dict[str, Any]) -> str:
        greens = sum(1 for s in info["scenarios"].values() if s["status"] == "green")
        quarantined = len(info["quarantines"])
        pending = len(info["scenarios"]) - greens - quarantined
        duration_str = _calculate_total_duration(info)

        lines = [
            "# Informe de ejecución de Lictor",
            "",
            "## Resumen",
            f"- **Escenarios en verde**: {greens}",
            f"- **Escenarios en cuarentena**: {quarantined}",
            f"- **Escenarios pendientes**: {pending}",
            f"- **Código de salida**: {info['exit_code']}",
            *([f"- **Motivo**: {info['exit_reason']}"] if info["exit_reason"] else []),
            f"- **Duración total**: {duration_str}",
            "",
            "## Escenarios",
            (
                "| ID | Nombre | Estado | Intentos | Tokens (In/Out) | "
                "Coste | Tiempo Agente | Tiempo Verif | Commit |"
            ),
            "|---|---|---|---|---|---|---|---|---|",
        ]

        for sid, s in sorted(info["scenarios"].items()):
            tokens_str = f"{s['tokens_in']} / {s['tokens_out']}"
            cost_str = f"${s['cost_usd']:.4f}"
            agent_str = f"{s['agent_ms']}ms"
            verify_str = f"{s['verify_ms']}ms"
            lines.append(
                f"| {sid} | {s['name']} | {s['status']} | {s['attempts']} | "
                f"{tokens_str} | {cost_str} | {agent_str} | {verify_str} | {s['commit']} |"
            )

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

        lines.extend(["", "## Drivers rechazados"])
        lines.extend(info["rejected_drivers"] or ["- *Ningún driver rechazado.*"])

        lines.extend(["", "## Integridad de la evidencia"])
        lines.extend(
            [f"- **Alterada tras registrarse**: `{path}`" for path in info["altered_evidence"]]
            or ["- *`runs/` coincide con lo registrado en la bitácora.*"]
        )

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
