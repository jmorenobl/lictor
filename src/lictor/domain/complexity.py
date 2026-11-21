"""What a complexity report leaves above its limit, and how the refactor hears of it (ADR-0013).

The report is every function with its value; the limit is the control's. What is above the limit
is two numbers that removing or splitting code never raises unfairly: `excess`, what the functions
above the limit exceed it by, summed, and `peak`, what the worst of them exceeds it by. A refactor
may raise neither; one that lowers them without reaching the limit improves the code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from lictor.domain.reports import ComplexityReport, FunctionComplexity

# How many functions a message names before it only counts the rest.
NAMED: Final[int] = 20


@dataclass(frozen=True)
class OverLimit:
    """The functions above a limit, most complex first, and what they exceed it by."""

    limit: int
    functions: tuple[FunctionComplexity, ...] = ()

    @property
    def excess(self) -> int:
        return sum(f.complexity - self.limit for f in self.functions)

    @property
    def peak(self) -> int:
        return max((f.complexity - self.limit for f in self.functions), default=0)


def over_limit(report: ComplexityReport, limit: int) -> OverLimit:
    """The functions of a report above `limit`, most complex first, then by file and line."""
    above = sorted(
        (f for f in report.functions if f.complexity > limit),
        key=lambda f: (-f.complexity, f.file, f.line),
    )
    return OverLimit(limit, tuple(above))


def function_line(function: FunctionComplexity) -> str:
    """One function as a message names it."""
    return f"{function.file}:{function.line} `{function.function}`: {function.complexity}"


def named_functions(functions: Sequence[FunctionComplexity]) -> list[str]:
    """The lines of a list of functions, the first `NAMED` by name and the rest counted."""
    lines = [f"- {function_line(f)}" for f in functions[:NAMED]]
    if len(functions) > NAMED:
        lines.append(f"- y {len(functions) - NAMED} más")
    return lines


def over_limit_reason(over: OverLimit) -> str:
    """Why a complexity control fails: which functions go above its limit."""
    count = len(over.functions)
    head = (
        f"Una función supera la complejidad de {over.limit}:"
        if count == 1
        else f"{count} funciones superan la complejidad de {over.limit}:"
    )
    return "\n".join([head, *named_functions(over.functions)])


def refactor_complexity_text(over: OverLimit | None, command: str | None) -> str:
    """The lines of the refactor's prompt on complexity: what the green left, and how to measure.

    With no measure of the green there is nothing to name, and saying the list is empty would
    be false. With one, an empty list is said: kept silent, the agent estimates by eye (F60). The
    rule is said with it: the refactor feared the discard more than anything (F60).
    """
    if over is None:
        return ""
    lines = [
        f"Complejidad, con un límite de {over.limit} por función, medida en la verificación del "
        "verde:"
    ]
    if over.functions:
        lines.extend(named_functions(over.functions))
        lines.append(
            "Baja al menos una, empezando por las de los ficheros que cambió este escenario; no "
            "hace falta arreglarlas todas ahora."
        )
    else:
        lines.append("- Ninguna función supera el límite.")
    lines.append(
        "La verificación descarta el refactor si deja por encima del límite más que el verde, en "
        "total o en la peor función. Por debajo del límite la complejidad no cuenta, y mejorar una "
        "función sin llegar al límite no se descarta."
    )
    if command:
        lines.append(f"Mídela tú, antes y después de cambiar nada, con `{command}`.")
    return "\n".join(lines)


def implementer_complexity_text(limit: int, command: str | None) -> str:
    """The line of the implementer's prompt when a complexity control blocks: its limit.

    Only a control that blocks reaches the implementer: one that measures is the refactor's.
    """
    how = f" Mídela con `{command}`." if command else ""
    return (
        f"- La verificación exige que ninguna función supere una complejidad ciclomática de "
        f"{limit}.{how}"
    )


def describe_over(count: int, limit: int, peak: int) -> str:
    """What a complexity measure says in one phrase, as `report.md` and `check` show it."""
    if count == 0:
        return f"ninguna función supera {limit}"
    worst = f"la peor, {limit + peak}"
    if count == 1:
        return f"1 función supera {limit} ({worst})"
    return f"{count} funciones superan {limit} ({worst})"
