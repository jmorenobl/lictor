"""Unit tests for Clock and JsonlEventLog store adapters."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lictor.adapters.store.clock import FixedClock, SystemClock
from lictor.adapters.store.jsonl_eventlog import JsonlEventLog
from lictor.application.ports import Clock, EventLog
from lictor.domain.model import Event, JournalDamage


class TestClocks:
    def test_system_clock_protocol(self) -> None:
        clock = SystemClock()
        assert isinstance(clock, Clock)

        now = clock.now()
        assert now.tzinfo is not None
        assert clock.now_iso().endswith("Z")

        run_id = clock.generate_run_id()
        # Format: YYYYMMDD-HHMMSS-<4 hex chars>
        assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{4}$", run_id)

    def test_fixed_clock(self) -> None:
        fixed_dt = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
        clock = FixedClock(fixed_dt=fixed_dt, fixed_run_id="20261030-120000-abcd")
        assert isinstance(clock, Clock)

        assert clock.now() == fixed_dt
        assert clock.now_iso() == "2026-10-30T12:00:00Z"
        assert clock.generate_run_id() == "20261030-120000-abcd"

    def test_fixed_clock_default(self) -> None:
        clock = FixedClock()
        assert clock.now_iso() == "2026-10-30T00:00:00Z"
        assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{4}$", clock.generate_run_id())


class TestJsonlEventLog:
    def test_eventlog_protocol(self, tmp_path: Path) -> None:
        log_file = tmp_path / ".lictor" / "journal.jsonl"
        log = JsonlEventLog(log_file)
        assert isinstance(log, EventLog)

    def test_append_and_read_events(self, tmp_path: Path) -> None:
        log_file = tmp_path / ".lictor" / "journal.jsonl"
        log = JsonlEventLog(log_file)

        # Before any events
        assert log.events() == []

        e1 = Event(
            id="evt-1",
            type="lictor.run.started.v1",
            source="lictor/0.1.0",
            time="2026-10-30T12:00:00Z",
            runid="20261030-120000-a1b2",
            data={"version": "0.1.0"},
        )
        e2 = Event(
            id="evt-2",
            type="lictor.agent.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T12:01:00Z",
            runid="20261030-120000-a1b2",
            scenarioid="IVA-001",
            data={"status": "ok", "role": "implement"},
        )
        e3 = Event(
            id="evt-3",
            type="lictor.run.started.v1",
            source="lictor/0.1.0",
            time="2026-10-30T13:00:00Z",
            runid="20261030-130000-c3d4",
            data={"version": "0.1.0"},
        )

        log.append(e1)
        log.append(e2)
        log.append(e3)

        assert log_file.exists()
        all_events = log.events()
        assert len(all_events) == 3
        assert all_events[0] == e1
        assert all_events[1] == e2
        assert all_events[2] == e3

        # Filtering by run_id
        run1_events = log.events(run_id="20261030-120000-a1b2")
        assert len(run1_events) == 2
        assert run1_events == [e1, e2]

        run2_events = log.events(run_id="20261030-130000-c3d4")
        assert len(run2_events) == 1
        assert run2_events == [e3]

        empty_events = log.events(run_id="nonexistent")
        assert empty_events == []


class TestJournalTolerance:
    def _log_with(self, tmp_path: Path, text: str) -> JsonlEventLog:
        path = tmp_path / "journal.jsonl"
        good = JsonlEventLog(path)
        good.append(Event(id="1", type="t", source="lictor/0.1.0", time="now", data={}))
        with open(path, "a", encoding="utf-8") as journal:
            journal.write(text)
        good.append(Event(id="2", type="t", source="lictor/0.1.0", time="now", data={}))
        return good

    def test_invalid_lines_do_not_lose_the_rest(self, tmp_path: Path) -> None:
        log = self._log_with(tmp_path, 'no es json\n{"forjado":true}\n[1, 2]\n')
        assert [event.id for event in log.events()] == ["1", "2"]

    def test_undecodable_bytes_do_not_lose_the_rest(self, tmp_path: Path) -> None:
        log = self._log_with(tmp_path, "")
        with open(tmp_path / "journal.jsonl", "ab") as journal:
            journal.write(b"\xff\xfe\n")
        assert [event.id for event in log.events()] == ["1", "2"]


def _event_line(source: str = "lictor/0.1.0") -> str:
    event = Event(id="1", type="t", source=source, time="2026-10-30T10:00:00Z", runid="r", data={})
    return json.dumps(event.to_dict())


def _damage(tmp_path: Path, *lines: str) -> JournalDamage:
    path = tmp_path / "journal.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return JsonlEventLog(path).damage()


def test_damage_of_a_missing_journal_is_none(tmp_path: Path) -> None:
    assert JsonlEventLog(tmp_path / "no-existe.jsonl").damage() == JournalDamage()


def test_damage_of_a_healthy_journal_is_none(tmp_path: Path) -> None:
    assert _damage(tmp_path, _event_line(), "", _event_line()).total == 0


def test_a_line_cut_short_is_corrupt(tmp_path: Path) -> None:
    damage = _damage(tmp_path, _event_line(), _event_line()[:20])

    assert damage == JournalDamage(corrupt=1, foreign=0)


@pytest.mark.parametrize(
    "line",
    ['{"forjado":true}', "[1, 2]", "7", '"texto"', _event_line(source="otro/1.0")],
    ids=["objeto", "lista", "numero", "cadena", "otra-fuente"],
)
def test_json_that_is_no_event_of_lictor_is_foreign(tmp_path: Path, line: str) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(f"{_event_line()}\n{line}\n", encoding="utf-8")
    log = JsonlEventLog(path)

    assert log.damage() == JournalDamage(corrupt=0, foreign=1)
    assert len(log.events()) == 1
