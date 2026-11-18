"""CommitRefactor: the branch takes a verified refactor, and the journal says what came of it.

It moves the branch like `CommitGreen`, with one `merge --ff-only`, but marks no scenario green
for the refactor itself (ADR-0010): only the collaterals its tree also turned green.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from lictor.application.events import new_event
from lictor.application.ports import Clock, EventLog, Vcs
from lictor.domain.errors import CandidateError
from lictor.domain.model import Candidate, Event
from lictor.domain.refactor import REFACTORED, RefactorRecord


@dataclass(frozen=True)
class CommitRefactorRequest:
    """A verified refactor of one scenario, and the collaterals chained on top of it."""

    scenario_id: str
    run_id: str
    attempt: int
    candidate: Candidate | None
    collateral: Sequence[str] = ()


@dataclass(frozen=True)
class CommitRefactorResult:
    """The refactor's own commit, which the collaterals' empty commit may sit on."""

    commit_sha: str


def refactored_event(clock: Clock, run_id: str, record: RefactorRecord) -> Event:
    """Record what came of the refactor of a scenario: applied, discarded or unchanged."""
    return new_event(clock, REFACTORED, run_id, scenario_id=record.scenario_id, data=record.data())


class CommitRefactor:
    """Use case to move the branch to a verified refactor."""

    def __init__(self, vcs: Vcs, event_log: EventLog, clock: Clock) -> None:
        self.vcs = vcs
        self.event_log = event_log
        self.clock = clock

    def execute(self, request: CommitRefactorRequest) -> CommitRefactorResult:
        """Advance the branch to the verified commit and record the refactor and its collaterals."""
        candidate = request.candidate
        if candidate is None:
            raise CandidateError(
                f"No hay refactor verificado para {request.scenario_id}: "
                "lanza una ejecución nueva con --new."
            )
        if self.vcs.get_tree(candidate.sha) != candidate.tree:
            raise CandidateError(f"El commit {candidate.sha[:12]} no es el árbol verificado.")

        # After a crash the branch may already be there.
        if self.vcs.get_head_sha() != candidate.sha:
            self.vcs.advance_head(candidate.sha, expected=candidate.parent)

        own = self.vcs.parent_of(candidate.sha) if request.collateral else candidate.sha
        self.event_log.append(
            refactored_event(
                self.clock,
                request.run_id,
                RefactorRecord(
                    request.scenario_id,
                    "applied",
                    request.attempt,
                    commit=own,
                    collateral=tuple(request.collateral),
                ),
            )
        )
        for sid in request.collateral:
            self.event_log.append(
                new_event(
                    self.clock,
                    "lictor.scenario.green.v1",
                    request.run_id,
                    scenario_id=sid,
                    data={
                        "commit": candidate.sha,
                        "tree": candidate.tree,
                        "attempt": 0,
                        "collateral": True,
                        "after_refactor": request.scenario_id,
                    },
                )
            )
        return CommitRefactorResult(commit_sha=own)
