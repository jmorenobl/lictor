"""EventLog adapter writing and reading CloudEvents 1.0 from journal.jsonl."""

import json
from pathlib import Path

from lictor.domain.model import Event, JournalDamage

_OWN_SOURCE = "lictor/"


class JsonlEventLog:
    """Appends and reads CloudEvents from a line-delimited JSON journal."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def append(self, event: Event) -> None:
        """Append an event as a single line in journal.jsonl."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), separators=(",", ":"))
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def events(self, run_id: str | None = None) -> list[Event]:
        """Read all events from journal.jsonl, optionally filtering by run_id.

        A line that isn't an event is skipped: it must not cost the record of everything else.
        """
        events, _ = self._read()
        return [event for event in events if run_id is None or event.runid == run_id]

    def damage(self) -> JournalDamage:
        """Count what `events` skips, so that skipping it never goes unnoticed."""
        return self._read()[1]

    def _read(self) -> tuple[list[Event], JournalDamage]:
        if not self._path.exists():
            return [], JournalDamage()

        result: list[Event] = []
        corrupt = foreign = 0
        with open(self._path, encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    corrupt += 1
                    continue
                try:
                    event = Event.from_dict(parsed)
                except (KeyError, AttributeError, TypeError):
                    foreign += 1
                    continue
                if not event.source.startswith(_OWN_SOURCE):
                    foreign += 1
                    continue
                result.append(event)
        return result, JournalDamage(corrupt=corrupt, foreign=foreign)
