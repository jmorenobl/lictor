"""CommitGreen use case for recording green scenarios to version control."""

import uuid
from dataclasses import dataclass

from lictor.application.ports import Clock, EventLog, Vcs
from lictor.domain.errors import CandidateError
from lictor.domain.model import Candidate, Event, ScenarioId


@dataclass(frozen=True)
class CommitGreenRequest:
    """Request payload for CommitGreen use case."""

    scenario_id: str | ScenarioId
    run_id: str
    attempt: int
    candidate: Candidate | None = None


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
        sid_str = (
            request.scenario_id.value
            if isinstance(request.scenario_id, ScenarioId)
            else str(request.scenario_id)
        )

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

        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.scenario.green.v1",
            source="lictor/0.1.0",
            time=self.clock.now_iso(),
            runid=request.run_id,
            scenarioid=sid_str,
            data={
                "commit": candidate.sha,
                "tree": candidate.tree,
                "attempt": request.attempt,
                "attempts": request.attempt,
            },
        )
        self.event_log.append(event)

        return CommitGreenResult(commit_sha=candidate.sha)
