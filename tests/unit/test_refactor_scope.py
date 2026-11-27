"""The scope of a refactor (F84): what it is asked, and what `L0-alcance` lets it change."""

import json
from pathlib import Path
from typing import Any

import pytest

from lictor.domain.complexity import OverLimit
from lictor.domain.duplication import Duplication
from lictor.domain.model import TreeChange
from lictor.domain.refactor import Findings
from lictor.domain.refactor_scope import (
    NO_CODE,
    CodePaths,
    LineSpan,
    RefactorScope,
    code_paths_problem,
    excluded_everything,
    hunk_span,
    out_of_scope,
    out_of_scope_reason,
    outside_scope,
    outside_scope_text,
    refactor_scope,
    scope_rule,
)
from lictor.domain.reports import Clone, CloneOccurrence, FunctionComplexity

_CODE = CodePaths(("src/",))
_FIXTURE = Path(__file__).parents[1] / "fixtures" / "refactors_pyverifactu_0_20_0.json"


def _f(file: str, line: int, last: int, name: str = "f", value: int = 12) -> FunctionComplexity:
    return FunctionComplexity(file, name, line, value, last)


def _clone(*places: tuple[str, int, int]) -> Clone:
    return Clone(tuple(CloneOccurrence(f, a, b, 0, 60) for f, a, b in places))


def _changed(path: str, old: str = "100644", new: str = "100644") -> TreeChange:
    return TreeChange(path, new, old)


# The span of a hunk.


def test_a_hunk_that_adds_or_changes_lines_is_those_lines() -> None:
    assert hunk_span(4, 3) == LineSpan(4, 6)
    assert hunk_span(4, 1) == LineSpan(4, 4)


def test_a_hunk_that_only_deletes_is_the_line_before_it() -> None:
    """Deleting a whole function between `f` and `g` touches the end of `f`, not `g`."""
    assert hunk_span(9, 0) == LineSpan(9, 9)
    assert hunk_span(0, 0) == LineSpan(1, 1)


def test_a_span_overlaps_the_lines_it_shares() -> None:
    span = LineSpan(5, 8)

    assert span.overlaps(8, 12)
    assert span.overlaps(1, 5)
    assert span.overlaps(6, 7)
    assert not span.overlaps(9, 12)
    assert not span.overlaps(1, 4)


# What is asked: the functions and clones on the green's lines.


def _scope(changed: dict[str, tuple[LineSpan, ...]], *findings: Findings) -> RefactorScope:
    return refactor_scope(changed, _CODE, findings)


def test_a_function_is_named_when_the_green_changed_one_of_its_lines() -> None:
    inside, before, after = _f("src/a.py", 10, 20), _f("src/a.py", 1, 9), _f("src/a.py", 21, 30)
    elsewhere = _f("src/b.py", 10, 20)

    scope = _scope(
        {"src/a.py": (LineSpan(20, 20),)}, OverLimit(10, (inside, before, after, elsewhere))
    )

    assert scope.functions == (inside,)


def test_deleting_the_end_of_a_function_names_it_and_not_the_next_one() -> None:
    f, g = _f("src/a.py", 1, 9, "f"), _f("src/a.py", 10, 20, "g")

    scope = _scope({"src/a.py": (hunk_span(9, 0),)}, OverLimit(10, (f, g)))

    assert scope.functions == (f,)


def test_deleting_lines_inside_a_function_names_it() -> None:
    """A hunk `+12,0` deleted what came after line 12, inside `g`."""
    f, g = _f("src/a.py", 1, 9, "f"), _f("src/a.py", 10, 20, "g")

    scope = _scope({"src/a.py": (hunk_span(12, 0),)}, OverLimit(10, (f, g)))

    assert scope.functions == (g,)


def test_a_change_in_a_nested_function_names_it_and_the_one_that_holds_it() -> None:
    outer, inner = _f("src/a.py", 1, 30, "f"), _f("src/a.py", 5, 9, "f.g")

    scope = _scope({"src/a.py": (LineSpan(6, 6),)}, OverLimit(10, (outer, inner)))

    assert scope.functions == (outer, inner)


def test_a_clone_is_named_when_one_of_its_places_is_on_the_green_lines() -> None:
    mine = _clone(("src/a.py", 1, 9), ("src/b.py", 40, 48))
    same_file = _clone(("src/a.py", 50, 60), ("src/a.py", 70, 80))
    theirs = _clone(("src/c.py", 1, 9), ("src/d.py", 1, 9))

    scope = _scope(
        {"src/a.py": (LineSpan(5, 5), LineSpan(75, 75))},
        Duplication(50, 360, (mine, same_file, theirs)),
    )

    assert scope.clones == (mine, same_file)
    assert scope.files == frozenset({"src/a.py"})
    assert scope.clone_files == frozenset({"src/b.py"})
    assert scope.allowed == frozenset({"src/a.py", "src/b.py"})


def test_without_measures_nothing_is_named_and_the_files_come_from_the_diff() -> None:
    scope = _scope({"src/a.py": (), "tests/unit/test_a.py": (LineSpan(1, 3),)})

    assert scope == RefactorScope(frozenset({"src/a.py"}), frozenset())


def test_a_file_the_green_changed_with_no_lines_is_in_its_scope() -> None:
    """An empty `__init__.py` it added has no hunk, and the refactor may write its exports."""
    scope = _scope({"src/p/__init__.py": ()})

    assert out_of_scope(scope, [_changed("src/p/__init__.py")], _CODE) == []


# What the gate lets the refactor change.


def test_a_code_file_the_green_didnt_change_is_out_of_scope() -> None:
    scope = _scope({"src/a.py": (LineSpan(1, 1),)})

    changes = [_changed("src/a.py"), _changed("src/b.py"), _changed("src/c.py")]

    assert out_of_scope(scope, changes, _CODE) == ["src/b.py", "src/c.py"]


def test_new_files_and_what_isnt_code_are_never_out_of_scope() -> None:
    scope = _scope({"src/a.py": (LineSpan(1, 1),)})

    changes = [_changed("src/nuevo.py", old="000000"), _changed("tests/unit/test_otro.py")]

    assert out_of_scope(scope, changes, _CODE) == []


def test_deleting_or_moving_a_file_the_green_didnt_change_is_out_of_scope() -> None:
    scope = _scope({"src/a.py": (LineSpan(1, 1),)})
    moved = [_changed("src/b.py", new="000000"), _changed("src/movido.py", old="000000")]

    assert out_of_scope(scope, moved, _CODE) == ["src/b.py"]


def test_moving_a_file_of_the_scenario_stays_in_scope() -> None:
    scope = _scope({"src/a.py": (LineSpan(1, 1),)})
    moved = [_changed("src/a.py", new="000000"), _changed("src/nuevo/a.py", old="000000")]

    assert out_of_scope(scope, moved, _CODE) == []


def test_the_other_side_of_a_named_clone_may_change() -> None:
    clone = _clone(("src/a.py", 1, 9), ("src/b.py", 40, 48))
    scope = _scope({"src/a.py": (LineSpan(5, 5),)}, Duplication(50, 120, (clone,)))

    assert out_of_scope(scope, [_changed("src/a.py"), _changed("src/b.py")], _CODE) == []


def test_a_clone_past_those_a_message_names_still_opens_its_file() -> None:
    """F84, B1: the checkpoint keeps every clone, so the twenty-first one counts too."""
    others = tuple(_clone((f"src/x{i}.py", 1, 9), (f"src/y{i}.py", 1, 9)) for i in range(20))
    mine = _clone(("src/a.py", 1, 9), ("src/b.py", 40, 48))
    scope = _scope({"src/a.py": (LineSpan(5, 5),)}, Duplication(50, 9999, (*others, mine)))

    assert scope.clones == (mine,)
    assert out_of_scope(scope, [_changed("src/b.py")], _CODE) == []


def test_without_code_paths_nothing_is_out_of_scope() -> None:
    scope = refactor_scope({"src/a.py": ()}, NO_CODE, ())

    assert out_of_scope(scope, [_changed("src/b.py")], NO_CODE) == []


# The 21 refactors of pyverifactu with the 0.20.0 (F84): 9 left their scenario, 12 didn't.

_LEFT = {
    "NIF-001": ["services/xml_generator.py"],
    "ALM-001": ["cli/__init__.py"],
    "CLI-001": ["clients/aeat_soap.py"],
    "CLI-002": ["services/qr_generator.py"],
    "AEAT-001": [
        "models/customer.py",
        "models/issuer.py",
        "services/xml_validator.py",
        "utils/validators.py",
    ],
    "AEAT-003": ["models/customer.py", "models/issuer.py"],
    "PRE-001": ["clients/auth.py"],
    "COR-001": ["clients/aeat_soap.py"],
    "COR-002": ["clients/soap_envelope.py", "services/chain_check.py"],
}
_ROWS: list[dict[str, Any]] = json.loads(_FIXTURE.read_text(encoding="utf-8"))
_PKG = "src/verifactu_py/"


def _row_scope(row: dict[str, Any]) -> RefactorScope:
    changed = {
        p: tuple(LineSpan(a, b) for a, b in spans) for p, spans in row["green_lines"].items()
    }
    over = OverLimit(10, tuple(_f(f, a, b, n, v) for f, n, a, b, v in row["functions"]))
    clones = tuple(_clone(*(tuple(place) for place in c)) for c in row["clones"])
    return refactor_scope(changed, _CODE, (over, Duplication(50, 1, clones)))


def _row_changes(row: dict[str, Any]) -> list[TreeChange]:
    return [TreeChange(path, new, old) for path, old, new in row["changes"]]


def test_the_table_has_every_refactor_of_the_pass() -> None:
    assert len(_ROWS) == 21
    assert set(_LEFT) <= {row["refactor"] for row in _ROWS}


@pytest.mark.parametrize("row", _ROWS, ids=[row["refactor"] for row in _ROWS])
def test_the_gate_discards_the_nine_that_left_their_scenario_and_no_other(
    row: dict[str, Any],
) -> None:
    outside = out_of_scope(_row_scope(row), _row_changes(row), _CODE)

    assert outside == [_PKG + path for path in _LEFT.get(row["refactor"], [])]


def test_enr_001_isnt_asked_for_the_old_sending_it_left_alone() -> None:
    """Its file holds the old sending; by file it would have been told to lower 3 of them."""
    [row] = [row for row in _ROWS if row["refactor"] == "ENR-001"]

    scope = _row_scope(row)

    assert scope.functions == ()
    assert _PKG + "clients/aeat_soap.py" in scope.files


@pytest.mark.parametrize("refactor", ["REG-001", "VER-001"])
def test_the_two_with_a_clone_on_their_lines_are_asked_for_it(refactor: str) -> None:
    [row] = [row for row in _ROWS if row["refactor"] == refactor]

    assert _row_scope(row).clones


# What the prompt and the discard say.


_RULE_TAIL = (
    ". Puedes crear ficheros nuevos para lo que saques de ellos, y los tests unitarios pueden "
    "seguir al código que muevas. Modificar, mover o borrar cualquier otro fichero de código que "
    "ya exista descarta el refactor. Si un cambio te obligaría a tocar otro fichero de código, por "
    "ejemplo a quien importa lo que mueves, no hagas ese cambio."
)


def test_the_rule_names_the_files_of_the_scenario_and_of_its_clones() -> None:
    clone = _clone(("src/a.py", 1, 9), ("src/c.py", 40, 48), ("src/b.py", 1, 9))
    scope = _scope({"src/z.py": (), "src/a.py": (LineSpan(5, 5),)}, Duplication(50, 1, (clone,)))

    assert scope_rule(scope) == (
        "- Este refactor mejora lo que cambió el escenario. De código solo puedes modificar los "
        "ficheros que cambió el escenario (src/a.py, src/z.py), y los que comparten con ellos un "
        "clon de los que se nombran abajo (src/b.py, src/c.py)" + _RULE_TAIL
    )


def test_the_rule_says_when_the_scenario_changed_no_code() -> None:
    assert scope_rule(_scope({"tests/unit/test_a.py": ()})) == (
        "- Este refactor mejora lo que cambió el escenario. De código solo puedes modificar "
        "ninguno de los que ya existen, porque el escenario no cambió ningún fichero de código"
        + _RULE_TAIL
    )


def test_the_discard_names_what_it_changed_and_what_it_may() -> None:
    scope = _scope({"src/b.py": (), "src/a.py": ()})

    reason = out_of_scope_reason(["src/c.py", "src/d.py"], scope)

    assert reason == (
        "El refactor cambia ficheros de código que este escenario no cambió: src/c.py, src/d.py. "
        "De código solo puede cambiar los que ya existen entre estos: src/a.py, src/b.py; y crear "
        "ficheros nuevos."
    )
    assert "entre estos: ninguno;" in out_of_scope_reason(["src/b.py"], _scope({}))


@pytest.mark.parametrize(
    ("functions", "clones", "text"),
    [
        (0, 0, ""),
        (1, 0, "hay 1 función por encima del límite: no"),
        (2, 0, "hay 2 funciones por encima del límite: no"),
        (0, 1, "hay 1 clon: no"),
        (0, 2, "hay 2 clones: no"),
        (2, 3, "hay 2 funciones por encima del límite y 3 clones: no"),
    ],
)
def test_what_is_outside_is_counted_in_one_line(functions: int, clones: int, text: str) -> None:
    expected = f"Fuera de lo que cambió este escenario {text} son de este refactor." if text else ""

    assert outside_scope_text(functions, clones) == expected


# Where the code is, against what the agent may write.


def test_code_paths_must_reach_what_the_agent_may_write() -> None:
    assert code_paths_problem(("src/",), ("src/", "tests/unit/")) is None
    assert code_paths_problem(("src/",), ("src/paquete/",)) is None
    assert code_paths_problem(("src/paquete/",), ("src/",)) is None
    assert code_paths_problem(("lib/", "src/"), ("src/",)) is None
    assert code_paths_problem(("src/", "lib/"), ("lib/x/",)) is None
    assert code_paths_problem(("src/", "lib/"), ("lib/", "tests/unit/")) is None
    assert code_paths_problem(("src/", "app/"), ("lib/", "tests/unit/")) == (
        "paths.writable (lib/, tests/unit/) no incluye ninguna ruta de código "
        "(src/, app/): la puerta L0-alcance del refactor no comprobaría nada. Añade a "
        "paths.writable la ruta donde está el código."
    )
    assert code_paths_problem((), ("src/",)) == (
        "ni el perfil ni lictor.json declaran code_paths: la puerta L0-alcance no sabría qué "
        "es código. Escribe en lictor.json code_paths con las rutas de código."
    )


def test_what_the_project_excludes_from_its_code_paths_isnt_code() -> None:
    code = CodePaths(("iva/", "main.go"), ("*_test.go",))

    assert code.holds("iva/iva.go")
    assert code.holds("main.go")
    assert not code.holds("iva/iva_test.go")
    assert not code.holds("acceptance/x.go")
    assert not NO_CODE.holds("iva/iva.go")


def test_a_test_beside_the_code_may_follow_it_when_excluded() -> None:
    code = CodePaths(("iva/",), ("*_test.go",))
    scope = refactor_scope({"iva/iva.go": ()}, code, ())
    changes = [_changed("iva/iva.go"), _changed("iva/otro_test.go")]

    assert out_of_scope(scope, changes, code) == []
    assert out_of_scope(scope, changes, CodePaths(("iva/",))) == ["iva/otro_test.go"]
    assert refactor_scope({"iva/iva_test.go": ()}, code, ()).files == frozenset()


def test_what_is_outside_counts_every_measure_less_what_the_scope_names() -> None:
    near, far = _f("src/a.py", 1, 9, "f"), _f("src/b.py", 1, 9, "g")
    mine, theirs = _clone(("src/a.py", 1, 9), ("src/c.py", 1, 9)), _clone(("src/d.py", 1, 9))
    findings: list[Findings] = [
        OverLimit(10, (near, far)),
        OverLimit(5, (far,)),
        Duplication(50, 1, (mine, theirs)),
    ]
    scope = refactor_scope({"src/a.py": (LineSpan(3, 3),)}, _CODE, findings)

    assert scope.functions_of(OverLimit(10, (far, near))) == [near]
    assert scope.functions_of(None) == []
    assert scope.clones_of(Duplication(50, 1, (theirs, mine))) == [mine]
    assert scope.clones_of(None) == []
    assert outside_scope(findings, scope) == (
        "Fuera de lo que cambió este escenario hay 2 funciones por encima del límite y 1 clon: no "
        "son de este refactor."
    )
    assert outside_scope([], RefactorScope(frozenset(), frozenset())) == ""


def test_an_exclusion_that_leaves_no_code_is_named() -> None:
    files = ["go.mod", "iva/iva.go", "iva/iva_test.go", "acceptance/x_test.go"]

    assert excluded_everything(files, CodePaths(("iva/",), ("*_test.go",))) is None
    assert excluded_everything(files, CodePaths(("iva/",))) is None
    assert excluded_everything([], CodePaths(("iva/",), ("*",))) is None
    assert excluded_everything(["go.mod"], CodePaths(("iva/",), ("*",))) is None
    problem = excluded_everything(files, CodePaths(("iva/", "cmd/"), ("*.go", "x")))
    assert problem == (
        "code_exclude (*.go, x) excluye todos los ficheros de code_paths (iva/, cmd/): la "
        "puerta L0-alcance del refactor no comprobaría nada. Deja en code_exclude solo lo que no "
        "es código, como los tests junto a él (*_test.go)."
    )
