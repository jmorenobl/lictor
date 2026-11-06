"""Unit tests for the envelope every journal event shares."""

import lictor
from lictor.application.events import SOURCE, ObservedEventLog, new_event
from lictor.domain.model import Event, JournalDamage


class _Clock:
    def now_iso(self) -> str:
        return "2026-11-03T12:00:00Z"


def test_new_event_stamps_the_envelope() -> None:
    event = new_event(_Clock(), "lictor.x.v1", "R1", data={"a": 1}, scenario_id="IVA-001")

    assert (event.type, event.source, event.time, event.runid, event.scenarioid, event.data) == (
        "lictor.x.v1",
        SOURCE,
        "2026-11-03T12:00:00Z",
        "R1",
        "IVA-001",
        {"a": 1},
    )


def test_the_source_is_the_installed_version() -> None:
    assert f"lictor/{lictor.__version__}" == SOURCE


def test_new_event_has_a_fresh_id_and_no_scenario_by_default() -> None:
    first = new_event(_Clock(), "lictor.x.v1", None, data={})
    second = new_event(_Clock(), "lictor.x.v1", None, data={})

    assert first.scenarioid is None
    assert first.runid is None
    assert len(first.id) == 32
    assert first.id != second.id


class _Log:
    def __init__(self) -> None:
        self.appended: list[Event] = []

    def append(self, event: Event) -> None:
        self.appended.append(event)

    def events(self, run_id: str | None = None) -> list[Event]:
        return [e for e in self.appended if run_id is None or e.runid == run_id]

    def damage(self) -> JournalDamage:
        return JournalDamage(corrupt=1)


def test_an_observed_log_tells_each_event_once_it_is_written() -> None:
    inner = _Log()
    seen: list[tuple[str, int]] = []
    log = ObservedEventLog(inner, lambda event: seen.append((event.type, len(inner.appended))))

    log.append(new_event(_Clock(), "lictor.x.v1", "R1", data={}))
    log.append(new_event(_Clock(), "lictor.y.v1", "R2", data={}))

    assert seen == [("lictor.x.v1", 1), ("lictor.y.v1", 2)]
    assert [e.type for e in log.events("R2")] == ["lictor.y.v1"]
    assert log.damage() == JournalDamage(corrupt=1)
