"""EventLog adapter writing and reading CloudEvents 1.0 from journal.jsonl."""

import json
from pathlib import Path

from lictor.domain.model import Event


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
        if not self._path.exists():
            return []

        result: list[Event] = []
        with open(self._path, encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = Event.from_dict(json.loads(stripped))
                except (json.JSONDecodeError, KeyError, AttributeError):
                    continue
                if run_id is None or event.runid == run_id:
                    result.append(event)
        return result
