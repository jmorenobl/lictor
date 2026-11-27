"""Where a run of the index stands, and what `run` and `retire` may do with it (G8 of 0.23.2).

A run started from a person's checkout lives in a worktree of its own (ADR-0005). Before 0.23.2
one boolean said whether it could go on, and a worktree it couldn't read counted as one that
could: `retire` refused a worktree half removed and asked for a `lictor run` that would have spent.
Here every case has a name, decided from facts read without the project's configuration, and what
can't be read is never resumed.

The facts come from the checkout, which is the only one that can tell a worktree git forgot (a
`git worktree remove` that failed halfway, which deletes git's own entry anyway) from one whose
checkout moved (which `git worktree repair` fixes): seen from the worktree both have a `.git` that
points nowhere.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from lictor.domain.run_branches import RUN_BRANCH_PREFIX

Link = Literal["linked", "unlinked", "orphan"]
"""How git knows the worktree: both ends point to each other, only git's entry is left (`git
worktree repair` fixes it), or git has no entry for it."""

Checkpoint = Literal["none", "open", "finished", "unreadable"]
"""What the run's checkpoint says: nothing yet, a run that goes on, one that finished, or bytes
that can't be read."""

Standing = Literal["retiring", "gone", "goes_on", "unlinked", "not_started", "finished", "broken"]

EntryKind = Literal["file", "dir", "link"]

_LISTED = 5
_LICTOR_DIR = ".lictor"
_GIT_POINTER = ".git"
_CHECKPOINT_STANDING: dict[Checkpoint, Standing] = {
    "none": "not_started",
    "open": "goes_on",
    "finished": "finished",
    "unreadable": "broken",
}


@dataclass(frozen=True)
class WorktreeFacts:
    """What is known of a run's worktree, none of it read through the project's configuration.

    `held` is whether another process holds the worktree's lock; `retire`, which holds it itself,
    passes False. `problem` says why the checkpoint can't be read, when it can't.
    """

    exists: bool
    retiring: bool = False
    held: bool = False
    link: Link = "linked"
    checkpoint: Checkpoint = "none"
    problem: str = ""


def run_standing(facts: WorktreeFacts) -> Standing:
    """Name where a run stands; the order is what decides when several facts apply."""
    if facts.retiring:
        return "retiring"
    if not facts.exists:
        return "gone"
    if facts.held:
        return "goes_on"
    if facts.link == "orphan":
        return "broken"
    if facts.link == "unlinked":
        return "unlinked"
    return _CHECKPOINT_STANDING[facts.checkpoint]


def counts_for_resume(facts: WorktreeFacts) -> bool:
    """Whether `lictor run` picks it among the runs it may resume.

    An unlinked one that hadn't finished counts so that choosing it refuses with what fixes it:
    otherwise `lictor run` without `--branch` would start another run, and spend.
    """
    standing = run_standing(facts)
    if standing == "unlinked":
        return facts.checkpoint != "finished"
    return standing in ("goes_on", "not_started")


def broken_reason(facts: WorktreeFacts) -> str:
    """Why a run can't go on, in one phrase."""
    if facts.link == "orphan":
        return "git ya no conoce su worktree"
    return f"su checkpoint no se puede leer ({facts.problem})"


def standing_note(
    standing: Standing, facts: WorktreeFacts, *, name: str, worktree: str, checkout: str
) -> str | None:
    """What `run`, `status` and `report` say of a run they can't go on with, or None."""
    branch = f"{RUN_BRANCH_PREFIX}{name}"
    if standing == "retiring":
        return (
            f"la ejecución de la rama {branch} se estaba retirando y el retiro quedó a medias: "
            f"termínalo con `lictor retire {name}`."
        )
    if standing == "unlinked":
        return unlinked_note(branch, worktree, checkout)
    if standing == "broken":
        return (
            f"la ejecución de la rama {branch} no se puede seguir: {broken_reason(facts)} "
            f"({worktree}). Retírala con `lictor retire {name}`."
        )
    return None


def unlinked_note(branch: str, worktree: str, checkout: str) -> str:
    """Git still has the worktree, but its `.git` no longer points back: one step repairs it."""
    return (
        f"el worktree de la rama {branch} ({worktree}) ya no está enlazado con el repositorio "
        "(¿se movió el checkout o se borró su .git?). No se ha tocado nada: arréglalo con "
        f"`git -C {checkout} worktree repair {worktree}` y vuelve a lanzar."
    )


def foreign_leftovers(
    entries: Sequence[tuple[str, EntryKind]], tracked: Collection[str] | None
) -> list[str]:
    """What is left in a worktree git forgot that may not be Lictor's to delete, topmost first.

    `entries` are every path under the worktree, relative to it. `.lictor/` is Lictor's, and a
    `.git` file is the pointer git left (a `.git` directory is someone's repository). With
    `tracked`, the paths of the run's branch, `retire` had marked the run after finding its tree
    clean, so what the branch has is what git was removing; without it nothing else is Lictor's.
    """
    allowed = _branch_paths(tracked) if tracked is not None else {}
    foreign: list[str] = []
    for path, kind in sorted(entries):
        if _lictors(path, kind) or any(path.startswith(f"{shown}/") for shown in foreign):
            continue
        if allowed.get(path) != (kind == "dir"):
            foreign.append(path)
    return foreign


def _lictors(path: str, kind: EntryKind) -> bool:
    """`.lictor/` and the `.git` file git leaves pointing to its entry."""
    own = path == _LICTOR_DIR or path.startswith(f"{_LICTOR_DIR}/")
    return own or (path == _GIT_POINTER and kind == "file")


def _branch_paths(tracked: Collection[str]) -> dict[str, bool]:
    """Each path of the branch and each directory holding one, telling directories apart."""
    found = {path: False for path in tracked}
    for path in tracked:
        # The last parent of a relative path is `.`, which names no directory of the branch.
        found |= {str(parent): True for parent in list(PurePosixPath(path).parents)[:-1]}
    return found


def leftovers_problem(worktree: str, foreign: Sequence[str], *, marked: bool) -> str:
    """Why `retire` leaves a worktree git forgot as it is: something in it may be the person's."""
    shown = ", ".join(foreign[:_LISTED])
    more = f" y {len(foreign) - _LISTED} más" if len(foreign) > _LISTED else ""
    why = (
        "No son de la rama: si son ignorados que git iba a borrar con el worktree, bórralos tú."
        if marked
        else "Lictor no dejó este retiro a medias, así que no sabe si son tuyos: revísalos y "
        "bórralos tú."
    )
    return (
        f"git ya no conoce el worktree {worktree}, y en él queda {shown}{more}. {why} No se ha "
        "tocado nada: después vuelve a lanzar `lictor retire`."
    )
