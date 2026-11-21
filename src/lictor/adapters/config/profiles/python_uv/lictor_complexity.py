"""The cyclomatic complexity of each function, with the one definition Lictor uses (ADR-0013).

Lictor copies this file into the verification environment, where it runs as the `complexity`
control, and next to each refactor call, where the agent measures with it. It imports nothing of
Lictor, reads the code without importing or running it, reads no configuration and honours no
comment: what it counts is fixed here, and its tests fix each construct.

A function's complexity is 1 plus one for each:

- `if` and `elif`, and each conditional expression;
- `for` and `while` (`async for` too), and each `for` and `if` of a comprehension;
- `except` (`except*` too);
- `case` of a `match`, but the last one if it takes any value with no guard (`case _`);
- operand of an `and` or an `or` after its first (`a and b` adds 1, `a and b and c` adds 2).

The `else` of a loop or a `try`, `finally`, `with` and `assert` add nothing. A nested function or a
method counts apart; a lambda, in the function that holds it. Decorators and default values are
not the function's own; code at module level or in a class body is not measured.

    python -I -B -m lictor_complexity src [--out report.json]

It prints every function, most complex first; with `--out` it also writes them as JSON, which is
the report the control reads. A file that can't be parsed fails the command and leaves no report.
"""

import ast
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

DEFINITION = (
    "Complejidad ciclomática: 1, más 1 por cada if, elif, ternario, for, while, except, case "
    "(salvo un `case _` final), for o if de una comprensión, y operando de un and/or tras el "
    "primero. Una función anidada o un método cuentan aparte."
)
# A nested function or class is measured apart; a lambda counts in the function that holds it.
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
# Absent in the oldest interpreters a project may run: then nothing in its code can be one.
_MATCH = getattr(ast, "Match", None)
_MATCH_AS = getattr(ast, "MatchAs", None)


def _takes_anything(case: ast.AST) -> bool:
    """Whether a `case` takes any value with no guard: `case _`, `case otro`, `case (_ as x)`."""
    if _MATCH_AS is None or getattr(case, "guard", None) is not None:
        return False
    pattern = getattr(case, "pattern", None)
    while isinstance(pattern, _MATCH_AS) and getattr(pattern, "pattern", None) is not None:
        pattern = getattr(pattern, "pattern", None)
    return isinstance(pattern, _MATCH_AS)


def _decisions(node: ast.AST) -> int:
    if isinstance(node, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While)):
        return 1
    if isinstance(node, ast.ExceptHandler):
        return 1
    if isinstance(node, ast.BoolOp):
        return len(node.values) - 1
    if isinstance(node, ast.comprehension):
        return 1 + len(node.ifs)
    if _MATCH is not None and isinstance(node, _MATCH):
        cases = getattr(node, "cases", [])
        return len(cases) - (1 if cases and _takes_anything(cases[-1]) else 0)
    return 0


def _own_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """The nodes of a function's body that are its own: neither a nested function nor a class."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _NESTED):
            continue
        yield child
        yield from _own_nodes(child)


def complexity(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """The complexity of one function, by the definition above."""
    total = 1
    for statement in function.body:
        if isinstance(statement, _NESTED):
            continue
        total += _decisions(statement)
        total += sum(_decisions(node) for node in _own_nodes(statement))
    return total


def functions(tree: ast.AST, file: str) -> Iterator[dict[str, object]]:
    """Every function of a module, nested ones and methods included, with its complexity."""

    def walk(node: ast.AST, prefix: str) -> Iterator[dict[str, object]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _FUNCTIONS):
                name = f"{prefix}{child.name}"
                yield {
                    "file": file,
                    "function": name,
                    "line": child.lineno,
                    "complexity": complexity(child),
                }
                yield from walk(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                yield from walk(child, f"{prefix}{child.name}.")
            else:
                yield from walk(child, prefix)

    yield from walk(tree, "")


def measure(roots: Sequence[str]) -> list[dict[str, object]]:
    """Every function under the roots, most complex first; a file that doesn't parse raises."""
    found: list[dict[str, object]] = []
    for root in roots:
        base = Path(root)
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in files:
            tree = ast.parse(path.read_bytes(), filename=str(path))
            found.extend(functions(tree, path.as_posix()))
    found.sort(key=lambda f: (-int(str(f["complexity"])), str(f["file"]), int(str(f["line"]))))
    return found


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    out = None
    if "--out" in args:
        at = args.index("--out")
        if at + 1 >= len(args):
            print("--out necesita una ruta", file=sys.stderr)
            return 2
        out = args[at + 1]
        del args[at : at + 2]
    if not args:
        print("Uso: lictor_complexity <rutas> [--out informe.json]", file=sys.stderr)
        return 2
    try:
        found = measure(args)
    except (SyntaxError, ValueError, OSError) as exc:
        print(f"No se pudo analizar: {exc}", file=sys.stderr)
        return 2
    print(DEFINITION)
    for f in found:
        print(f"{f['complexity']:>4}  {f['file']}:{f['line']}  {f['function']}")
    if out is not None:
        Path(out).write_text(json.dumps({"functions": found}, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
