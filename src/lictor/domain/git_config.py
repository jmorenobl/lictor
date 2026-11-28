"""Which change of `.git/config` is Lictor's to guard, and which is inert (S22 of 0.25.0).

`.git/config` is shared by the checkout and every worktree, and much that isn't Lictor writes it:
an editor adds its own keys under the branch it just created (`vscode-merge-base`), and the person
sets up a branch of their own (`git switch -c`, `git push -u`) while a run goes on in its worktree.
None of that changes what git does for Lictor, which only works on the run's own branch.

A key is inert when it is in the section of a branch that isn't the one watched, or in that
branch's section but not one git reads (`remote`, `merge` and the rest of `GIT_BRANCH_KEYS`:
`mergeOptions` reaches `merge --ff-only`). Everything else (`core.*`, `remote.*`, `include.*`,
filters, hooks) is guarded as before. The entries are what `git config --list -z` gives: section
and name in lower case, the subsection as written.
"""

from collections.abc import Iterable, Sequence
from typing import Final

GIT_BRANCH_KEYS: Final = frozenset(
    {"remote", "pushremote", "merge", "mergeoptions", "rebase", "description"}
)

type ConfigEntries = tuple[tuple[str, str], ...]


def config_entries(listed: str) -> ConfigEntries:
    """The entries of `git config --list -z`: `key\\nvalue` records ended by NUL; a key alone
    (`[section] key` without `=`) has no value."""
    entries: list[tuple[str, str]] = []
    for record in listed.split("\0"):
        if record:
            key, _, value = record.partition("\n")
            entries.append((key, value))
    return tuple(entries)


def inert_key(key: str, branch: str) -> bool:
    """Whether a change of `key` leaves what git does for the run on `branch` as it was."""
    section, _, rest = key.partition(".")
    subsection, dot, name = rest.rpartition(".")
    if section != "branch" or not dot:
        return False
    return subsection != branch or name not in GIT_BRANCH_KEYS


def watched(entries: Iterable[tuple[str, str]], branch: str) -> ConfigEntries:
    """The entries Lictor guards, in their order: what an anchor hashes and a restore compares."""
    return tuple((key, value) for key, value in entries if not inert_key(key, branch))


def changed_keys(before: ConfigEntries, after: ConfigEntries, branch: str) -> tuple[str, ...]:
    """The guarded keys whose values differ, sorted: what a tampering of the config names."""
    keys = {key for key, _ in (*before, *after) if not inert_key(key, branch)}
    return tuple(sorted(key for key in keys if _values(before, key) != _values(after, key)))


def _values(entries: ConfigEntries, key: str) -> list[str]:
    return [value for name, value in entries if name == key]


# How many changed keys a tampering names; the rest are counted.
NAMED_KEYS: Final = 5


def config_change_text(keys: Sequence[str]) -> str:
    """How a changed config is named among what was restored: its first keys, and the count."""
    if not keys:
        return ".git/config"
    named = ", ".join(keys[:NAMED_KEYS])
    rest = len(keys) - NAMED_KEYS
    return f".git/config ({named} y {rest} más)" if rest > 0 else f".git/config ({named})"
