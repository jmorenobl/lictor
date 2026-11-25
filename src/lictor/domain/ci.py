"""What the project's own CI checks, as commands with where they are written (ADR-0015, F76).

Lictor reads the project's workflows and its pre-commit configuration from HEAD and hands each
command to the profile's survey, which recognises the tools with the same rules it uses for the
controls. A step is one command: a script of several lines gives one step per command, each with
its own line. What a pre-commit hook passes as files is told apart from what it names, because
that decides what it reaches.
"""

from dataclasses import dataclass
from typing import Literal

CiOrigin = Literal["workflow", "pre-commit"]


@dataclass(frozen=True)
class CiStep:
    """One command the CI runs, and where it is written (`file`, relative to the root, and line).

    `pass_filenames` says whether it gets the files that changed (pre-commit's default), and then
    `files` and `exclude` are the patterns that choose them; a workflow's step gets none.
    """

    file: str
    line: int
    run: str
    origin: CiOrigin
    pass_filenames: bool = False
    files: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def place(self) -> str:
        """Where it is written, as `file:line`."""
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class CiSteps:
    """Every step of the CI, and the files that couldn't be read, each with why."""

    steps: tuple[CiStep, ...] = ()
    unreadable: tuple[str, ...] = ()


def ci_entry_matches(entry: str, file: str, run: str) -> bool:
    """Whether an entry of `blind_spots.accepted` («file: command») names this step.

    The command is compared by its words, so spacing doesn't count; a path alone never matches a
    step, so a step added later to an accepted file still warns.
    """
    name, sep, command = entry.partition(": ")
    if not sep:
        return False
    return name.strip() == file and command.split() == run.split()


def is_ci_entry(entry: str) -> bool:
    """Whether an entry of `blind_spots.accepted` names a step of the CI, not a path."""
    return ": " in entry
