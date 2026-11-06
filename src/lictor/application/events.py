"""The envelope every journal event shares."""

import uuid
from collections.abc import Callable

from lictor import __version__
from lictor.application.ports import Clock, EventLog
from lictor.domain.model import Event, JournalDamage

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


class ObservedEventLog:
    """A journal that also tells someone each event once it is written, such as the terminal.

    The event is in the journal before anyone hears of it, so what is told never gets ahead of
    what was recorded.
    """

    def __init__(self, inner: EventLog, observe: Callable[[Event], None]) -> None:
        self.inner = inner
        self.observe = observe

    def append(self, event: Event) -> None:
        self.inner.append(event)
        self.observe(event)

    def events(self, run_id: str | None = None) -> list[Event]:
        return self.inner.events(run_id=run_id)

    def damage(self) -> JournalDamage:
        return self.inner.damage()
