"""The envelope every journal event shares."""

import uuid

from lictor import __version__
from lictor.application.ports import Clock
from lictor.domain.model import Event

# The installed version, never a literal: one written by hand went on saying 0.1.0 in 0.1.1 (F20).
SOURCE = f"lictor/{__version__}"


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
