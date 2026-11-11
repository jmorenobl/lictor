from pathlib import Path

from lictor.application.ports import EventLog
from lictor.application.seal import hash_file, hash_tree
from lictor.application.usecases.build_report import (
    BuildReport,
    BuildReportRequest,
)
from lictor.domain.model import Event, JournalDamage


class InMemoryEventLog(EventLog):
    def __init__(self, events: list[Event], damage: JournalDamage | None = None) -> None:
        self._events = events
        self.journal_damage = damage or JournalDamage()

    def append(self, event: Event) -> None:
        self._events.append(event)

    def events(self, run_id: str | None = None) -> list[Event]:
        if run_id is None:
            return list(self._events)
        return [e for e in self._events if e.runid == run_id]

    def damage(self) -> JournalDamage:
        return self.journal_damage


def test_build_report_generates_markdown_with_metrics(tmp_path: Path) -> None:
    events = [
        Event(
            id="1",
            type="lictor.run.started.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:00Z",
            runid="run-1",
            data={
                "lictor_version": "0.1.0",
                "cli_version": "claude-code 1.0.0",
                "profile": "python-uv@0.1",
                "config_hash": "cfg123",
            },
        ),
        Event(
            id="2",
            type="lictor.spec.loaded.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:01Z",
            runid="run-1",
            data={
                "scenario_count": 2,
                "scenarios": [
                    {
                        "id": "IVA-001",
                        "name": "IVA general",
                        "file": "features/iva.feature",
                        "line": 5,
                    },
                    {
                        "id": "IVA-002",
                        "name": "IVA reducido",
                        "file": "features/iva.feature",
                        "line": 10,
                    },
                ],
            },
        ),
        # An earlier attempt of IVA-001 that failed and got repeated (deduplication check)
        Event(
            id="3",
            type="lictor.agent.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:10Z",
            runid="run-1",
            scenarioid="IVA-001",
            data={
                "role": "implement",
                "attempt": 1,
                "status": "ok",
                "model": "claude-3-5-sonnet",
                "prompt_sha": "sha_imp_1",
                "tokens": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0},
                "cost_usd": 0.001,
                "duration_ms": 1000,
            },
        ),
        Event(
            id="4",
            type="lictor.verify.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:15Z",
            runid="run-1",
            scenarioid="IVA-001",
            data={
                "attempt": 1,
                "ok": True,
                "failed_at": None,
                "control_durations": {"unit": 200},
                "reverted_files": [],
            },
        ),
        Event(
            id="5",
            type="lictor.scenario.green.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:20Z",
            runid="run-1",
            scenarioid="IVA-001",
            data={"commit": "abc1234", "attempt": 2},
        ),
        # IVA-002 failed and quarantined
        Event(
            id="6",
            type="lictor.agent.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:25Z",
            runid="run-1",
            scenarioid="IVA-002",
            data={
                "role": "implement",
                "attempt": 1,
                "status": "ok",
                "model": "claude-3-5-sonnet",
                "prompt_sha": "sha_imp_1",
                "tokens": {"input": 150, "output": 75, "cache_read": 0, "cache_write": 0},
                "cost_usd": 0.0015,
                "duration_ms": 1500,
            },
        ),
        Event(
            id="7",
            type="lictor.verify.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:30Z",
            runid="run-1",
            scenarioid="IVA-002",
            data={
                "attempt": 1,
                "ok": False,
                "failed_at": "L1",
                "control_durations": {"lint": 50},
                "reverted_files": [],
            },
        ),
        Event(
            id="8",
            type="lictor.scenario.quarantined.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:35Z",
            runid="run-1",
            scenarioid="IVA-002",
            data={"reason": "intentos agotados", "details": "SyntaxError in test"},
        ),
        Event(
            id="9",
            type="lictor.run.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:40Z",
            runid="run-1",
            data={
                "exit_code": 30,
                "totals": {
                    "tokens": {"input": 250, "output": 125},
                    "cost_usd": 0.0025,
                    "agent_duration_ms": 2500,
                    "verify_duration_ms": 250,
                },
            },
        ),
    ]

    event_log = InMemoryEventLog(events)
    use_case = BuildReport(event_log=event_log)

    res = use_case.execute(BuildReportRequest(project_root=tmp_path, run_id="run-1"))

    assert res.report_path == tmp_path / ".lictor" / "report.md"
    assert res.report_path.is_file()

    md = res.report_path.read_text(encoding="utf-8")
    assert "IVA-001" in md
    assert "IVA-002" in md
    assert "| 2 |" in md
    assert "30" in md
    assert "abc1234" in md
    assert "intentos agotados" in md
    assert "SyntaxError in test" in md
    assert "Duración total" in md
    assert "claude-code 1.0.0" in md
    assert "sha_imp_1" in md


def _event(event_type: str, data: dict[str, object], scenario: str | None = None) -> Event:
    return Event(
        id=event_type,
        type=event_type,
        source="lictor/0.1.0",
        time="2026-10-31T10:00:00Z",
        runid="run-1",
        scenarioid=scenario,
        data=data,
    )


def _report(tmp_path: Path, events: list[Event]) -> str:
    use_case = BuildReport(InMemoryEventLog(events))
    return use_case.execute(BuildReportRequest(project_root=tmp_path, run_id="run-1")).content


def test_report_gives_the_reason_of_the_exit(tmp_path: Path) -> None:
    finished = _event("lictor.run.finished.v1", {"exit_code": 10, "reason": "Árbol sucio"})

    report = _report(tmp_path, [finished])

    assert "**Código de salida**: 10" in report
    assert "**Motivo**: Árbol sucio" in report


def test_report_omits_the_reason_when_there_is_none(tmp_path: Path) -> None:
    assert "Motivo" not in _report(tmp_path, [_event("lictor.run.finished.v1", {"exit_code": 0})])


def test_report_gives_the_reason_of_a_stopped_run(tmp_path: Path) -> None:
    stopped = _event("lictor.run.stopped.v1", {"exit_code": 50, "reason": "Cuota agotada"})

    report = _report(tmp_path, [stopped])

    assert "**Código de salida**: 50" in report
    assert "**Motivo**: Cuota agotada" in report


def _verify_event(controls: dict[str, object], **data: object) -> Event:
    return _event(
        "lictor.verify.finished.v1",
        {"attempt": 1, "ok": True, "controls": controls, **data},
        "IVA-001",
    )


def test_report_lists_measure_controls_with_result_and_output(tmp_path: Path) -> None:
    controls: dict[str, object] = {
        "mut": {"ok": False, "mode": "measure", "exit_code": 7, "output": "r/mut.log"},
        "cov": {"ok": True, "mode": "measure", "exit_code": 0, "output": "r/cov.log"},
        "lint": {"ok": True, "mode": "block", "exit_code": 0, "output": "r/lint.log"},
    }

    report = _report(tmp_path, [_verify_event(controls)])

    section = report.split("## Controles en medición")[1].split("##")[0]
    assert "**mut** (IVA-001, intento 1): fallo, código 7, salida en `r/mut.log`" in section
    assert "**cov** (IVA-001, intento 1): correcto, código 0, salida en `r/cov.log`" in section
    assert "lint" not in section


def test_report_shows_the_mutation_score_beside_the_result(tmp_path: Path) -> None:
    score = {
        "killed": 923,
        "survived": 57,
        "timeout": 0,
        "no_tests": 0,
        "suspicious": 0,
        "segfault": 0,
        "skipped": 0,
        "total": 980,
        "percent": 100 * 923 / 980,
    }
    controls: dict[str, object] = {
        "mutation": {
            "ok": True,
            "mode": "measure",
            "exit_code": 0,
            "output": "r/m.log",
            "score": score,
        },
        "odd": {"ok": True, "mode": "measure", "exit_code": 0, "output": "r/o.log", "score": {}},
        "bad": {
            "ok": True,
            "mode": "measure",
            "exit_code": 0,
            "output": "r/b.log",
            "score": {"x": 1},
        },
    }

    report = _report(tmp_path, [_verify_event(controls)])

    section = report.split("## Controles en medición")[1].split("##")[0]
    assert (
        "**mutation** (IVA-001, intento 1): correcto, 94,1 % (923 de 980 muertos; 57 vivos, "
        "0 por tiempo), código 0, salida en `r/m.log`"
    ) in section
    assert "**odd** (IVA-001, intento 1): correcto, código 0" in section
    assert "**bad** (IVA-001, intento 1): correcto, código 0" in section


def test_report_shows_the_score_of_a_blocking_control(tmp_path: Path) -> None:
    score = {"killed": 3, "survived": 1, "total": 4, "percent": 75.0}
    controls: dict[str, object] = {
        "mutation": {"ok": False, "mode": "block", "exit_code": 0, "score": score},
        "lint": {"ok": True, "mode": "block", "exit_code": 0},
    }

    report = _report(tmp_path, [_verify_event(controls)])

    section = report.split("## Puntuaciones")[1].split("##")[0]
    assert "**mutation** (IVA-001, intento 1): fallo, 75,0 % (3 de 4 muertos; 1 vivos" in section
    assert "lint" not in section
    assert "*Ningún control en medición.*" in report


def test_report_shows_the_coverage_score(tmp_path: Path) -> None:
    score = {
        "kind": "coverage",
        "covered_lines": 9,
        "num_statements": 10,
        "covered_branches": 1,
        "num_branches": 2,
        "percent": 100 * 10 / 12,
    }
    controls: dict[str, object] = {
        "coverage": {"ok": True, "mode": "measure", "exit_code": 0, "score": score},
    }

    report = _report(tmp_path, [_verify_event(controls)])

    section = report.split("## Puntuaciones")[1].split("##")[0]
    assert "**coverage** (IVA-001, intento 1): correcto, 83,3 % (9 de 10 líneas, 1 de 2 ramas)" in (
        section
    )


def test_report_says_when_no_control_was_scored(tmp_path: Path) -> None:
    assert "*Ningún control registró puntuación.*" in _report(tmp_path, [_verify_event({})])


def test_report_says_when_nothing_was_measured(tmp_path: Path) -> None:
    assert "*Ningún control en medición.*" in _report(tmp_path, [_verify_event({})])


def test_report_lists_every_attempt_with_its_error(tmp_path: Path) -> None:
    failed = _event(
        "lictor.verify.finished.v1",
        {"attempt": 1, "ok": False, "failed_at": "acceptance", "error_path": "r/error.txt"},
        "IVA-001",
    )
    passed = _event("lictor.verify.finished.v1", {"attempt": 2, "ok": True}, "IVA-001")

    report = _report(tmp_path, [failed, passed])

    section = report.split("## Verificaciones")[1].split("##")[0]
    assert "| IVA-001 | 1 | acceptance | `r/error.txt` |" in section
    assert "| IVA-001 | 2 | verde | - |" in section


def test_report_lists_rejected_drivers(tmp_path: Path) -> None:
    rejected = _event(
        "lictor.driver.rejected.v1",
        {"attempt": 1, "reason": "mapeo", "final": False, "error_path": "r/error.txt"},
    )

    report = _report(tmp_path, [rejected])

    section = report.split("## Drivers rechazados")[1].split("##")[0]
    assert "- Intento 1: mapeo (`r/error.txt`)" in section


def test_report_says_when_no_driver_was_rejected(tmp_path: Path) -> None:
    assert "*Ningún driver rechazado.*" in _report(tmp_path, [])


def _verified(path: str, digest: str) -> Event:
    return Event(
        id="v",
        type="lictor.verify.finished.v1",
        source="lictor/0.1.0",
        time="2026-10-30T10:00:00Z",
        runid="run-1",
        scenarioid="IVA-001",
        data={"attempt": 1, "ok": True, "evidence": {"path": path, "hash": digest}},
    )


def _report(tmp_path: Path, events: list[Event]) -> str:
    use_case = BuildReport(event_log=InMemoryEventLog(events))
    return use_case.execute(BuildReportRequest(project_root=tmp_path, run_id="run-1")).content


def test_report_says_when_the_evidence_is_intact(tmp_path: Path) -> None:
    step = tmp_path / ".lictor" / "runs" / "paso"
    step.mkdir(parents=True)
    (step / "acceptance.log").write_text("ok", encoding="utf-8")

    report = _report(tmp_path, [_verified(".lictor/runs/paso", hash_tree(step))])

    assert "coincide con lo registrado" in report
    assert "Alterada" not in report


def test_report_names_evidence_changed_after_it_was_recorded(tmp_path: Path) -> None:
    step = tmp_path / ".lictor" / "runs" / "paso"
    step.mkdir(parents=True)
    log = step / "acceptance.log"
    log.write_text("ok", encoding="utf-8")
    digest = hash_tree(step)
    log.write_text("todo fue bien", encoding="utf-8")

    report = _report(tmp_path, [_verified(".lictor/runs/paso", digest)])

    assert "**Alterada tras registrarse**: `.lictor/runs/paso`" in report


def _agent_call(path: str, digest: str) -> Event:
    return Event(
        id="p1",
        type="lictor.agent.finished.v1",
        source="lictor",
        time="2026-10-30T10:00:00Z",
        runid="run-1",
        scenarioid="IVA-001",
        data={"role": "implement", "attempt": 1, "prompt": {"path": path, "hash": digest}},
    )


def test_report_names_a_prompt_changed_after_the_call(tmp_path: Path) -> None:
    prompt = tmp_path / ".lictor" / "runs" / "run-1" / "IVA-001" / "001-implement.prompt.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Objetivo: que pase el escenario IVA-001.", encoding="utf-8")
    path = prompt.relative_to(tmp_path).as_posix()
    digest = hash_file(prompt)

    assert "Alterada" not in _report(tmp_path, [_agent_call(path, digest)])

    prompt.write_text("Objetivo: cualquier cosa.", encoding="utf-8")
    assert f"- **Alterada tras registrarse**: `{path}`" in _report(
        tmp_path, [_agent_call(path, digest)]
    )
    prompt.unlink()
    assert f"- **Alterada tras registrarse**: `{path}`" in _report(
        tmp_path, [_agent_call(path, digest)]
    )


def test_report_names_evidence_that_is_gone(tmp_path: Path) -> None:
    report = _report(tmp_path, [_verified(".lictor/runs/borrado", "sha256:x")])

    assert "`.lictor/runs/borrado`" in report


def test_report_says_when_no_journal_line_was_discarded(tmp_path: Path) -> None:
    assert "Ninguna línea descartada" in _report(tmp_path, [])


def test_report_counts_discarded_lines_by_kind(tmp_path: Path) -> None:
    log = InMemoryEventLog([], JournalDamage(corrupt=2, foreign=5))
    use_case = BuildReport(event_log=log)

    report = use_case.execute(BuildReportRequest(project_root=tmp_path, run_id="run-1")).content

    assert "**Líneas descartadas**: 7" in report
    assert "(escritura cortada o edición): 2" in report
    assert "(forjadas): 5" in report


def test_report_lists_skipped_controls_with_the_reason(tmp_path: Path) -> None:
    controls: dict[str, object] = {
        "unit": {"ok": True, "mode": "block", "skipped": "no existe tests/unit"},
        "lint": {"ok": True, "mode": "block", "skipped": None},
    }

    report = _report(tmp_path, [_verify_event(controls)])

    section = report.split("## Controles omitidos")[1].split("##")[0]
    assert "- **unit** (IVA-001, intento 1): no existe tests/unit" in section
    assert "lint" not in section


def test_report_says_when_no_control_was_skipped(tmp_path: Path) -> None:
    assert "*Ningún control omitido.*" in _report(tmp_path, [_verify_event({})])


def test_report_lists_reopened_and_reregistered_scenarios(tmp_path: Path) -> None:
    loaded = _event("lictor.spec.loaded.v1", {"scenarios": [{"id": "IVA-001", "name": "General"}]})
    reopened = _event("lictor.scenario.reopened.v1", {"commit": "a" * 40}, "IVA-001")
    again = _event("lictor.scenario.reregistered.v1", {"commit": "b" * 40}, "IVA-001")

    report = _report(tmp_path, [loaded, reopened, again])

    section = report.split("## Escenarios reabiertos")[1].split("##")[0]
    assert f"- **IVA-001**: su especificación cambió desde el verde de `{'a' * 12}`" in section
    section = report.split("## Verdes registrados de nuevo")[1].split("##")[0]
    assert (
        "- **IVA-001**: sigue pasando con la especificación nueva; "
        f"registrado sin agente en `{'b' * 12}`" in section
    )
    assert "| IVA-001 | General | verde | - |" in report


def test_report_says_when_nothing_was_reopened_or_reregistered(tmp_path: Path) -> None:
    report = _report(tmp_path, [])
    assert "*Ningún escenario reabierto.*" in report
    assert "*Ningún verde registrado de nuevo.*" in report


def test_report_lists_collateral_greens(tmp_path: Path) -> None:
    loaded = _event("lictor.spec.loaded.v1", {"scenarios": [{"id": "IVA-002", "name": "R"}]})
    green = _event(
        "lictor.scenario.green.v1",
        {"commit": "c" * 40, "attempt": 0, "collateral": True},
        "IVA-002",
    )

    report = _report(tmp_path, [loaded, green])

    section = report.split("## Verdes colaterales")[1].split("##")[0]
    assert f"- **IVA-002**: pasó sin llamar al agente en `{'c' * 12}`" in section
    assert f"| IVA-002 | R | verde (colateral) | - | - | - | - | - | - | {'c' * 12} |" in report
    assert "*Ningún verde colateral.*" in _report(tmp_path, [])


def _verification(path: str, ok: bool, failed_at: str | None = None) -> Event:
    return _event(
        "lictor.verify.finished.v1",
        {"attempt": 1, "ok": ok, "failed_at": failed_at, "evidence": {"path": path, "hash": "h"}},
        "IVA-001",
    )


def test_a_verification_repeated_in_the_same_attempt_shows_twice_in_one_attempt(
    tmp_path: Path,
) -> None:
    loaded = _event("lictor.spec.loaded.v1", {"scenarios": [{"id": "IVA-001", "name": "General"}]})
    first = _verification("r/IVA-001/001-verify", False, "L1")
    again = _verification("r/IVA-001/001-verify-r1", True)
    call = _event("lictor.agent.finished.v1", {"role": "implement", "attempt": 1}, "IVA-001")

    report = _report(tmp_path, [loaded, call, first, again])

    rows = report.split("## Verificaciones")[1].split("##")[0]
    assert "| IVA-001 | 1 | L1 | - |" in rows
    assert "| IVA-001 | 1 | verde | - |" in rows
    assert "| IVA-001 | General |" in report
    assert report.split("| IVA-001 | General |")[1].split("|")[1].strip() == "1"


def test_an_event_repeated_by_a_resumed_node_counts_once(tmp_path: Path) -> None:
    same = _verification("r/IVA-001/001-verify", True)

    report = _report(tmp_path, [same, same])

    rows = report.split("## Verificaciones")[1].split("##")[0]
    assert rows.count("| IVA-001 | 1 | verde | - |") == 1


def _pruned(runs: list[str], keep: int = 3) -> Event:
    return Event(
        id="p",
        type="lictor.runs.pruned.v1",
        source="lictor/0.1.0",
        time="2026-11-04T09:00:00Z",
        runid="run-9",
        data={"pruned": runs, "keep_runs": keep},
    )


def test_evidence_of_a_retired_run_is_retired_not_altered(tmp_path: Path) -> None:
    report = _report(
        tmp_path, [_verified(".lictor/runs/run-1/IVA-001/001-verify", "x"), _pruned(["run-1"])]
    )

    assert "Alterada" not in report
    assert (
        "La evidencia de la ejecución run-1 se retiró el 2026-11-04T09:00:00Z al conservar las "
        "3 más recientes" in report
    )
    assert "coincide con lo registrado" not in report


def test_a_step_that_differs_in_a_retired_run_is_still_altered(tmp_path: Path) -> None:
    step = tmp_path / ".lictor" / "runs" / "run-1" / "IVA-001" / "001-verify"
    step.mkdir(parents=True)
    (step / "acceptance.log").write_text("cambiado", encoding="utf-8")
    path = ".lictor/runs/run-1/IVA-001/001-verify"

    report = _report(tmp_path, [_verified(path, "otro"), _pruned(["run-1"])])

    assert f"- **Alterada tras registrarse**: `{path}`" in report


def test_a_step_missing_from_a_run_that_wasnt_retired_is_altered(tmp_path: Path) -> None:
    path = ".lictor/runs/run-1/IVA-001/001-verify"

    report = _report(tmp_path, [_verified(path, "x"), _pruned(["run-0"])])

    assert f"- **Alterada tras registrarse**: `{path}`" in report
    assert "se retiró" not in report


def test_evidence_outside_runs_is_never_taken_for_retired(tmp_path: Path) -> None:
    report = _report(tmp_path, [_verified("otra/run-1/paso", "x"), _pruned(["run-1"])])

    assert "- **Alterada tras registrarse**: `otra/run-1/paso`" in report


def _denied(scenario: str | None, denials: list[dict[str, str]]) -> Event:
    return Event(
        id="d1",
        type="lictor.agent.denied.v1",
        source="lictor",
        time="2026-10-30T10:00:00Z",
        runid="run-1",
        scenarioid=scenario,
        data={
            "role": "implement" if scenario else "driver",
            "attempt": 1,
            "denials": denials,
        },
    )


def test_report_lists_what_the_backend_denied_the_agent(tmp_path: Path) -> None:
    heredoc = "python3 - <<'E'\n" + "x = 1\n" * 3 + "E"
    events = [
        _denied("IVA-001", [{"tool": "Bash", "target": "sed -i s/a/b/ src/`x`.py"}]),
        _denied(None, [{"tool": "Bash", "target": heredoc}, {"tool": "Write", "target": ""}]),
    ]

    lines = _report(tmp_path, events).splitlines()

    start = lines.index("## Denegaciones")
    assert lines[start + 1 : start + 4] == [
        "- **IVA-001, intento 1**: Bash `sed -i s/a/b/ src/'x'.py`",
        "- **driver, intento 1**: Bash `python3 - <<'E'…`",
        "- **driver, intento 1**: Write",
    ]


def test_report_says_when_nothing_was_denied(tmp_path: Path) -> None:
    assert "- *El backend no denegó nada al agente.*" in _report(tmp_path, [])


def test_report_cuts_a_long_denied_command(tmp_path: Path) -> None:
    report = _report(tmp_path, [_denied("IVA-001", [{"tool": "Bash", "target": "a" * 200}])])

    assert f"Bash `{'a' * 120}…`" in report
    assert "a" * 121 not in report


def _call(scenario: str | None, attempt: int, cost: float, tokens: dict[str, int]) -> Event:
    return Event(
        id=f"call-{scenario}-{attempt}",
        type="lictor.agent.finished.v1",
        source="lictor/0.2.0",
        time="2026-11-06T10:00:00Z",
        runid="run-1",
        scenarioid=scenario,
        data={
            "role": "implement" if scenario else "driver",
            "attempt": attempt,
            "tokens": tokens,
            "cost_usd": cost,
            "duration_ms": 61_000,
        },
    )


def _timed(event: Event, time: str) -> Event:
    return Event(
        id=event.id,
        type=event.type,
        source=event.source,
        time=time,
        runid=event.runid,
        scenarioid=event.scenarioid,
        data=event.data,
    )


def _summary_run() -> list[Event]:
    loaded = _event(
        "lictor.spec.loaded.v1",
        {"scenarios": [{"id": "IVA-001", "name": "General"}, {"id": "IVA-002", "name": "R"}]},
    )
    tokens = {"input": 8, "output": 1783, "cache_read": 40_000, "cache_write": 2000}
    return [
        _timed(
            _event("lictor.run.started.v1", {"lictor_version": "0.2.0"}), "2026-11-06T10:00:00Z"
        ),
        loaded,
        _call(None, 1, 0.072, tokens),
        _event("lictor.driver.sealed.v1", {"commit": "d" * 40}),
        _call("IVA-001", 1, 0.05, tokens),
        _event(
            "lictor.verify.finished.v1",
            {"attempt": 1, "ok": True, "control_durations": {"unit": 4000}},
            "IVA-001",
        ),
        _event("lictor.scenario.green.v1", {"commit": "a" * 40, "attempt": 1}, "IVA-001"),
        _event("lictor.scenario.green.v1", {"commit": "b" * 40, "collateral": True}, "IVA-002"),
        _timed(
            _event("lictor.run.finished.v1", {"exit_code": 0, "totals": {}}),
            "2026-11-06T10:02:07Z",
        ),
    ]


def test_report_gives_the_calls_and_cost_of_the_whole_run(tmp_path: Path) -> None:
    report = _report(tmp_path, _summary_run())

    assert "- **Llamadas al agente**: 2 (driver: 1)" in report
    assert "- **Coste total**: 0,1220 $" in report
    assert "- **Duración total**: 2 min 7 s (agente 2 min 2 s, verificación 4 s)" in report


def test_report_has_a_row_for_the_driver_and_one_for_the_total(tmp_path: Path) -> None:
    report = _report(tmp_path, _summary_run())

    assert (
        f"| driver | Driver de aceptación | sellado | 1 | 1 | 42008 / 1783 | 0,0720 $ | 1 min 1 s "
        f"| - | {'d' * 12} |"
    ) in report
    assert (
        f"| IVA-001 | General | verde | 1 | 1 | 42008 / 1783 | 0,0500 $ | 1 min 1 s | 4 s "
        f"| {'a' * 12} |"
    ) in report
    assert "| **Total** | | | | 2 | 84016 / 3566 | 0,1220 $ | 2 min 2 s | 4 s | |" in report
    assert "La entrada incluye la caché" in report
    assert "a" * 13 not in report


def test_report_names_statuses_in_spanish(tmp_path: Path) -> None:
    loaded = _event(
        "lictor.spec.loaded.v1",
        {"scenarios": [{"id": "IVA-001", "name": "General"}, {"id": "IVA-002", "name": "R"}]},
    )
    quarantined = _event("lictor.scenario.quarantined.v1", {"reason": "x"}, "IVA-002")

    report = _report(tmp_path, [loaded, quarantined])

    assert "| IVA-001 | General | pendiente |" in report
    assert "| IVA-002 | R | en cuarentena |" in report
    assert "| green |" not in report
    assert "| driver |" not in report  # a run that never called the driver has no row for it


def test_a_sealed_driver_that_was_only_checked_says_so(tmp_path: Path) -> None:
    report = _report(tmp_path, [_event("lictor.driver.checked.v1", {"attempt": 1})])

    assert "| driver | Driver de aceptación | comprobado | - | - | - | - | - | - | - |" in report


def test_a_driver_rejected_for_good_says_so(tmp_path: Path) -> None:
    rejected = _event(
        "lictor.driver.rejected.v1",
        {"attempt": 2, "reason": "sin_prueba", "final": True, "error_path": "e"},
    )

    report = _report(tmp_path, [_call(None, 2, 0.01, {}), rejected])

    assert "| driver | Driver de aceptación | rechazado | 2 | 1 |" in report


def test_each_run_keeps_a_copy_of_its_report(tmp_path: Path) -> None:
    """`report.md` is overwritten by every run; the copy lets two runs be compared (F03)."""
    first = Event(
        id="1",
        source="lictor/test",
        type="lictor.run.started.v1",
        time="2026-11-10T10:00:00Z",
        runid="run-1",
        data={},
    )
    second = Event(
        id="2",
        source="lictor/test",
        type="lictor.run.started.v1",
        time="2026-11-10T11:00:00Z",
        runid="run-2",
        data={},
    )
    use_case = BuildReport(event_log=InMemoryEventLog([first, second]))

    one = use_case.execute(BuildReportRequest(project_root=tmp_path, run_id="run-1"))
    two = use_case.execute(BuildReportRequest(project_root=tmp_path, run_id="run-2"))

    runs = tmp_path / ".lictor" / "runs"
    assert (runs / "run-1" / "report.md").read_text(encoding="utf-8") == one.content
    assert (runs / "run-2" / "report.md").read_text(encoding="utf-8") == two.content
    assert two.report_path.read_text(encoding="utf-8") == two.content
    assert one.content != two.content
