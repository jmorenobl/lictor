"""What a duplication report leaves repeated, and how the refactor hears of it (ADR-0014).

The report is every clone with its places, and how many tokens of each file are in one. That
count, every copy counted, is what a refactor may not raise: it doesn't move with formatting or
with renaming inside a function, and removing code that isn't repeated never raises it.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Final

from lictor.domain.code_measures import ALONE, RefactorBlock
from lictor.domain.reports import Clone, CloneOccurrence, DuplicationReport

# How many clones a message names before it only counts the rest.
NAMED: Final[int] = 20


@dataclass(frozen=True)
class Duplication:
    """What a duplication report leaves repeated: the tokens in clones, and the clones."""

    min_tokens: int
    in_clones: int
    clones: tuple[Clone, ...] = ()
    # How many clones there were: a measure kept in the checkpoint names only the first `NAMED`.
    count: int = 0


def duplication_of(report: DuplicationReport) -> Duplication:
    """The repeated code of a report, its clones largest first."""
    clones = tuple(sorted(report.clones, key=lambda c: (-c.tokens, _first(c))))
    return Duplication(report.min_tokens, report.in_clones, clones, len(clones))


def _first(clone: Clone) -> tuple[str, int]:
    first = clone.occurrences[0]
    return first.file, first.start


def kept(duplication: Duplication) -> Duplication:
    """The same measure with only the clones a message names, as the checkpoint keeps it."""
    return Duplication(
        duplication.min_tokens,
        duplication.in_clones,
        duplication.clones[:NAMED],
        duplication.count,
    )


def _place(occurrence: CloneOccurrence) -> str:
    return f"{occurrence.file}:{occurrence.first_line}-{occurrence.last_line}"


def clone_line(clone: Clone) -> str:
    """One clone as a message names it: where it is, and how many tokens it has."""
    *before, last = [_place(o) for o in clone.occurrences]
    return f"{', '.join(before)} y {last}: {clone.tokens} tokens"


def first_where_changed(clones: Sequence[Clone], changed: Collection[str]) -> list[Clone]:
    """The clones with a place in a file the scenario changed first, each group as it came."""
    touched = [c for c in clones if any(o.file in changed for o in c.occurrences)]
    return touched + [c for c in clones if c not in touched]


def named_clones(clones: Sequence[Clone], total: int) -> list[str]:
    """The lines of the first `NAMED` of `total` clones, and how many more there are.

    A measure kept in the checkpoint has only the first ones: the total says how many it had.
    """
    lines = [f"- {clone_line(c)}" for c in clones[:NAMED]]
    rest = total - min(len(clones), NAMED)
    if rest:
        lines.append(f"- y {rest} más")
    return lines


def clones_reason(duplication: Duplication) -> str:
    """Why a duplication control fails: which code is repeated."""
    head = (
        "Hay un clon de código repetido:"
        if duplication.count == 1
        else f"Hay {duplication.count} clones de código repetido:"
    )
    return "\n".join([head, *named_clones(duplication.clones, duplication.count)])


def _definition(min_tokens: int) -> str:
    return (
        f"un clon es un trozo de {min_tokens} tokens o más que se repite en dos sitios, con los "
        "nombres y los literales cambiados de forma coherente dentro de cada función"
    )


def refactor_duplication_text(
    duplication: Duplication | None, command: str | None, changed: Collection[str] = ()
) -> str:
    """The lines of the refactor's prompt on duplication: what the green left, and how to measure.

    With no measure of the green there is nothing to name, and saying the list is empty would be
    false. With one, an empty list is said: kept silent, the agent estimates by eye (F65).
    """
    if duplication is None:
        return ""
    ordered = first_where_changed(duplication.clones, changed)
    return RefactorBlock(
        "Código repetido, medido en la verificación del verde "
        f"({_definition(duplication.min_tokens)}):",
        named_clones(ordered, duplication.count),
        "Quita al menos uno, empezando por los de los ficheros que cambió este escenario; no hace "
        "falta quitarlos todos ahora.",
        "- Ningún clon.",
        f"La verificación descarta el refactor si deja más tokens en clones que el verde "
        f"({duplication.in_clones}). El formato no cuenta, ni cambiar el nombre de una variable "
        "dentro de una función.",
        f"Mídelo tú con `{command}`, {ALONE} Su segunda línea dice los tokens en clones."
        if command
        else None,
    ).text()


def implementer_duplication_text(command: str | None) -> str:
    """The line of the implementer's prompt when a duplication control blocks.

    Only a control that blocks reaches the implementer: one that measures is the refactor's. The
    size of a clone is the command's, which says it on its first line.
    """
    how = (
        f" Mídelo con `{command}`, que dice en su primera línea qué cuenta como clon."
        if command
        else ""
    )
    return (
        "- La verificación exige que no haya código repetido: ningún trozo que se repita en dos "
        f"sitios, aunque cambien los nombres.{how}"
    )


def describe_duplication(count: int, in_clones: int, code_tokens: int) -> str:
    """What a duplication measure says in one phrase, as `report.md` and `check` show it."""
    if count == 0:
        return "ningún clon"
    clones = "1 clon" if count == 1 else f"{count} clones"
    return f"{clones}, {in_clones} de {code_tokens} tokens en clones"
