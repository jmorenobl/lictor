"""Unit tests for the envelope every journal event shares."""

from lictor.application.events import SOURCE, new_event


class _Clock:
    def now_iso(self) -> str:
        return "2026-11-03T12:00:00Z"


def test_new_event_stamps_the_envelope() -> None:
    event = new_event(_Clock(), "lictor.x.v1", "R1", data={"a": 1}, scenario_id="IVA-001")

    assert (event.type, event.source, event.time, event.runid, event.scenarioid, event.data) == (
        "lictor.x.v1",
        "lictor/0.1.0",
        "2026-11-03T12:00:00Z",
        "R1",
        "IVA-001",
        {"a": 1},
    )
    assert SOURCE == "lictor/0.1.0"


def test_new_event_has_a_fresh_id_and_no_scenario_by_default() -> None:
    first = new_event(_Clock(), "lictor.x.v1", None, data={})
    second = new_event(_Clock(), "lictor.x.v1", None, data={})

    assert first.scenarioid is None
    assert first.runid is None
    assert len(first.id) == 32
    assert first.id != second.id
