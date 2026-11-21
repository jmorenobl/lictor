"""The definition of complexity Lictor uses (ADR-0013), construct by construct.

Each row is a function and the complexity the definition gives it. The first ones are where radon
6.0.1 and lizard 1.25.0 agree, which is all the code of the passes measured; the last ones are
where they don't, and say what Lictor decided.
"""

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_complexity

_CONSTRUCTS: list[tuple[str, str, int]] = [
    ("nada", "def f():\n    return 1", 1),
    ("if", "def f(x):\n    if x:\n        return 1\n    return 2", 2),
    (
        "if, elif, else",
        (
            "def f(x):\n"
            "    if x == 1:\n"
            "        return 1\n"
            "    elif x:\n"
            "        return 2\n"
            "    else:\n"
            "        return 3"
        ),
        3,
    ),
    ("ternario", "def f(x):\n    return 1 if x else 2", 2),
    ("for", "def f(xs):\n    for x in xs:\n        print(x)", 2),
    ("async for", "async def f(xs):\n    async for x in xs:\n        print(x)", 2),
    ("while", "def f(x):\n    while x:\n        x -= 1", 2),
    (
        "dos except",
        (
            "def f(x):\n"
            "    try:\n"
            "        return 1 / x\n"
            "    except ZeroDivisionError:\n"
            "        return 0\n"
            "    except TypeError:\n"
            "        return -1"
        ),
        3,
    ),
    ("with", "def f(p):\n    with open(p) as h:\n        return h.read()", 1),
    ("a and b and c", "def f(a, b, c):\n    if a and b and c:\n        return 1\n    return 0", 4),
    ("a and b or c", "def f(a, b, c):\n    if a and b or c:\n        return 1\n    return 0", 4),
    ("and fuera de un if", "def f(a, b):\n    return a and b", 2),
    ("comprensión con if", "def f(xs):\n    return [x for x in xs if x]", 3),
    ("comprensión", "def f(xs):\n    return [x for x in xs]", 2),
    ("dos for en una comprensión", "def f(xs):\n    return [y for x in xs for y in x]", 3),
    ("lambda con ternario", "def f(xs):\n    return sorted(xs, key=lambda x: x if x else 0)", 2),
    (
        "match, el último case _",
        (
            "def f(x):\n"
            "    match x:\n"
            "        case 1:\n"
            "            return 1\n"
            "        case 2:\n"
            "            return 2\n"
            "        case _:\n"
            "            return 3"
        ),
        3,
    ),
    # Where radon and lizard differ, or don't know: what Lictor decided.
    (
        "else de un for: no decide nada",
        "def f(xs):\n    for x in xs:\n        print(x)\n    else:\n        print()",
        2,
    ),
    (
        "else de un try",
        (
            "def f(x):\n"
            "    try:\n"
            "        x()\n"
            "    except ValueError:\n"
            "        pass\n"
            "    else:\n"
            "        print()"
        ),
        2,
    ),
    ("finally", "def f(x):\n    try:\n        return x\n    finally:\n        print()", 1),
    ("assert, con and dentro", "def f(a, b):\n    assert a and b", 2),
    ("except*", "def f(x):\n    try:\n        x()\n    except* ValueError:\n        pass", 2),
    (
        "case _ con guarda: decide",
        (
            "def f(x, y):\n"
            "    match x:\n"
            "        case 1:\n"
            "            return 1\n"
            "        case _ if y:\n"
            "            return 2"
        ),
        3,
    ),
    (
        "case que captura, al final",
        (
            "def f(x):\n"
            "    match x:\n"
            "        case 1:\n"
            "            return 1\n"
            "        case otro:\n"
            "            return otro"
        ),
        2,
    ),
    (
        "case _ que no es el último",
        (
            "def f(x):\n"
            "    match x:\n"
            "        case _ if x:\n"
            "            return 1\n"
            "        case 2:\n"
            "            return 2"
        ),
        3,
    ),
    (
        "decorador y valor por defecto: no son de la función",
        "@dec(a if b else c)\ndef f(x=1 if y else 2):\n    return x",
        1,
    ),
]


@pytest.mark.parametrize(
    ("source", "expected"), [pytest.param(s, e, id=name) for name, s, e in _CONSTRUCTS]
)
def test_each_construct_counts_as_the_definition_says(source: str, expected: int) -> None:
    [found] = lictor_complexity.functions(ast.parse(source), "m.py")

    assert found["complexity"] == expected


def test_a_nested_function_and_a_method_count_apart_and_say_where_they_are() -> None:
    source = textwrap.dedent(
        """
        def fuera(xs):
            def dentro(x):
                if x:
                    return 1
                return 0
            return [dentro(x) for x in xs]

        class C:
            def m(self, x):
                if x:
                    return 1

        def con_clase():
            class D:
                def n(self, y):
                    return y if y else 0
            return D
        """
    )

    found = {
        f["function"]: f["complexity"] for f in lictor_complexity.functions(ast.parse(source), "m")
    }

    assert found == {"fuera": 2, "fuera.dentro": 2, "C.m": 2, "con_clase": 1, "con_clase.D.n": 2}


def test_code_at_module_level_or_in_a_class_body_is_not_measured() -> None:
    source = "x = 1 if y else 2\nclass C:\n    z = [a for a in b if a]\n"

    assert list(lictor_complexity.functions(ast.parse(source), "m")) == []


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", lictor_complexity.__file__, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_prints_every_function_and_writes_the_report(tmp_path: Path) -> None:
    (tmp_path / "src" / "p").mkdir(parents=True)
    (tmp_path / "src" / "p" / "a.py").write_text("def f(x):\n    if x:\n        return 1\n")
    (tmp_path / "src" / "p" / "b.py").write_text("def g():\n    return 2\n")

    done = _run(tmp_path, "src", "--out", "r.json")

    assert done.returncode == 0
    assert done.stdout.splitlines()[0] == lictor_complexity.DEFINITION
    assert done.stdout.splitlines()[1:] == [
        "   2  src/p/a.py:1  f",
        "   1  src/p/b.py:1  g",
    ]
    assert json.loads((tmp_path / "r.json").read_text()) == {
        "functions": [
            {"file": "src/p/a.py", "function": "f", "line": 1, "complexity": 2},
            {"file": "src/p/b.py", "function": "g", "line": 1, "complexity": 1},
        ]
    }


def test_a_file_that_doesnt_parse_fails_and_leaves_no_report(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(:\n")

    done = _run(tmp_path, "src", "--out", "r.json")

    assert done.returncode == 2
    assert "No se pudo analizar" in done.stderr
    assert not (tmp_path / "r.json").exists()


@pytest.mark.parametrize("args", [[], ["src", "--out"]])
def test_a_command_without_what_it_needs_fails(tmp_path: Path, args: list[str]) -> None:
    assert _run(tmp_path, *args).returncode == 2


def test_it_measures_a_single_file_too(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(a, b):\n    return a or b\n")

    assert lictor_complexity.measure([str(tmp_path / "m.py")])[0]["complexity"] == 2
