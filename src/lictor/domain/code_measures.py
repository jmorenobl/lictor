"""How the refactor's prompt tells what a control of the code left (ADR-0013, ADR-0014).

Each control that measures the code itself (the functions above a limit, the clones) says the
same four things: what the green left, or that it left nothing; what to do with it; what the
verification discards; and how to measure it alone, before editing and when done.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

# Chained with `&&` after the acceptance, which fails while scenarios are pending, the measure
# never ran (F60, the pass of the 0.13.0).
ALONE: Final[str] = (
    "sola y no detrás de otras órdenes con `&&`: una vez antes de editar y otra al terminar."
)


@dataclass(frozen=True)
class RefactorBlock:
    """The block of one control in the refactor's prompt."""

    head: str  # what was measured, and where
    named: Sequence[str]  # what the green left: one line each
    ask: str  # what to do with it, when there is something
    empty: str  # what to say when there is nothing
    rule: str  # what the verification discards
    measure: str | None  # how the agent measures it, if it can

    def text(self) -> str:
        lines = [self.head, *self.named, self.ask] if self.named else [self.head, self.empty]
        lines.append(self.rule)
        if self.measure:
            lines.append(self.measure)
        return "\n".join(lines)
