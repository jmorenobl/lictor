from unittest.mock import MagicMock

from lictor.application.ports import Clock, EventLog
from lictor.application.usecases.quarantine import Quarantine, QuarantineRequest
from lictor.domain.model import Event, ScenarioId


def test_quarantine_emits_event() -> None:
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:10:00Z"

    use_case = Quarantine(event_log=event_log, clock=clock)
    req = QuarantineRequest(
        scenario_id="IVA-002",
        run_id="20261030-140000-abcd",
        reason="intentos agotados",
        details="3 failed verification attempts",
    )

    result = use_case.execute(req)

    assert result.scenario_id == "IVA-002"
    assert result.reason == "intentos agotados"
    event_log.append.assert_called_once()
    event = event_log.append.call_args[0][0]
    assert isinstance(event, Event)
    assert event.type == "lictor.scenario.quarantined.v1"
    assert event.scenarioid == "IVA-002"
    assert event.data["reason"] == "intentos agotados"
    assert event.data["details"] == "3 failed verification attempts"
    assert event.data["last_error"] == "3 failed verification attempts"


def test_quarantine_event_without_details_and_from_a_scenario_id() -> None:
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:10:00Z"
    req = QuarantineRequest(
        scenario_id=ScenarioId("IVA-002"), run_id="R7", reason="spec_conflict", details=None
    )

    result = Quarantine(event_log=event_log, clock=clock).execute(req)

    assert result.scenario_id == "IVA-002"
    event = event_log.append.call_args[0][0]
    assert (event.runid, event.scenarioid) == ("R7", "IVA-002")
    assert (event.data["details"], event.data["last_error"]) == ("", "")
