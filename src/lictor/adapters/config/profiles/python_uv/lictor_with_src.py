"""Run a tool's command with the project's `src` at the end of the import path (S19 of 0.25.0).

The project isn't installed in the verification environment (an editable install would import the
agent's tree, not the clone's), and `src` only enters the path inside pytest
(`lictor_project_path`). A tool that imports the project by its name outside pytest, as
import-linter does, can't find it: `Could not find package`. This runs the tool's console script,
the one its distribution declares, after appending `src`: behind `site-packages`, as ADR-0019
wants, so a module of `src` can't replace a dependency. The current directory is the project's.

    uv run python -I -B -m lictor_with_src lint-imports --config pyproject.toml

`-I` keeps the interpreter from putting the current directory first; nothing else is added. Lictor
copies this file into the environment, so it imports nothing of Lictor; valid back to Python 3.9.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

USAGE = "uso: python -I -B -m lictor_with_src <orden> [argumentos…]"


def console_script(name: str) -> Callable[[], object] | None:
    """The function behind a console script of the environment, or None if none is called so."""
    for entry in _console_scripts():
        if entry.name == name:
            loaded: Callable[[], object] = entry.load()
            return loaded
    return None


def _console_scripts() -> Iterable[Any]:
    """The console scripts of the environment: `select` since Python 3.10, a dict before."""
    found: Any = metadata.entry_points()
    scripts: Iterable[Any] = (
        found.select(group="console_scripts")
        if hasattr(found, "select")
        else found.get("console_scripts", [])
    )
    return scripts


def main(argv: Sequence[str], project: Path) -> int:
    """Append `src`, then run the tool as its console script would: its exit code is ours."""
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    name, *rest = argv
    sys.path.append(str(project / "src"))
    tool = console_script(name)
    if tool is None:
        print(f"lictor_with_src: no hay ninguna orden «{name}» en el entorno", file=sys.stderr)
        return 2
    sys.argv = [name, *rest]
    try:
        result = tool()
    except SystemExit as exit_:
        return _code(exit_.code)
    return _code(result)


def _code(result: object) -> int:
    """A console script's result as an exit code, as `sys.exit` reads it."""
    if result is None:
        return 0
    if isinstance(result, int):
        return result
    print(result, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], Path.cwd()))
