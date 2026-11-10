"""Which `.gitignore` entries a file still lacks, counting lines that already say the same (F33).

Only exact equivalences count: a line without a trailing `/` also ignores the directory, and a
bracket of plain characters stands for each of them (`*.py[oc]` is `*.pyo` and `*.pyc`). Anything
else, a range or a negated class included, is compared as written: adding a line that was already
covered is harmless, skipping one that wasn't would leave files out of `.gitignore`.
"""

import re
from collections.abc import Iterable, Sequence

_PLAIN_BRACKET = re.compile(r"\[([^\]\[!^\-]+)\]")


def _spellings(line: str) -> set[str]:
    """Every literal pattern a line stands for, or none for a comment, a negation or a blank."""
    pattern = line.strip()
    if not pattern or pattern.startswith(("#", "!")):
        return set()
    found = _PLAIN_BRACKET.search(pattern)
    if found is None:
        return {pattern}
    before, after = pattern[: found.start()], pattern[found.end() :]
    return {spelling for char in found.group(1) for spelling in _spellings(before + char + after)}


def _covered(entry: str, existing: set[str]) -> bool:
    return entry in existing or (entry.endswith("/") and entry.removesuffix("/") in existing)


def missing_entries(existing_lines: Iterable[str], entries: Sequence[str]) -> list[str]:
    """Return, in order and once each, the entries no existing line already covers."""
    existing = {spelling for line in existing_lines for spelling in _spellings(line)}
    missing: list[str] = []
    for raw in entries:
        entry = raw.strip()
        if not entry or _covered(entry, existing):
            continue
        missing.append(entry)
        existing.add(entry)
    return missing
