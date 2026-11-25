from pathlib import Path

import pytest

from lictor.application.ports import EventLog
from lictor.application.seal import hash_file, hash_tree
from lictor.application.usecases.build_report import (
    BuildReport,
    BuildReportRequest,
)
from lictor.domain.driver_measures import (
    DriverMeasures,
    InterfaceMeasure,
    RedMeasure,
    StubMeasure,
    measures_record,
)
from lictor.domain.gherkin_proof import GherkinFacts
from lictor.domain.model import Event, JournalDamage, StepCounts, UnreadValue


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
                "profile": "python-uv@0.2",
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
            type="lictor.verify.finished.v2",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:15Z",
            runid="run-1",
            scenarioid="IVA-001",
            data={
                "attempt": 1,
                "ok": True,
                "failed_at": None,
                "controls": {"unit": _executed(duration_ms=200)},
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
            type="lictor.verify.finished.v2",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:30Z",
            runid="run-1",
            scenarioid="IVA-002",
            data={
                "attempt": 1,
                "ok": False,
                "failed_at": "L1",
                "controls": {"lint": _executed(passed=False, duration_ms=50)},
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


_EXECUTED: dict[str, object] = {
    "outcome": "executed",
    "mode": "block",
    "exit_code": 0,
    "duration_ms": 0,
    "timed_out": False,
    "output_path": "r/x.log",
    "judgement": "judged",
    "passed": True,
    "reason": None,
    "score": None,
    "cases": {},
}


def _executed(
    *, passed: bool = True, output: str = "r/x.log", **fields: object
) -> dict[str, object]:
    """A control that ran and was judged, as `verify.finished.v2` records it."""
    failed = {} if passed else {"reason": "falló"}
    return {**_EXECUTED, "passed": passed, "output_path": output, **failed, **fields}


def _verify_event(controls: dict[str, object], **data: object) -> Event:
    return _event(
        "lictor.verify.finished.v2",
        {"attempt": 1, "ok": True, "controls": controls, **data},
        "IVA-001",
    )


def test_report_lists_measure_controls_with_result_and_output(tmp_path: Path) -> None:
    controls: dict[str, object] = {
        "mut": _executed(passed=False, mode="measure", exit_code=7, output="r/mut.log"),
        "cov": _executed(mode="measure", output="r/cov.log"),
        "lint": _executed(output="r/lint.log"),
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
        "kind": "mutation",
    }
    controls: dict[str, object] = {
        "mutation": _executed(mode="measure", output="r/m.log", score=score),
        "odd": _executed(mode="measure", output="r/o.log", score={}),
        "bad": _executed(mode="measure", output="r/b.log", score={"x": 1}),
    }

    report = _report(tmp_path, [_verify_event(controls)])

    section = report.split("## Controles en medición")[1].split("##")[0]
    assert (
        "**mutation** (IVA-001, intento 1): correcto, 94,1 % (923 de 980 muertos; 57 vivos, "
        "0 por tiempo), código 0, salida en `r/m.log`"
    ) in section
    # A record whose score can't be read doesn't follow the schema: it isn't shown as if it did.
    assert "odd" not in section
    assert "bad" not in section


def test_report_shows_the_score_of_a_blocking_control(tmp_path: Path) -> None:
    score = {"killed": 3, "survived": 1, "total": 4, "percent": 75.0, "kind": "mutation"}
    controls: dict[str, object] = {
        "mutation": _executed(passed=False, score=score),
        "lint": _executed(),
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
    controls: dict[str, object] = {"coverage": _executed(mode="measure", score=score)}

    report = _report(tmp_path, [_verify_event(controls)])

    section = report.split("## Puntuaciones")[1].split("##")[0]
    assert "**coverage** (IVA-001, intento 1): correcto, 83,3 % (9 de 10 líneas, 1 de 2 ramas)" in (
        section
    )


_COVERAGE_SCORE = {
    "kind": "coverage",
    "covered_lines": 3,
    "num_statements": 4,
    "covered_branches": 0,
    "num_branches": 0,
    "percent": 75.0,
}


def _read(*, passed: bool = True, **fields: object) -> dict[str, object]:
    """A reader's verdict, as `verify.finished.v2` records it: no exit code, duration or log."""
    failed = {} if passed else {"reason": "Sin informe de cobertura"}
    return {
        "outcome": "read",
        "mode": "measure",
        "reads": "unit",
        "passed": passed,
        **failed,
        "score": None,
        **fields,
    }


def test_report_shows_a_reader_as_reading_its_control(tmp_path: Path) -> None:
    """ADR-0008, point 8: «lee unit», with its verdict and its score."""
    controls: dict[str, object] = {
        "unit": _executed(output="r/unit.log"),
        "coverage": _read(score=_COVERAGE_SCORE),
    }

    report = _report(tmp_path, [_verify_event(controls)])

    scores = report.split("## Puntuaciones")[1].split("##")[0]
    assert "- **coverage** (IVA-001, intento 1): lee unit, correcto, 75,0 % (3 de 4 líneas)\n" in (
        scores
    )
    measures = report.split("## Controles en medición")[1].split("##")[0]
    assert "**coverage** (IVA-001, intento 1): lee unit, correcto, 75,0 %" in measures
    assert "código" not in measures


def test_report_marks_a_score_read_from_a_failed_run(tmp_path: Path) -> None:
    controls: dict[str, object] = {
        "unit": _executed(passed=False, mode="measure", exit_code=1),
        "coverage": _read(score=_COVERAGE_SCORE),
    }

    report = _report(tmp_path, [_verify_event(controls)])

    scores = report.split("## Puntuaciones")[1].split("##")[0]
    assert "lee unit, correcto, 75,0 % (3 de 4 líneas) (de un unit fallido)" in scores


def test_report_shows_why_a_reader_failed(tmp_path: Path) -> None:
    controls: dict[str, object] = {"unit": _executed(), "coverage": _read(passed=False)}

    report = _report(tmp_path, [_verify_event(controls)])

    measures = report.split("## Controles en medición")[1].split("##")[0]
    assert "**coverage** (IVA-001, intento 1): lee unit, fallo: Sin informe de cobertura" in (
        measures
    )


def test_a_score_below_its_minimum_is_shown_once(tmp_path: Path) -> None:
    """The reason of a minimum not met carries the score: «Puntuaciones» doesn't repeat it."""
    reason = "La cobertura, 75,0 % (3 de 4 líneas), no llega al mínimo de 80 %"
    controls: dict[str, object] = {
        "unit": _executed(),
        "coverage": _read(passed=False, mode="block", reason=reason, score=_COVERAGE_SCORE),
    }

    report = _report(tmp_path, [_verify_event(controls)])

    scores = report.split("## Puntuaciones")[1].split("##")[0]
    assert scores.strip() == (
        "- **coverage** (IVA-001, intento 1): lee unit, fallo, 75,0 % (3 de 4 líneas)"
    )


def test_report_says_when_no_control_was_scored(tmp_path: Path) -> None:
    assert "*Ningún control registró puntuación.*" in _report(tmp_path, [_verify_event({})])


def test_report_says_when_nothing_was_measured(tmp_path: Path) -> None:
    assert "*Ningún control en medición.*" in _report(tmp_path, [_verify_event({})])


def _started(**data: object) -> Event:
    return _event("lictor.run.started.v1", {"lictor_version": "0.4.0", **data})


def test_report_lists_the_controls_only_check_runs_before_the_measures(tmp_path: Path) -> None:
    started = _started(
        check_controls=[
            {"id": "mutation", "level": "L5", "mode": "block", "min_score": 95.0},
            {"id": "audit", "level": "L4", "mode": "measure", "min_score": None},
            {"id": "cov", "level": "L4", "mode": "block", "min_score": 90.5},
            {"id": "raro", "level": "L4", "mode": "block", "min_score": True},
        ]
    )

    report = _report(tmp_path, [started, _verify_event({})])

    assert report.index("## Controles solo en `lictor check`") < report.index(
        "## Controles en medición"
    )
    section = report.split("## Controles solo en `lictor check`")[1].split("##")[0]
    assert "- **mutation** (L5, bloquea, mínimo 95 %)" in section
    assert "- **audit** (L4, mide)" in section
    assert "- **cov** (L4, bloquea, mínimo 90,5 %)" in section
    assert "- **raro** (L4, bloquea)" in section


def test_check_controls_are_not_skipped_controls(tmp_path: Path) -> None:
    started = _started(check_controls=[{"id": "mutation", "level": "L5", "mode": "block"}])

    report = _report(tmp_path, [started, _verify_event({})])

    assert "mutation" not in report.split("## Controles omitidos")[1].split("##")[0]


@pytest.mark.parametrize("data", [{}, {"check_controls": []}, {"check_controls": "roto"}])
def test_a_journal_without_check_controls_lists_none(
    tmp_path: Path, data: dict[str, object]
) -> None:
    report = _report(tmp_path, [_started(**data), _verify_event({})])

    section = report.split("## Controles solo en `lictor check`")[1].split("##")[0]
    assert "*Ninguno: todos los controles corren en cada intento.*" in section


def test_report_lists_every_attempt_with_its_error(tmp_path: Path) -> None:
    failed = _event(
        "lictor.verify.finished.v2",
        {"attempt": 1, "ok": False, "failed_at": "acceptance", "error_path": "r/error.txt"},
        "IVA-001",
    )
    passed = _event("lictor.verify.finished.v2", {"attempt": 2, "ok": True}, "IVA-001")

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
        type="lictor.verify.finished.v2",
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
        "unit": {"outcome": "skipped", "mode": "block", "reason": "no existe tests/unit"},
        "lint": _executed(),
    }

    report = _report(tmp_path, [_verify_event(controls)])

    section = report.split("## Controles omitidos")[1].split("##")[0]
    assert "- **unit** (IVA-001, intento 1): no existe tests/unit" in section
    assert "lint" not in section


def test_report_tells_a_control_that_tampered_from_one_that_failed(tmp_path: Path) -> None:
    """ADR-0009: a result with no verdict is no failure of the control."""
    tampered = {**_executed(mode="measure"), "judgement": "unjudged", "changes": ["seal.json"]}
    for key in ("passed", "reason", "score", "cases"):
        tampered.pop(key)
    removed = {"outcome": "not_executed", "mode": "measure", "reason": "borró tests/unit"}

    report = _report(tmp_path, [_verify_event({"mut": tampered, "unit": removed})])

    section = report.split("## Controles en medición")[1].split("##")[0]
    assert "**mut** (IVA-001, intento 1): manipuló el estado; sin veredicto, código 0" in section
    assert "**unit** (IVA-001, intento 1): fallo, no se ejecutó: borró tests/unit" in section


def test_a_verification_of_an_earlier_version_is_counted_and_not_read(tmp_path: Path) -> None:
    older = _event(
        "lictor.verify.finished.v1",
        {"attempt": 1, "ok": False, "failed_at": "L1", "control_durations": {"lint": 9}},
        "IVA-001",
    )

    report = _report(tmp_path, [older])

    assert "De una versión anterior, sin interpretar: 1" in report
    assert "| IVA-001 | 1 |" not in report


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
    assert "| IVA-001 | General | verde | - | - |" in report


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
    assert f"| IVA-002 | R | verde (colateral) | - | - | - | - | - | - | - | {'c' * 12} |" in report
    assert "*Ningún verde colateral.*" in _report(tmp_path, [])


def test_collateral_greens_are_listed_by_id_not_by_commit(tmp_path: Path) -> None:
    """F38: DNI-024 went green in an earlier commit than DNI-005 and was listed first."""
    ids = ["DNI-024", "DNI-005", "DNI-014"]
    loaded = _event("lictor.spec.loaded.v1", {"scenarios": [{"id": i, "name": i} for i in ids]})
    greens = [
        _event("lictor.scenario.green.v1", {"commit": "c" * 40, "collateral": True}, sid)
        for sid in ids
    ]

    section = _report(tmp_path, [loaded, *greens]).split("## Verdes colaterales")[1].split("##")[0]

    listed = [line.split("**")[1] for line in section.splitlines() if line.startswith("- **")]
    assert listed == ["DNI-005", "DNI-014", "DNI-024"]


def _verification(path: str, ok: bool, failed_at: str | None = None) -> Event:
    return _event(
        "lictor.verify.finished.v2",
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
    assert report.split("| IVA-001 | General |")[1].split("|")[3].strip() == "1"


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
            "lictor.verify.finished.v2",
            {"attempt": 1, "ok": True, "controls": {"unit": _executed(duration_ms=4000)}},
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

    assert "- **Llamadas al agente**: 2 (driver: 1, refactor: 0)" in report
    assert "- **Coste total**: 0,1220 $" in report
    assert "- **Duración total**: 2 min 7 s (agente 2 min 2 s, verificación 4 s)" in report


def test_report_has_a_row_for_the_driver_and_one_for_the_total(tmp_path: Path) -> None:
    report = _report(tmp_path, _summary_run())

    assert (
        "| driver | Driver de aceptación | sellado | - | 1 | 1 | 42008 / 1783 | 0,0720 $ "
        f"| 1 min 1 s | - | {'d' * 12} |"
    ) in report
    assert (
        f"| IVA-001 | General | verde | - | 1 | 1 | 42008 / 1783 | 0,0500 $ | 1 min 1 s | 4 s "
        f"| {'a' * 12} |"
    ) in report
    assert "| **Total** | | | | | 2 | 84016 / 3566 | 0,1220 $ | 2 min 2 s | 4 s | |" in report
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

    assert (
        "| driver | Driver de aceptación | comprobado | - | - | - | - | - | - | - | - |" in report
    )


def test_a_driver_rejected_for_good_says_so(tmp_path: Path) -> None:
    rejected = _event(
        "lictor.driver.rejected.v1",
        {"attempt": 2, "reason": "sin_prueba", "final": True, "error_path": "e"},
    )

    report = _report(tmp_path, [_call(None, 2, 0.01, {}), rejected])

    assert "| driver | Driver de aceptación | rechazado | - | 2 | 1 |" in report


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


# The refactor after each implemented green (ADR-0010): its own column, row and section.


def _refactor_run(outcome: str, failed_at: str | None = None) -> list[Event]:
    tokens = {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0}
    refactor_call = _call("IVA-001", 1, 0.03, tokens)
    refactor_call = Event(
        id="refactor-call",
        type=refactor_call.type,
        source=refactor_call.source,
        time=refactor_call.time,
        runid=refactor_call.runid,
        scenarioid="IVA-001",
        data={**refactor_call.data, "role": "refactor", "duration_ms": 2000},
    )
    refactor_verify = _event(
        "lictor.verify.finished.v2",
        {
            "role": "refactor",
            "attempt": 1,
            "ok": failed_at is None,
            "failed_at": failed_at,
            "controls": {"unit": _executed(duration_ms=3000)},
        },
        "IVA-001",
    )
    refactored = _event(
        "lictor.scenario.refactored.v1",
        {
            "outcome": outcome,
            "attempt": 1,
            "commit": "e" * 40 if outcome == "applied" else None,
            "failed_at": failed_at,
            "collateral": [],
        },
        "IVA-001",
    )
    run = _summary_run()
    return [*run[:-1], refactor_call, refactor_verify, refactored, run[-1]]


def test_the_implementers_call_and_the_refactors_are_both_kept(tmp_path: Path) -> None:
    """Same scenario and attempt: only the role tells them apart, and both count."""
    report = _report(tmp_path, _refactor_run("applied"))

    assert "- **Llamadas al agente**: 3 (driver: 1, refactor: 1)" in report
    assert "- **Coste total**: 0,1520 $" in report


def test_the_scenario_row_keeps_its_own_spend_and_says_how_its_refactor_ended(
    tmp_path: Path,
) -> None:
    report = _report(tmp_path, _refactor_run("applied"))

    assert (
        f"| IVA-001 | General | verde | aplicado | 1 | 1 | 42008 / 1783 | 0,0500 $ | 1 min 1 s "
        f"| 4 s | {'a' * 12} |"
    ) in report
    assert (
        "| refactor | Todos los refactors | - | - | - | 1 | 1 / 2 | 0,0300 $ | 2 s | 3 s | - |"
        in (report)
    )
    section = report.split("## Refactors")[1].split("##")[0]
    assert f"- **IVA-001**: aplicado en `{'e' * 12}`; 1 llamada, 0,0300 $, 5 s" in section


def test_a_discarded_refactor_names_where_it_failed_and_costs_no_attempt(tmp_path: Path) -> None:
    report = _report(tmp_path, _refactor_run("discarded", "L0-regresión"))

    assert "| IVA-001 | General | verde | descartado (L0-regresión) | 1 |" in report
    assert "- **IVA-001**: descartado (L0-regresión); 1 llamada" in report
    levels = report.split("## Intentos fallidos por nivel")[1].split("##")[0]
    assert "L0-regresión" not in levels
    assert "| IVA-001 | 1 (refactor) | L0-regresión |" in report


def test_a_run_without_refactors_says_so(tmp_path: Path) -> None:
    report = _report(tmp_path, _summary_run())

    assert "- *Ningún refactor.*" in report.split("## Refactors")[1]


def test_report_lists_the_scenarios_the_code_already_met(tmp_path: Path) -> None:
    loaded = _event("lictor.spec.loaded.v1", {"scenarios": [{"id": "IVA-002", "name": "R"}]})
    green = _event(
        "lictor.scenario.green.v1",
        {"commit": "c" * 40, "attempt": 0, "collateral": True, "proof": "interface"},
        "IVA-002",
    )

    report = _report(tmp_path, [loaded, green])

    section = report.split("## Ya se cumplían")[1].split("##")[0]
    assert "- **IVA-002**: el código ya lo cumplía; su test falla cuando" in section
    assert f"`{'c' * 12}`" in section
    assert "verde (ya se cumplía)" in report
    assert "*Ningún verde colateral.*" in report
    assert "*Ningún escenario nuevo que el código ya cumpliera.*" in _report(tmp_path, [])


def test_report_names_the_tests_the_stubs_accepted_unchecked(tmp_path: Path) -> None:
    sealed = _event("lictor.driver.sealed.v1", {"commit": "d" * 40, "stub_unchecked": ["MUL-006"]})

    report = _report(tmp_path, [sealed])

    section = report.split("## Tests aceptados sin comprobar")[1].split("##")[0]
    assert "Lictor aceptó MUL-006 sin comprobarlos" in section
    assert "*Ninguno: cada test pendiente del driver se comprobó.*" in _report(tmp_path, [])


def _measured(attempt: int, step: str, measures: DriverMeasures) -> Event:
    data: dict[str, object] = {"attempt": attempt, "step": step, **measures_record(measures)}
    return _event("lictor.driver.measured.v1", data)


_MEASURES = DriverMeasures(
    red={"PRE-008": RedMeasure("then", "sin_implementar")},
    stubs={
        "PRE-008": StubMeasure({"None": "entonces", "True": "no_aserta"}, "probado"),
        "MUL-006": StubMeasure({"None": "pasa", "True": "pasa"}, "a_la_interfaz", "sin_alterar"),
    },
    interface={
        "PRE-004": InterfaceMeasure(
            outcome="sin_alterar",
            calls=StepCounts(given=3, when=1, then=1),
            altered={"mas": StepCounts(), "menos": StepCounts()},
            failed_step={"mas": None, "menos": None},
        ),
        "PRE-001": InterfaceMeasure(
            outcome="probado",
            calls=StepCounts(given=1, when=1, then=2),
            altered={"mas": StepCounts(when=1), "menos": StepCounts(when=1)},
            failed_step={"mas": "then", "menos": "when"},
        ),
        "APA-001": InterfaceMeasure(
            outcome="fuera_del_entonces",
            calls=StepCounts(given=2, when=1),
            altered={"mas": StepCounts(when=1), "menos": StepCounts(when=1)},
            failed_step={"mas": "when", "menos": "when"},
        ),
        "CLI-001": InterfaceMeasure(
            outcome="a_traves",
            calls=StepCounts(given=3),
            altered={"mas": StepCounts(), "menos": StepCounts()},
            failed_step={"mas": None, "menos": None},
            via=StepCounts(given=4, when=31),
            handed=StepCounts(then=1),
            libraries=("typer.testing",),
        ),
        "ENR-005": InterfaceMeasure(
            outcome="solo_el_valor",
            calls=StepCounts(when=1),
            altered={"mas": StepCounts(when=1), "menos": StepCounts(when=1)},
            failed_step={"mas": None, "menos": None},
            interface="sobrevive",
            gherkin=GherkinFacts(4, read=(0, 1, 2), inputs=(3,), unreceived=1),
        ),
        "FP-002": InterfaceMeasure(
            outcome="provocada",
            calls=StepCounts(then=2),
            altered={"mas": StepCounts(then=1), "menos": StepCounts(then=1)},
            failed_step={"mas": "then", "menos": "then"},
            interface="probado",
            provoked=("mas", "menos"),
            gherkin=GherkinFacts(1),
        ),
        "VER-005": InterfaceMeasure(
            outcome="ignora_el_valor",
            calls=StepCounts(when=1),
            altered={"mas": StepCounts(when=1), "menos": StepCounts(when=1)},
            failed_step={"mas": "then", "menos": "then"},
            interface="probado",
            gherkin=GherkinFacts(2, unread=(UnreadValue("paso", "tipo", "alta"),)),
        ),
    },
)


def test_report_shows_what_the_red_saw_and_what_c_decided_of_each_driver_measured(
    tmp_path: Path,
) -> None:
    events = [
        _measured(2, ".lictor/runs/run-1/driver/002-red", DriverMeasures(red={})),
        _measured(1, ".lictor/runs/run-1/driver/001-red", _MEASURES),
    ]

    report = _report(tmp_path, events)

    section = report.split("## Medidas del driver")[1].split("\n## ")[0]
    assert "ADR-0012" in section
    first, second = section.split("### Intento 2")
    assert "### Intento 1 (`.lictor/runs/run-1/driver/001-red`)" in first
    assert "- **Con C**: 1 aceptado, 1 aceptado sin comprobar" in first
    assert (
        "- **Interfaz**: 1 `a_traves`, 1 `fuera_del_entonces`, 1 `ignora_el_valor`, "
        "1 `probado`, 1 `provocada`, 1 `sin_alterar`, 1 `solo_el_valor`" in first
    )
    assert (
        "| PRE-001 | - | - | - | `probado` | mas: Entonces; menos: Cuando | 1 / 1 / 2 / 0 | - "
        "| - |" in first
    )
    assert (
        "| ENR-005 | - | - | - | `solo_el_valor` (interfaz: `sobrevive`) | mas: sin fallo en un "
        "paso; menos: sin fallo en un paso | 0 / 1 / 0 / 0 | - | 3 de 4 leídos, 1 entrada, "
        "1 no recibido |" in first
    )
    assert (
        "| FP-002 | - | - | - | `provocada` (interfaz: `probado`), lanzó por lo alterado en "
        "mas, menos |" in first
    )
    assert "| 0 / 0 / 2 / 0 | - | 1 valor |" in first
    assert "| VER-005 | - | - | - | `ignora_el_valor` (interfaz: `probado`) |" in first
    assert "| - | 2 valores, 1 sin leer |" in first
    assert (
        "| CLI-001 | - | - | - | `a_traves` | mas: sin fallo en un paso; menos: sin fallo en un "
        "paso | 3 / 0 / 0 / 0 | 31 desde el código, 1 de lo que entrega (typer.testing) | - |"
        in first
    )
    assert "| APA-001 | - | - | - | `fuera_del_entonces` | mas: Cuando; menos: Cuando |" in first
    assert (
        "| PRE-004 | - | - | - | `sin_alterar` | mas: sin fallo en un paso; menos: sin fallo en "
        "un paso | 3 / 1 / 1 / 0 |" in first
    )
    assert (
        "| PRE-008 | sin implementar (Entonces) | `None`: aserción de un Entonces; "
        "`True`: falla sin una aserción del driver | aceptado | - |" in first
    )
    assert (
        "| MUL-006 | - | `None`: pasa; `True`: pasa | aceptado sin comprobar (interfaz: "
        "`sin_alterar`) | - |" in first
    )
    assert "- *Solo el rojo: ningún impostor corrió.*" in second


def test_report_without_a_fresh_driver_measured_says_so(tmp_path: Path) -> None:
    report = _report(tmp_path, [])

    assert "## Medidas del driver\n- *Ningún driver nuevo medido.*" in report


def test_report_keeps_each_red_check_of_one_attempt_and_skips_what_doesn_t_follow(
    tmp_path: Path,
) -> None:
    """A red check run again after a crash leaves another step: both happened."""
    events = [
        _measured(1, ".lictor/runs/run-1/driver/001-red", _MEASURES),
        _measured(1, ".lictor/runs/run-1/driver/001-red-r1", _MEASURES),
        _event("lictor.driver.measured.v1", {"attempt": 3, "red": "roto"}),
    ]

    report = _report(tmp_path, events)

    assert report.count("### Intento 1") == 2
    assert "### Intento 3" not in report


def test_report_shows_how_much_code_is_repeated(tmp_path: Path) -> None:
    """ADR-0014: the measure of duplication, in one phrase beside its result."""
    controls: dict[str, object] = {
        "duplication": _executed(
            mode="measure",
            passed=False,
            output="r/d.log",
            duplication={"clones": 2, "in_clones": 250, "code_tokens": 1800},
        ),
    }

    report = _report(tmp_path, [_verify_event(controls)])

    section = report.split("## Puntuaciones")[1].split("##")[0]
    assert (
        "**duplication** (IVA-001, intento 1): fallo, 2 clones, 250 de 1800 tokens en clones"
        in (section)
    )


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, ["- *No se miró al empezar el run.*"]),
        ({"blind_spots": None}, ["- *No se miró al empezar el run.*"]),
        (
            {"blind_spots": {"skipped": None, "spots": [], "lines": []}},
            ["- *Nada: algún control ve cada test y cada fichero que usa el código.*"],
        ),
        (
            {"blind_spots": {"skipped": "no comprobado: no hay código", "lines": []}},
            ["- no comprobado: no hay código"],
        ),
        (
            {"blind_spots": {"skipped": None, "spots": [], "lines": ["Uno.", "Dos."]}},
            ["- Uno.", "- Dos."],
        ),
    ],
)
def test_report_says_what_the_controls_didnt_see_at_the_start(
    tmp_path: Path, data: dict[str, object], expected: list[str]
) -> None:
    report = _report(tmp_path, [_started(**data), _verify_event({})])

    section = report.split("## Lo que los controles no ven")[1].split("##")[0]
    assert [line for line in section.splitlines() if line] == expected
