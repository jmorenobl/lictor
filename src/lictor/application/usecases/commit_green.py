"""CommitGreen use case for recording green scenarios to version control."""

from collections.abc import Sequence
from dataclasses import dataclass

from lictor.application.events import new_event
from lictor.application.ports import Clock, EventLog, Vcs
from lictor.domain.errors import CandidateError
from lictor.domain.model import Candidate, ScenarioId


@dataclass(frozen=True)
class CommitGreenRequest:
    """Request payload for CommitGreen use case."""

    scenario_id: str | ScenarioId
    run_id: str
    attempt: int
    candidate: Candidate | None = None
    collateral: Sequence[str] = ()


@dataclass(frozen=True)
class CommitGreenResult:
    """Result of CommitGreen use case."""

    commit_sha: str


class CommitGreen:
    """Use case to commit changes for a successfully verified scenario."""

    def __init__(self, vcs: Vcs, event_log: EventLog, clock: Clock) -> None:
        self.vcs = vcs
        self.event_log = event_log
        self.clock = clock

    def execute(self, request: CommitGreenRequest) -> CommitGreenResult:
        """Advance the branch to the verified commit and append CloudEvent."""
        sid_str = str(request.scenario_id)

        candidate = request.candidate
        if candidate is None:
            raise CandidateError(
                f"No hay commit verificado para {sid_str}: lanza una ejecución nueva con --new."
            )
        if self.vcs.get_tree(candidate.sha) != candidate.tree:
            raise CandidateError(f"El commit {candidate.sha[:12]} no es el árbol verificado.")

        # After a crash the branch may already be there. Nothing is added here: what the working
        # tree holds now was not verified.
        if self.vcs.get_head_sha() != candidate.sha:
            self.vcs.advance_head(candidate.sha, expected=candidate.parent)

        attempts: dict[str, object] = {"attempt": request.attempt, "attempts": request.attempt}
        self._green(request, candidate, sid_str, attempts)
        for collateral in request.collateral:
            self._green(request, candidate, collateral, {"attempt": 0, "collateral": True})

        return CommitGreenResult(commit_sha=candidate.sha)

    def _green(
        self, request: CommitGreenRequest, candidate: Candidate, sid: str, data: dict[str, object]
    ) -> None:
        self.event_log.append(
            new_event(
                self.clock,
                "lictor.scenario.green.v1",
                request.run_id,
                scenario_id=sid,
                data={"commit": candidate.sha, "tree": candidate.tree, **data},
            )
        )
