"""CommitRefactor: the branch takes a verified refactor; no green is marked for it (ADR-0010)."""

from unittest.mock import MagicMock

import pytest

from lictor.application.ports import Clock, EventLog, Vcs
from lictor.application.usecases.commit_refactor import CommitRefactor, CommitRefactorRequest
from lictor.domain.errors import CandidateError
from lictor.domain.model import Candidate, Event

CANDIDATE = Candidate(sha="r0", tree="t1", parent="verde")


def _use_case(head: str = "verde", tree: str = "t1") -> tuple[CommitRefactor, MagicMock, MagicMock]:
    vcs = MagicMock(spec=Vcs)
    vcs.get_head_sha.return_value = head
    vcs.get_tree.return_value = tree
    vcs.parent_of.return_value = "propio"
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-11-18T10:00:00Z"
    return CommitRefactor(vcs, event_log, clock), vcs, event_log


def _events(event_log: MagicMock) -> list[Event]:
    return [call.args[0] for call in event_log.append.call_args_list]


def _request(
    candidate: Candidate | None = CANDIDATE, collateral: tuple[str, ...] = ()
) -> CommitRefactorRequest:
    return CommitRefactorRequest(
        scenario_id="IVA-001", run_id="run", attempt=2, candidate=candidate, collateral=collateral
    )


def test_it_moves_the_branch_and_records_the_refactor_only() -> None:
    use_case, vcs, event_log = _use_case()

    result = use_case.execute(_request())

    vcs.advance_head.assert_called_once_with("r0", expected="verde")
    assert result.commit_sha == "r0"
    (event,) = _events(event_log)
    assert (event.type, event.scenarioid) == ("lictor.scenario.refactored.v1", "IVA-001")
    assert event.data == {
        "outcome": "applied",
        "attempt": 2,
        "commit": "r0",
        "failed_at": None,
        "collateral": [],
    }


def test_the_collaterals_of_a_refactor_are_green_and_say_where_they_came_from() -> None:
    use_case, _, event_log = _use_case()

    result = use_case.execute(_request(collateral=("IVA-002",)))

    refactored, green = _events(event_log)
    assert result.commit_sha == "propio"  # the refactor's own commit, under the empty one
    assert refactored.data["commit"] == "propio"
    assert refactored.data["collateral"] == ["IVA-002"]
    assert green.type == "lictor.scenario.green.v1"
    assert green.scenarioid == "IVA-002"
    assert green.data["commit"] == "r0"
    assert green.data["collateral"] is True
    assert green.data["after_refactor"] == "IVA-001"


def test_after_a_crash_the_branch_is_already_there() -> None:
    use_case, vcs, event_log = _use_case(head="r0")

    use_case.execute(_request())

    vcs.advance_head.assert_not_called()
    assert len(_events(event_log)) == 1


def test_without_a_candidate_or_with_another_tree_nothing_moves() -> None:
    use_case, vcs, _ = _use_case()
    with pytest.raises(CandidateError):
        use_case.execute(_request(candidate=None))
    use_case, vcs, _ = _use_case(tree="otro")
    with pytest.raises(CandidateError):
        use_case.execute(_request())
    vcs.advance_head.assert_not_called()
