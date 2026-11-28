"""The stubs a fresh driver declares with their constants, without a plugin (ADR-0021).

Lictor knows no language there, so it can't write `return false` with the right type: the driver
does. Each stub is a line of its own that fails with Lictor's mark (`lictor: no implementado`) and
ends with a comment that gives the constants that replace it, whole:

    panic("lictor: no implementado") // lictor-impostor: return 1 | return 2

Two distinct values of the function's type, none its zero (but a boolean's) and never null, or a
single one when it returns nothing (`return`). In the variant `k` the whole line becomes its
indentation and the constant `k` (the only one, for a function without a result). Lictor checks
the text alone: what the values are is the order's to ask, and a wrong one fails closed.

These are pure functions over text; reading the files is the application's.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

STUB_MARK: Final = "lictor: no implementado"
IMPOSTOR_MARK: Final = "lictor-impostor:"
SEPARATOR: Final = " | "
VARIANTS: Final = 2  # how many runs of the acceptance the constants give


@dataclass(frozen=True)
class DeclaredStub:
    """One stub line: where it is, how it is indented, and the constants that replace it."""

    line: int  # counted from 1
    indent: str
    constants: tuple[str, ...]  # one (no result) or two

    def constant(self, variant: int) -> str:
        """The constant of the variant `variant` (from 0): the only one, if there is one."""
        return self.constants[min(variant, len(self.constants) - 1)]


@dataclass(frozen=True)
class StubFormProblem:
    """A line that looks like a stub and isn't one Lictor can replace, and why."""

    line: int
    reason: str


def parse_stub_line(number: int, text: str) -> DeclaredStub | StubFormProblem | None:
    """Read one line: a stub, a stub written wrong, or neither (None)."""
    if STUB_MARK not in text and IMPOSTOR_MARK not in text:
        return None
    reason = _marks_problem(text)
    if reason is None:
        constants = tuple(
            part.strip() for part in text.partition(IMPOSTOR_MARK)[2].split(SEPARATOR)
        )
        reason = _constants_problem(constants)
        if reason is None:
            return DeclaredStub(number, text[: len(text) - len(text.lstrip())], constants)
    return StubFormProblem(number, reason)


def _marks_problem(text: str) -> str | None:
    """Why the two marks of a stub line aren't where they go, or None."""
    if IMPOSTOR_MARK not in text:
        return f"lleva «{STUB_MARK}» sin «{IMPOSTOR_MARK}» y sus constantes"
    before = text.partition(IMPOSTOR_MARK)[0]
    if STUB_MARK not in before:
        return f"lleva «{IMPOSTOR_MARK}» sin «{STUB_MARK}» antes, en la misma línea"
    return None


def _constants_problem(constants: tuple[str, ...]) -> str | None:
    """Why the constants of a stub line can't replace it, or None."""
    if any(not c for c in constants):
        return "tiene una constante vacía"
    if len(constants) > VARIANTS:
        return (
            f"tiene {len(constants)} constantes: son dos separadas por «{SEPARATOR.strip()}» "
            "entre espacios, o una si la función no devuelve nada"
        )
    if len(constants) == VARIANTS and constants[0] == constants[1]:
        return "tiene dos constantes iguales: tienen que ser distintas"
    return None


def declared_stubs(text: str) -> tuple[list[DeclaredStub], list[StubFormProblem]]:
    """Every stub of a file, and every line that looks like one and isn't."""
    stubs: list[DeclaredStub] = []
    problems: list[StubFormProblem] = []
    for number, line in enumerate(text.splitlines(), start=1):
        found = parse_stub_line(number, line)
        if isinstance(found, DeclaredStub):
            stubs.append(found)
        elif found is not None:
            problems.append(found)
    return stubs, problems


def declared_variant(text: str, variant: int) -> tuple[str, int]:
    """The file with each stub replaced by its constant `variant`, and how many were replaced.

    Each line keeps its own end, whatever it is; what comes before the mark is the indentation.
    """
    stubs, _ = declared_stubs(text)
    lines = text.splitlines(keepends=True)
    for stub in stubs:
        line = lines[stub.line - 1]
        end = line[len(line.splitlines()[0]) :]
        lines[stub.line - 1] = stub.indent + stub.constant(variant) + end
    return "".join(lines), len(stubs)


def single_variant(stubs: Sequence[DeclaredStub]) -> bool:
    """Whether every stub has one constant: then both variants are the same tree, run once."""
    return all(len(stub.constants) == 1 for stub in stubs)


def form_problem_text(path: str, problem: StubFormProblem) -> str:
    """What the driver's agent reads of one line written wrong."""
    return f"La línea {path}:{problem.line} {problem.reason}."


def declared_stubs_rule(declared: bool) -> str:
    """The rule of the driver's order on the stubs' constants, only for a profile that asks for
    them: without it the line is empty and the order keeps its own."""
    if not declared:
        return ""
    return (
        f"- Cada stub falla en una línea sola, dentro de su función, con el texto `{STUB_MARK}`, "
        f"y esa línea acaba con un comentario `{IMPOSTOR_MARK} <A>{SEPARATOR}<B>`: dos "
        "sentencias que la sustituyen devolviendo dos valores distintos de su tipo, ninguno su "
        'valor cero salvo en un booleano (1 y 2, "a" y "b", una lista de uno y otra de dos, '
        "una estructura con cada campo lleno), nunca nulos, y que solo usan lo que ya usa la "
        "firma. Si la función no devuelve nada, una sola (`return`). Lictor pone cada constante "
        "en lugar del stub para comprobar que cada test mira el resultado: un test que pasa con "
        "todas se rechaza.\n"
    )
