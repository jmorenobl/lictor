"""Quarantine use case for handling scenarios that failed or were conflicted."""

import uuid
from dataclasses import dataclass
from typing import Literal

from lictor.application.ports import Clock, EventLog, Vcs
from lictor.domain.model import Event, ScenarioId

QuarantineReason = Literal["intentos agotados", "spec_conflict"]


@dataclass(frozen=True)
class QuarantineRequest:
    """Request payload for Quarantine use case."""

    scenario_id: str | ScenarioId
    run_id: str
    reason: QuarantineReason
    details: str | None = None


@dataclass(frozen=True)
class QuarantineResult:
    """Result payload for Quarantine use case."""

    scenario_id: str
    reason: QuarantineReason


class Quarantine:
    """Use case to safely quarantine a scenario and reset working tree."""

    def __init__(self, vcs: Vcs, event_log: EventLog, clock: Clock) -> None:
        self.vcs = vcs
        self.event_log = event_log
        self.clock = clock

    def execute(self, request: QuarantineRequest) -> QuarantineResult:
        """Reset working tree to HEAD, append CloudEvent, and return result."""
        self.vcs.reset_to_head()

        sid_str = (
            request.scenario_id.value
            if isinstance(request.scenario_id, ScenarioId)
            else str(request.scenario_id)
        )

        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.scenario.quarantined.v1",
            source="lictor/0.1.0",
            time=self.clock.now_iso(),
            runid=request.run_id,
            scenarioid=sid_str,
            data={
                "reason": request.reason,
                "details": request.details or "",
            },
        )
        self.event_log.append(event)

        return QuarantineResult(scenario_id=sid_str, reason=request.reason)
