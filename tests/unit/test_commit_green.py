from unittest.mock import MagicMock

from lictor.application.ports import Clock, EventLog, Vcs
from lictor.application.usecases.commit_green import CommitGreen, CommitGreenRequest
from lictor.domain.model import Event


def test_commit_green_emits_event_and_calls_vcs() -> None:
    vcs = MagicMock(spec=Vcs)
    vcs.commit.return_value = "c0ffee123456"
    vcs.get_trailers.return_value = []

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:00:00Z"

    use_case = CommitGreen(vcs=vcs, event_log=event_log, clock=clock)
    req = CommitGreenRequest(
        scenario_id="IVA-001",
        run_id="20261030-140000-abcd",
        attempt=2,
    )

    result = use_case.execute(req)

    assert result.commit_sha == "c0ffee123456"
    vcs.commit.assert_called_once()
    event_log.append.assert_called_once()
    call_args = event_log.append.call_args[0][0]
    assert isinstance(call_args, Event)
    assert call_args.type == "lictor.scenario.green.v1"
    assert call_args.scenarioid == "IVA-001"
    assert call_args.data["commit"] == "c0ffee123456"
    assert call_args.data["attempt"] == 2


def test_commit_green_idempotent_when_already_committed() -> None:
    vcs = MagicMock(spec=Vcs)
    vcs.get_trailers.side_effect = lambda key: (
        ["IVA-001"] if key == "Scenario" else ["20261030-140000-abcd"]
    )
    vcs.commit.return_value = "head_commit_sha"

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:00:00Z"

    use_case = CommitGreen(vcs=vcs, event_log=event_log, clock=clock)
    req = CommitGreenRequest(
        scenario_id="IVA-001",
        run_id="20261030-140000-abcd",
        attempt=1,
    )

    result = use_case.execute(req)
    assert result.commit_sha is not None
