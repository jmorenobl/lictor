from unittest.mock import MagicMock

from lictor.application.ports import Clock, EventLog, Vcs
from lictor.application.usecases.quarantine import Quarantine, QuarantineRequest
from lictor.domain.model import Event


def test_quarantine_resets_head_and_emits_event() -> None:
    vcs = MagicMock(spec=Vcs)
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:10:00Z"

    use_case = Quarantine(vcs=vcs, event_log=event_log, clock=clock)
    req = QuarantineRequest(
        scenario_id="IVA-002",
        run_id="20261030-140000-abcd",
        reason="intentos agotados",
        details="3 failed verification attempts",
    )

    result = use_case.execute(req)

    assert result.scenario_id == "IVA-002"
    assert result.reason == "intentos agotados"
    vcs.reset_to_head.assert_called_once()
    event_log.append.assert_called_once()
    event = event_log.append.call_args[0][0]
    assert isinstance(event, Event)
    assert event.type == "lictor.scenario.quarantined.v1"
    assert event.scenarioid == "IVA-002"
    assert event.data["reason"] == "intentos agotados"
    assert event.data["details"] == "3 failed verification attempts"
    assert event.data["last_error"] == "3 failed verification attempts"
