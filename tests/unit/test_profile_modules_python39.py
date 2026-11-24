"""The profile's modules run in the project's interpreter, so they must work back to Python 3.9.

Before 0.17.0, `lictor_impostor.py` and `lictor_outcome.py` failed to import on 3.9 (a
`TypeGuard` from `typing`, an annotation `X | None` evaluated at definition), and both are in the
plugins of every acceptance: a project on 3.9 couldn't run its acceptance at all. The parse with
3.9's grammar always runs; the import, where a Python 3.9 is installed.
"""

import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lictor.adapters.config.loader import DEFAULT_PROFILES_DIR

MODULES = sorted((DEFAULT_PROFILES_DIR / "python_uv").glob("lictor_*.py"))
OLDEST = (3, 9)


def _evaluated_annotations(tree: ast.Module) -> bool:
    """Whether the module has annotations Python evaluates when it defines them."""
    return any(
        isinstance(node, ast.AnnAssign)
        or (isinstance(node, ast.arg) and node.annotation is not None)
        or (isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None)
        for node in ast.walk(tree)
    )


def _postponed(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _unions_in_calls(tree: ast.Module) -> list[int]:
    """Lines where `A | B` is passed to `isinstance` or `issubclass`: a TypeError before 3.10."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("isinstance", "issubclass")
        and len(node.args) == 2
        and isinstance(node.args[1], ast.BinOp)
        and isinstance(node.args[1].op, ast.BitOr)
    ]


@pytest.mark.parametrize("module", MODULES, ids=[m.name for m in MODULES])
def test_each_module_is_written_for_python_3_9(module: Path) -> None:
    source = module.read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=OLDEST)

    if _evaluated_annotations(tree):
        assert _postponed(tree), "sin `from __future__ import annotations`"
    assert _unions_in_calls(tree) == []


def _python_3_9() -> str | None:
    uv = shutil.which("uv")
    if uv is None:
        return None
    found = subprocess.run(
        [uv, "python", "find", "3.9"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "UV_PYTHON_DOWNLOADS": "never"},
    )
    return found.stdout.strip() or None if found.returncode == 0 else None


@pytest.mark.parametrize("module", MODULES, ids=[m.name for m in MODULES])
def test_each_module_imports_on_python_3_9_where_one_is_installed(module: Path) -> None:
    python = _python_3_9()
    if python is None:
        pytest.skip("no hay un Python 3.9 instalado")
    # pytest isn't in that interpreter: what a plugin reads of it at import is stood in for.
    stand_in = (
        "import sys, types\n"
        "class _Any:\n"
        "    def __getattr__(self, name): return _Any()\n"
        "    def __call__(self, *a, **k):\n"
        "        return a[0] if len(a) == 1 and callable(a[0]) and not k else _Any()\n"
        "    def __getitem__(self, key): return _Any()\n"
        "sys.modules['pytest'] = _Any()\n"
        f"sys.path.insert(0, {str(module.parent)!r})\n"
        f"__import__({module.stem!r})\n"
    )
    done = subprocess.run(
        [python, "-I", "-B", "-c", stand_in], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr
