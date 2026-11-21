"""The definition of a clone Lictor uses (ADR-0014), case by case.

Each row is a set of modules and what the definition finds in them: how many clones (families)
and how many tokens are in one. The body `_BODY` has exactly 50 tokens that count (its signature
doesn't), the least a clone has, and `_SHORT` 49.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_duplication

_HEAD = (
    "    total = 0\n"
    "    for e in datos:\n"
    "        if e is None:\n"
    "            continue\n"
    "        total += e * factor\n"
    "        if total > limite:\n"
    '            raise ValueError("demasiado")\n'
)
_BODY = _HEAD + "    return round(-total / len(datos), 2)\n"
_SHORT = _HEAD + "    return round(total / len(datos), 2)\n"


def _function(name: str, body: str = _BODY, signature: str = "(datos, factor, limite)") -> str:
    return f"def {name}{signature}:\n{body}"


_RENAMED = (
    "    suma = 1\n"
    "    for x in valores:\n"
    "        if x is None:\n"
    "            continue\n"
    "        suma += x * peso\n"
    "        if suma > tope:\n"
    '            raise ValueError("mucho")\n'
    "    return round(-suma / len(valores), 3)\n"
)
# `total` and `factor` are one name in the copy: `t += e * t` can't stand for `total += e * factor`.
_CLASHING = _BODY.replace("total", "t").replace("factor", "t")
# Lines broken inside the brackets the code already has: a bracket more would be a token more.
_REFORMATTED = (
    "    total = 0  # empieza en cero\n"
    "    for e in datos:\n"
    "        if e is None:\n"
    "            continue\n"
    "        total += e * factor  # suma\n"
    "        if total > limite:\n"
    "            raise ValueError(\n"
    '                "demasiado"\n'
    "            )\n"
    "    return round(\n"
    "        -total / len(datos), 2\n"
    "    )\n"
)
_IMPORTS = "".join(f"from paquete.modulo{i} import nombre{i}, otro{i}\n" for i in range(10))
_SIGNATURE = "(" + ", ".join(f"p{i}: dict[str, int]" for i in range(8)) + ") -> None"
_DOCSTRING = '    """' + " ".join(f"palabra{i}" for i in range(80)) + '"""\n'
_TABLE = "T = {\n" + "".join(f'    "clave{i}": {i},\n' for i in range(40)) + "}\n"
_FIELDS = "".join(f"    campo{i}: {'int' if i % 2 else 'str'}\n" for i in range(14))
# Six blocks of 23 tokens with an `if`: logic, not a table. Two blocks that follow each other are
# shorter than a clone, but the first three are 69 tokens that repeat in the last three.
_UNROLLED = "".join(
    f"    v{i} = datos[{i}] * factor + limite\n    if v{i} > limite:\n        v{i} = limite - {i}\n"
    for i in range(1, 7)
)

_CASES: list[tuple[str, dict[str, str], int, int]] = [
    ("en dos ficheros", {"a.py": _function("f"), "b.py": _function("g")}, 1, 100),
    ("en un fichero", {"a.py": _function("f") + "\n\n" + _function("g")}, 1, 100),
    (
        "49 tokens no son un clon",
        {"a.py": _function("f", _SHORT), "b.py": _function("g", _SHORT)},
        0,
        0,
    ),
    (
        "otro formato y comentarios",
        {"a.py": _function("f"), "b.py": _function("g", _REFORMATTED)},
        1,
        100,
    ),
    (
        "nombres y literales cambiados de forma coherente",
        {"a.py": _function("f"), "b.py": _function("g", _RENAMED, "(valores, peso, tope)")},
        1,
        100,
    ),
    (
        "dos nombres de una copia que son uno en la otra",
        {"a.py": _function("f"), "b.py": _function("g", _CLASHING)},
        0,
        0,
    ),
    (
        "cuerpos iguales con firmas distintas",
        {"a.py": _function("f"), "b.py": _function("g", signature="(x: int, *ys: str) -> int")},
        1,
        100,
    ),
    (
        "importaciones iguales",
        {"a.py": _IMPORTS + "X = 1\n", "b.py": _IMPORTS + "Y = [2]\n"},
        0,
        0,
    ),
    (
        "firmas iguales con cuerpos distintos",
        {
            "a.py": _function("f", "    return None\n", _SIGNATURE),
            "b.py": _function("g", "    print(p0)\n", _SIGNATURE),
        },
        0,
        0,
    ),
    (
        "docstrings iguales",
        {
            "a.py": _function("f", _DOCSTRING + "    return 1\n"),
            "b.py": _function("g", _DOCSTRING + "    return [2]\n"),
        },
        0,
        0,
    ),
    ("dos tablas", {"a.py": _TABLE, "b.py": _TABLE.replace("T =", "U =")}, 0, 0),
    (
        "dos listas de campos",
        {
            "a.py": "class A:\n" + _FIELDS,
            "b.py": "class B:\n" + _FIELDS.replace("campo", "dato"),
        },
        0,
        0,
    ),
    (
        "lógica desenrollada en dos ficheros",
        {
            "a.py": _function("f", _UNROLLED + "    return v1\n"),
            "b.py": _function("g", _UNROLLED + "    return None\n"),
        },
        1,
        2 * 139,
    ),
    (
        "lógica desenrollada en un fichero: la primera mitad repite la segunda",
        {"a.py": _function("f", _UNROLLED + "    return v1\n")},
        1,
        2 * 69,
    ),
    (
        "tres copias",
        {"a.py": _function("f"), "b.py": _function("g"), "c.py": _function("h")},
        1,
        150,
    ),
]


def _report(sources: dict[str, str]) -> lictor_duplication.Report:
    return lictor_duplication.report_of({k: v.encode() for k, v in sources.items()})


def _found(sources: dict[str, str]) -> tuple[int, int]:
    report = _report(sources)
    return len(report.families), report.in_clones


def test_the_bodies_are_as_long_as_the_cases_say() -> None:
    assert len(lictor_duplication.tokens(_function("f").encode())) == 50
    assert len(lictor_duplication.tokens(_function("f", _SHORT).encode())) == 49


@pytest.mark.parametrize(
    ("sources", "clones", "in_clones"),
    [pytest.param(s, c, t, id=name) for name, s, c, t in _CASES],
)
def test_each_case_is_what_the_definition_says(
    sources: dict[str, str], clones: int, in_clones: int
) -> None:
    assert _found(sources) == (clones, in_clones)


def test_renaming_a_local_of_one_function_doesnt_break_the_clone_with_the_next() -> None:
    first = "    actual = mirar(datos)\n    return actual[2] if actual else limite\n"
    after = _function("f", first) + "\n" + _function("g")
    renamed = _function("f", first.replace("actual", "token")) + "\n" + _function("g")

    assert _found({"a.py": after, "b.py": after}) == _found({"a.py": after, "b.py": renamed})


def test_what_a_file_is_called_doesnt_change_the_count() -> None:
    sources = {
        "analizador.py": _function("f") + _function("g", _SHORT),
        "evaluador.py": _function("h"),
    }

    renamed = {"sintaxis.py": sources["analizador.py"], "evaluador.py": sources["evaluador.py"]}

    assert _found(sources) == _found(renamed)


def test_a_function_copied_many_times_is_one_clone_with_every_copy() -> None:
    source = "\n\n".join(_function(f"f{i}") + f"X{i} = {i}\n" for i in range(30))

    report = _report({"m.py": source})

    [family] = report.families
    assert len(family) == 30
    assert report.in_clones == sum(o.end - o.start for o in family)


def test_a_clone_says_where_it_is_and_starts_at_its_first_statement() -> None:
    report = _report({"a.py": "x = 1\n\n" + _function("f"), "b.py": _function("g")})

    assert report.as_json() == {
        "min_tokens": 50,
        "files": [
            {"file": "a.py", "tokens": 54, "covered": [[4, 54]]},
            {"file": "b.py", "tokens": 50, "covered": [[0, 50]]},
        ],
        "families": [
            {
                "occurrences": [
                    {"file": "a.py", "first_line": 4, "last_line": 11, "start": 4, "end": 54},
                    {"file": "b.py", "first_line": 2, "last_line": 9, "start": 0, "end": 50},
                ]
            }
        ],
    }


def test_an_f_string_is_one_token_its_source() -> None:
    found = lictor_duplication.tokens(b'x = f"{a} {{b}} {c!r:>4}"\n')

    assert [(t.kind, t.text) for t in found] == [
        ("ID", "x"),
        ("=", "="),
        ("STR", 'f"{a} {{b}} {c!r:>4}"'),
        ("\n", "\n"),
    ]


def test_a_one_line_function_with_non_ascii_in_its_signature_keeps_its_body() -> None:
    found = lictor_duplication.tokens("def f(año): return año + 1\n".encode())

    assert [t.kind for t in found] == ["return", "ID", "+", "NUM", "\n"]


def test_each_token_knows_its_function_and_outside_them_none() -> None:
    source = "x = 1\ndef f():\n    a = 1\n    def g():\n        return 2\n    return a\n"

    found = lictor_duplication.tokens(source.encode())

    assert [(t.text, t.scope) for t in found if t.kind != "\n"] == [
        ("x", 0),
        ("=", 0),
        ("1", 0),
        ("a", 1),
        ("=", 1),
        ("1", 1),
        ("return", 2),
        ("2", 2),
        ("return", 1),
        ("a", 1),
    ]


@pytest.mark.parametrize(
    ("kinds", "period"),
    [([], 0), (["a"], 1), (["a", "b", "a", "b"], 2), (["a", "b", "a"], 2), (["a", "b", "c"], 3)],
)
def test_the_smallest_period(kinds: list[str], period: int) -> None:
    assert lictor_duplication.smallest_period(kinds) == period


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", lictor_duplication.__file__, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_command_prints_the_total_and_every_clone_and_writes_the_report(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "p").mkdir(parents=True)
    (tmp_path / "src" / "p" / "a.py").write_text(_function("f"))
    (tmp_path / "src" / "p" / "b.py").write_text(_function("g"))

    done = _run(tmp_path, "src", "--out", "r.json")

    assert done.returncode == 0
    assert done.stdout.splitlines() == [
        lictor_duplication.DEFINITION,
        "100 tokens en clones, de 100; 1 clones.",
        "   50  src/p/a.py:2-9, src/p/b.py:2-9",
    ]
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["min_tokens"] == 50
    assert [f["file"] for f in report["files"]] == ["src/p/a.py", "src/p/b.py"]


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
    (tmp_path / "m.py").write_text(_function("f") + _function("g"))

    report = lictor_duplication.measure([str(tmp_path / "m.py")])

    assert (report.in_clones, report.code_tokens) == (100, 100)
