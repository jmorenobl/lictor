from unittest.mock import MagicMock

import pytest

from lictor.application.ports import Clock, EventLog, Vcs
from lictor.application.usecases.commit_green import CommitGreen, CommitGreenRequest
from lictor.domain.errors import CandidateError
from lictor.domain.model import Candidate, Event

CANDIDATE = Candidate(sha="c0ffee", tree="t1", parent="p0")


def _use_case(head: str = "p0", tree: str = "t1") -> tuple[CommitGreen, MagicMock, MagicMock]:
    vcs = MagicMock(spec=Vcs)
    vcs.get_head_sha.return_value = head
    vcs.get_tree.return_value = tree
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:00:00Z"
    return CommitGreen(vcs=vcs, event_log=event_log, clock=clock), vcs, event_log


def _request() -> CommitGreenRequest:
    return CommitGreenRequest(
        scenario_id="IVA-001", run_id="20261030-140000-abcd", attempt=2, candidate=CANDIDATE
    )


def test_commit_green_advances_to_the_candidate_and_emits_event() -> None:
    use_case, vcs, event_log = _use_case()

    result = use_case.execute(_request())

    assert result.commit_sha == "c0ffee"
    vcs.advance_head.assert_called_once_with("c0ffee", expected="p0")
    event = event_log.append.call_args[0][0]
    assert isinstance(event, Event)
    assert event.type == "lictor.scenario.green.v1"
    assert event.scenarioid == "IVA-001"
    assert event.data["commit"] == "c0ffee"
    assert event.data["tree"] == "t1"
    assert event.data["attempt"] == 2
    assert event.data["attempts"] == 2


def test_commit_green_is_idempotent_after_a_crash() -> None:
    use_case, vcs, event_log = _use_case(head="c0ffee")

    result = use_case.execute(_request())

    assert result.commit_sha == "c0ffee"
    vcs.advance_head.assert_not_called()
    event_log.append.assert_called_once()


def test_commit_green_needs_a_candidate() -> None:
    use_case, vcs, _ = _use_case()
    request = CommitGreenRequest(scenario_id="IVA-001", run_id="r", attempt=1, candidate=None)

    with pytest.raises(CandidateError):
        use_case.execute(request)

    vcs.advance_head.assert_not_called()


def test_commit_green_refuses_a_tree_other_than_the_verified_one() -> None:
    use_case, vcs, _ = _use_case(tree="otro")

    with pytest.raises(CandidateError):
        use_case.execute(_request())

    vcs.advance_head.assert_not_called()


def test_commit_green_expects_the_parent_it_verified_not_the_current_head() -> None:
    use_case, vcs, _ = _use_case(head="otro")

    use_case.execute(_request())

    vcs.advance_head.assert_called_once_with("c0ffee", expected="p0")
