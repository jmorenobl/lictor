"""The rule files of a commit, as `doctor` and `lictor conventions` read them (F79).

Each one is read from the commit, never from the working tree: what a run's agents get is what
the person reviewed against what is committed. A link is followed inside the repository, and a
content that two paths read counts once.
"""

import hashlib
from collections.abc import Mapping, Sequence

from lictor.application.ports import Vcs
from lictor.domain.conventions import (
    FoundRuleFile,
    RuleFile,
    TreeEntry,
    by_content,
    conventions_warning,
    is_rule_file,
    link_target,
)

# A chain of links longer than this is a loop, or as good as one.
MAX_LINK_HOPS = 8


def rule_files_at(vcs: Vcs, rev: str, names: Sequence[str]) -> tuple[RuleFile, ...]:
    """Every rule file of `rev` with the hash of its content, a link's being its target's."""
    if not names:
        return ()
    entries = {entry.path: entry for entry in vcs.tree_entries(rev)}
    found = []
    for path in sorted(entries):
        if not is_rule_file(path, names):
            continue
        resolved = _resolve(vcs, rev, entries, path)
        if resolved is not None:
            digest = hashlib.sha256(vcs.read_bytes(rev, resolved)).hexdigest()
            found.append(FoundRuleFile(path, resolved, digest))
    return by_content(found)


def _resolve(vcs: Vcs, rev: str, entries: Mapping[str, TreeEntry], path: str) -> str | None:
    """The file a path reads, following links inside the repository, or None if it reads none."""
    current: str | None = path
    for _ in range(MAX_LINK_HOPS):
        entry = entries.get(current) if current is not None else None
        if entry is None:
            return None
        if entry.is_file:
            return entry.path
        if not entry.is_link:
            return None
        target = vcs.read_bytes(rev, entry.path).decode("utf-8", "surrogateescape")
        current = link_target(entry.path, target)
    return None


def rule_files_warnings(
    vcs: Vcs, names: Sequence[str], reviewed: Mapping[str, str]
) -> tuple[str, ...] | None:
    """What `doctor` says of HEAD's rule files: a warning, nothing if all are reviewed, or None
    when there is no rule file to speak of."""
    files = rule_files_at(vcs, "HEAD", names)
    if not files:
        return None
    warning = conventions_warning(files, reviewed)
    return (warning,) if warning else ()
