"""The envelope every journal event shares."""

import uuid

from lictor.application.ports import Clock
from lictor.domain.model import Event

SOURCE = "lictor/0.1.0"


def new_event(
    clock: Clock,
    kind: str,
    run_id: str | None,
    *,
    data: dict[str, object],
    scenario_id: str | None = None,
) -> Event:
    """Build an event of this run, stamped now."""
    return Event(
        id=uuid.uuid4().hex,
        type=kind,
        source=SOURCE,
        time=clock.now_iso(),
        runid=run_id,
        scenarioid=scenario_id,
        data=data,
    )
