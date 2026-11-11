"""Unit tests for what a run tells while it works, folded from its journal (F10, F11)."""

from typing import Any

import pytest

from lictor.domain.model import Event
from lictor.domain.progress import (
    RunProgress,
    StatusView,
    calls_text,
    cost_text,
    describe_status,
    duration_text,
    id_ranges,
    node_text,
    status_text,
)


def _event(kind: str, scenario: str | None = None, **data: Any) -> Event:
    return Event(
        id="x",
        type=f"lictor.{kind}.v1",
        source="lictor/0.2.0",
        time="2026-11-06T10:00:00Z",
        runid="R1",
        scenarioid=scenario,
        data=data,
    )


def _spec(*ids: str, green: tuple[str, ...] = ()) -> Event:
    scenarios = [
        {"id": sid, **({"green": {"commit": "c" * 40}} if sid in green else {})} for sid in ids
    ]
    return _event("spec.loaded", scenario_count=len(ids), scenarios=scenarios)


def _finished(owner: str | None, cost: float, ms: int, signal: str = "ok") -> Event:
    role = "implement" if owner else "driver"
    return _event(
        "agent.finished", owner, role=role, attempt=1, signal=signal, cost_usd=cost, duration_ms=ms
    )


def _lines(progress: RunProgress, *events: Event) -> list[str]:
    return [line for event in events for line in progress.observe(event)]


@pytest.mark.parametrize(
    ("usd", "text"), [(0.0, "0,00 $"), (0.12, "0,12 $"), (0.125, "0,12 $"), (3.456, "3,46 $")]
)
def test_cost_is_written_the_spanish_way(usd: float, text: str) -> None:
    assert cost_text(usd) == text


def test_cost_can_keep_more_decimals() -> None:
    assert cost_text(0.0434, decimals=4) == "0,0434 $"


@pytest.mark.parametrize(
    ("ms", "text"),
    [
        (0, "0 s"),
        (400, "0 s"),
        (59_400, "59 s"),
        (59_600, "1 min"),
        (125_000, "2 min 5 s"),
        (3_600_000, "1 h"),
        (3_660_000, "1 h 1 min"),
    ],
)
def test_duration_reads_as_a_person_says_it(ms: int, text: str) -> None:
    assert duration_text(ms) == text


@pytest.mark.parametrize(
    ("count", "text"), [(0, "0 llamadas"), (1, "1 llamada"), (2, "2 llamadas")]
)
def test_calls_agree_in_number(count: int, text: str) -> None:
    assert calls_text(count) == text


@pytest.mark.parametrize(
    ("status", "text"),
    [("green", "verde"), ("pending", "pendiente"), ("quarantined", "en cuarentena"), ("x", "x")],
)
def test_statuses_are_in_spanish(status: str, text: str) -> None:
    assert status_text(status) == text


def test_nodes_are_named_by_what_they_do() -> None:
    assert node_text("implement") == "implementar"
    assert node_text("build_driver") == "escribir el driver"
    assert node_text("desconocido") == "desconocido"


def test_the_spec_says_how_many_scenarios_and_how_many_are_green() -> None:
    progress = RunProgress()

    assert progress.observe(_spec("A-001", "A-002", "A-003", green=("A-002",))) == [
        "Escenarios: 3 (1 en verde, 2 pendientes)."
    ]
    assert progress.previous == ["A-002"]


def test_a_call_starting_says_who_and_which_attempt_of_how_many() -> None:
    progress = RunProgress()

    driver = _event("agent.started", role="driver", attempt=1, max_attempts=2)
    implement = _event("agent.started", "A-001", role="implement", attempt=2, max_attempts=3)

    assert _lines(progress, driver, implement) == [
        "driver: escribiendo el driver, intento 1 de 2…",
        "A-001: implementando, intento 2 de 3…",
    ]


def test_a_call_without_a_known_limit_gives_only_its_attempt() -> None:
    event = _event("agent.started", "A-001", role="implement", attempt=1, max_attempts=0)

    assert RunProgress().observe(event) == ["A-001: implementando, intento 1…"]


def test_a_sealed_driver_says_its_calls_and_cost() -> None:
    progress = RunProgress()

    lines = _lines(
        progress, _finished(None, 0.03, 1000), _finished(None, 0.04, 1000), _event("driver.sealed")
    )

    assert lines == ["driver: sellado (2 llamadas, 0,07 $)."]


def test_a_green_says_its_attempt_time_and_cost() -> None:
    progress = RunProgress()

    lines = _lines(
        progress,
        _finished("A-001", 0.05, 60_000),
        _event("verify.finished", "A-001", attempt=1, ok=True, control_durations={"lint": 5000}),
        _finished("A-001", 0.07, 55_000),
        _event("verify.finished", "A-001", attempt=2, ok=True, control_durations={"a": 5000}),
        _event("scenario.green", "A-001", attempt=2),
    )

    assert lines == ["A-001: verde, intento 2, 2 min 5 s, 0,12 $."]
    assert progress.implemented == ["A-001"]


def test_a_failed_verification_says_where() -> None:
    progress = RunProgress()

    lines = _lines(
        progress,
        _event("verify.finished", "A-001", attempt=1, ok=False, failed_at="L1-lint"),
        _event("verify.finished", "A-001", attempt=2, ok=False, failed_at=None),
    )

    assert lines == ["A-001: el intento 1 falló en L1-lint.", "A-001: el intento 2 falló."]


def test_collateral_greens_go_on_one_line_once_they_end() -> None:
    progress = RunProgress()

    held = _lines(
        progress,
        _event("scenario.green", "A-002", collateral=True),
        _event("scenario.green", "A-003", collateral=True),
    )
    released = progress.observe(_event("agent.started", "A-004", attempt=1, max_attempts=3))

    assert held == []
    assert released == [
        "A-002, A-003: verdes colaterales, sin llamar al agente.",
        "A-004: implementando, intento 1 de 3…",
    ]
    assert progress.collateral == ["A-002", "A-003"]


def test_a_single_collateral_green_is_singular_and_flush_releases_it() -> None:
    progress = RunProgress()
    progress.observe(_event("scenario.green", "A-002", collateral=True))

    assert progress.flush() == ["A-002: verde colateral, sin llamar al agente."]
    assert progress.flush() == []


def test_a_failed_call_that_keeps_the_attempt_says_so() -> None:
    progress = RunProgress()

    assert progress.observe(_finished("A-001", 0.0, 10, signal="error")) == [
        "A-001: la llamada al agente falló; se repite sin gastar el intento."
    ]
    assert progress.observe(_finished(None, 0.0, 10, signal="rate_limited")) == []


def test_the_driver_steps_that_need_no_agent_are_told() -> None:
    progress = RunProgress()

    lines = _lines(
        progress,
        _event("driver.rejected", attempt=1, reason="sin_prueba"),
        _event("driver.checked"),
        _event("scenario.reopened", "A-001"),
        _event("scenario.reregistered", "A-001"),
    )

    assert lines == [
        "driver: el intento 1 se rechazó (sin_prueba).",
        "driver: el driver sellado sigue siendo válido.",
        "A-001: reabierto, su especificación cambió desde su verde.",
        "A-001: sigue pasando con la especificación nueva; registrado sin agente.",
    ]
    assert progress.reregistered == ["A-001"]


def test_a_quarantine_says_why_and_what_it_cost() -> None:
    progress = RunProgress()

    lines = _lines(
        progress,
        _finished("A-001", 0.1, 10),
        _event("scenario.quarantined", "A-001", reason="intentos agotados"),
    )

    assert lines == ["A-001: en cuarentena (intentos agotados), 1 llamada, 0,10 $."]
    assert progress.quarantined == ["A-001"]


def test_the_end_gives_the_totals() -> None:
    progress = RunProgress()

    lines = _lines(
        progress,
        _spec("A-001", "A-002", "A-003", "A-004", green=("A-001",)),
        _finished(None, 0.05, 10),
        _finished("A-002", 0.1, 10),
        _event("scenario.green", "A-002", attempt=1),
        _event("scenario.green", "A-003", collateral=True),
        _event("run.finished", exit_code=0),
    )

    assert lines[-2:] == [
        "A-003: verde colateral, sin llamar al agente.",
        "Total: 2 llamadas, 0,15 $; 3 en verde, 0 en cuarentena, 1 pendiente.",
    ]
    assert (progress.calls, round(progress.cost_usd, 2)) == (2, 0.15)


def test_a_run_that_never_loaded_its_spec_gives_no_totals() -> None:
    assert RunProgress().observe(_event("run.finished", exit_code=40)) == []


def test_the_start_and_a_resume_are_told() -> None:
    progress = RunProgress()

    lines = _lines(
        progress, _event("run.started"), _event("run.resumed", resume_node="build_driver")
    )

    assert lines == [
        "Entorno de verificación listo.",
        "Reanudando la ejecución: escribir el driver.",
    ]


def test_events_it_does_not_know_say_nothing() -> None:
    assert RunProgress().observe(_event("agent.denied", "A-001")) == []


def test_spend_is_kept_per_owner() -> None:
    progress = RunProgress()
    _lines(progress, _finished(None, 0.05, 10), _finished("A-001", 0.1, 20))

    assert progress.spend("driver").calls == 1
    assert progress.spend("A-001").agent_ms == 20
    assert progress.spend("nadie").calls == 0


def _status(
    next_nodes: list[str],
    values: dict[str, Any],
    progress: RunProgress | None = None,
    by_hand: frozenset[str] = frozenset(),
) -> list[str]:
    return describe_status(
        StatusView(
            run_id="R1",
            next_nodes=next_nodes,
            values=values,
            progress=progress or RunProgress(),
            by_hand=by_hand,
            max_attempts=3,
            max_driver_attempts=2,
        )
    )


def _folded(*events: Event) -> RunProgress:
    progress = RunProgress()
    _lines(progress, *events)
    return progress


def test_status_of_a_run_in_progress_names_the_scenario_and_its_attempt() -> None:
    progress = _folded(
        _spec("A-001", "A-002", "A-003", "A-004"),
        _finished(None, 0.07, 10),
        _finished("A-001", 0.05, 10),
        _event("scenario.green", "A-001", attempt=1),
        _event("scenario.green", "A-002", collateral=True),
        _finished("A-003", 0.01, 10),
    )
    values = {
        "current": "A-003",
        "attempt": 1,
        "status": {"A-001": "green", "A-002": "green", "A-003": "pending", "A-004": "pending"},
    }

    lines = _status(["implement"], values, progress)

    assert lines == [
        "Ejecución: R1",
        "Estado: sin terminar; siguiente paso: implementar.",
        "Escenario en curso: A-003, intento 2 de 3.",
        "Verdes: 2 de 4 (1 implementado, 1 colateral). En cuarentena: 0. Pendientes: 2.",
        "Agente: 3 llamadas, 0,13 $ (driver: 1 llamada, 0,07 $).",
        "Escenarios:",
        "  - A-001: verde (implementado)",
        "  - A-002: verde (colateral)",
        "  - A-003: pendiente (en curso)",
        "  - A-004: pendiente",
    ]


def test_status_groups_the_pending_scenarios_on_one_line() -> None:
    """F38: 27 pending scenarios were listed one by one under a summary that already counts them."""
    ids = [f"DNI-{n:03d}" for n in range(1, 28)]
    status = {sid: "pending" for sid in ids} | {"DNI-001": "green", "DNI-011": "quarantined"}
    progress = _folded(_spec(*ids), _event("scenario.green", "DNI-001", attempt=1))

    lines = _status(["implement"], {"current": "DNI-002", "attempt": 0, "status": status}, progress)

    assert lines[lines.index("Escenarios:") + 1 :] == [
        "  - DNI-001: verde (implementado)",
        "  - DNI-002: pendiente (en curso)",
        "  - DNI-011: en cuarentena",
        "  - DNI-003 a DNI-010, DNI-012 a DNI-027: pendientes",
    ]


def test_status_groups_the_greens_from_before_as_it_groups_the_pending() -> None:
    """F42: after F38, the 27 greens of an earlier run were still listed one by one."""
    ids = [f"DNI-{n:03d}" for n in range(1, 30)]
    status = dict.fromkeys(ids, "green")
    progress = _folded(
        _spec(*ids, green=tuple(ids[:27])),
        _event("scenario.green", "DNI-028", attempt=1),
        _event("scenario.green", "DNI-029", collateral=True),
    )

    lines = _status([], {"status": status}, progress, by_hand=frozenset({"DNI-005"}))

    assert lines[lines.index("Escenarios:") + 1 :] == [
        "  - DNI-005: verde (registrado a mano)",
        "  - DNI-028: verde (implementado)",
        "  - DNI-029: verde (colateral)",
        "  - DNI-001 a DNI-004, DNI-006 a DNI-027: verdes (de antes)",
    ]


@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        (["A-001"], "A-001"),
        (["A-001", "A-002"], "A-001, A-002"),
        (["A-001", "A-002", "A-003"], "A-001 a A-003"),
        (["A-001", "A-003", "A-004", "A-005", "B-006"], "A-001, A-003 a A-005, B-006"),
        (["A-009", "A-010", "A-011"], "A-009 a A-011"),
        (["A-1", "A-2", "A-03"], "A-1, A-2, A-03"),
        (["X", "Y", "Z"], "X, Y, Z"),
    ],
)
def test_consecutive_ids_are_written_as_a_range(ids: list[str], expected: str) -> None:
    assert id_ranges(ids) == expected


def test_status_counts_the_attempt_being_verified_as_it_is() -> None:
    lines = _status(["verify"], {"current": "A-001", "attempt": 2, "status": {"A-001": "pending"}})

    assert "Escenario en curso: A-001, intento 2 de 3." in lines


def test_status_while_the_driver_is_written_gives_its_attempt() -> None:
    lines = _status(["build_driver"], {"driver_attempts": 1, "status": {"A-001": "pending"}})

    assert "Driver: intento 2 de 2." in lines
    assert not any(line.startswith("Escenario en curso") for line in lines)


def test_status_of_a_red_check_gives_the_driver_attempt_being_checked() -> None:
    lines = _status(["red_check"], {"driver_attempts": 1, "status": {}})

    assert "Driver: intento 1 de 2." in lines


def test_status_of_a_paused_run_names_where_it_resumes() -> None:
    values = {"current": "A-001", "attempt": 0, "resume_to": "implement", "status": {}}

    lines = _status(["pause"], values)

    assert "Estado: sin terminar; siguiente paso: pausa." in lines
    assert "Escenario en curso: A-001, intento 1 de 3." in lines


def test_status_of_a_finished_run_gives_its_code_and_reason_in_spanish() -> None:
    values = {
        "exit_code": 30,
        "exit_reason": "A-002 en cuarentena",
        "current": "A-002",
        "status": {"A-001": "green", "A-002": "quarantined"},
    }
    progress = _folded(_spec("A-001", "A-002", green=("A-001",)))

    lines = _status([], values, progress, by_hand=frozenset({"A-001"}))

    assert lines[1:3] == ["Estado: terminada con código 30.", "Motivo: A-002 en cuarentena"]
    assert "Verdes: 1 de 2 (1 de antes). En cuarentena: 1. Pendientes: 0." in lines
    assert "Agente: 0 llamadas, 0,00 $." in lines
    assert "  - A-001: verde (registrado a mano)" in lines
    assert "  - A-002: en cuarentena" in lines
    assert not any(line.startswith("Escenario en curso") for line in lines)
    assert not any("None" in line for line in lines)


def test_status_of_a_run_with_nothing_yet() -> None:
    lines = _status([], {})

    assert lines == [
        "Ejecución: R1",
        "Estado: sin empezar.",
        "Verdes: 0 de 0. En cuarentena: 0. Pendientes: 0.",
        "Agente: 0 llamadas, 0,00 $.",
    ]


def test_status_tells_greens_registered_again() -> None:
    progress = _folded(_spec("A-001", green=("A-001",)), _event("scenario.reregistered", "A-001"))

    lines = _status([], {"exit_code": 0, "status": {"A-001": "green"}}, progress)

    assert "Verdes: 1 de 1 (1 registrado de nuevo). En cuarentena: 0. Pendientes: 0." in lines
    assert "  - A-001: verde (registrado de nuevo)" in lines


def test_a_call_that_loaded_a_skill_of_the_project_says_so() -> None:
    progress = RunProgress()
    event = _event(
        "agent.finished",
        "A-001",
        role="implement",
        attempt=1,
        signal="ok",
        cost_usd=0.0,
        duration_ms=10,
        project_skills=["lictor"],
    )

    [line] = progress.observe(event)

    assert line.startswith("⚠ A-001:")
    assert "lictor" in line
    assert progress.observe(_finished("A-001", 0.0, 10)) == []
