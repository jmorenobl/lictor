"""CommitGreen use case for recording green scenarios to version control."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from lictor.application.ports import Clock, EventLog, Vcs
from lictor.domain.model import Event, ScenarioId


@dataclass(frozen=True)
class CommitGreenRequest:
    """Request payload for CommitGreen use case."""

    scenario_id: str | ScenarioId
    run_id: str
    attempt: int
    paths_writable: Sequence[str] | None = None


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
        """Create a git commit with metadata trailers and append CloudEvent."""
        sid_str = (
            request.scenario_id.value
            if isinstance(request.scenario_id, ScenarioId)
            else str(request.scenario_id)
        )

        trailers = {
            "Scenario": sid_str,
            "Lictor-Run": request.run_id,
            "Lictor-Attempts": str(request.attempt),
        }

        # Check idempotency: if HEAD already has matching Scenario and Lictor-Run
        head_scenarios = self.vcs.get_trailers("Scenario", commit_ref="HEAD")
        head_runs = self.vcs.get_trailers("Lictor-Run", commit_ref="HEAD")
        if (
            head_scenarios
            and head_scenarios[0] == sid_str
            and head_runs
            and head_runs[0] == request.run_id
        ):
            commit_sha = self.vcs.get_head_sha()
        else:
            commit_sha = self.vcs.commit(
                message=f"feat({sid_str}): implementar escenario",
                trailers=trailers,
                paths=request.paths_writable,
            )

        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.scenario.green.v1",
            source="lictor/0.1.0",
            time=self.clock.now_iso(),
            runid=request.run_id,
            scenarioid=sid_str,
            data={
                "commit": commit_sha,
                "attempt": request.attempt,
                "attempts": request.attempt,
            },
        )
        self.event_log.append(event)

        return CommitGreenResult(commit_sha=commit_sha)
