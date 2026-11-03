"""Quarantine use case for handling scenarios that failed or were conflicted."""

from dataclasses import dataclass
from typing import Literal

from lictor.application.events import new_event
from lictor.application.ports import Clock, EventLog
from lictor.domain.model import ScenarioId

QuarantineReason = Literal["intentos agotados", "spec_conflict", "driver_sospechoso"]


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
    """Use case to quarantine a scenario, whose attempts never reached the project."""

    def __init__(self, event_log: EventLog, clock: Clock) -> None:
        self.event_log = event_log
        self.clock = clock

    def execute(self, request: QuarantineRequest) -> QuarantineResult:
        """Append CloudEvent and return result."""
        sid_str = (
            request.scenario_id.value
            if isinstance(request.scenario_id, ScenarioId)
            else str(request.scenario_id)
        )

        event = new_event(
            self.clock,
            "lictor.scenario.quarantined.v1",
            request.run_id,
            scenario_id=sid_str,
            data={
                "reason": request.reason,
                "details": request.details or "",
                "last_error": request.details or "",
            },
        )
        self.event_log.append(event)

        return QuarantineResult(scenario_id=sid_str, reason=request.reason)
