"""Unit tests for Clock and JsonlEventLog store adapters."""

import re
from datetime import UTC, datetime
from pathlib import Path

from lictor.adapters.store.clock import FixedClock, SystemClock
from lictor.adapters.store.jsonl_eventlog import JsonlEventLog
from lictor.application.ports import Clock, EventLog
from lictor.domain.model import Event


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
