"""The form of a declared stub line, and the variants it gives (ADR-0021)."""

import pytest

from lictor.domain.declared_stubs import (
    DeclaredStub,
    StubFormProblem,
    declared_stubs,
    declared_stubs_rule,
    declared_variant,
    form_problem_text,
    parse_stub_line,
    single_variant,
)

GO = '\tpanic("lictor: no implementado") // lictor-impostor: return 1 | return 2'
JS = '  throw new Error("lictor: no implementado"); // lictor-impostor: return "a"; | return "b";'
PY = (
    '        raise NotImplementedError("lictor: no implementado")'
    "  # lictor-impostor: return 1 | return 2"
)
VOID = '\tpanic("lictor: no implementado") // lictor-impostor: return'


@pytest.mark.parametrize(
    ("line", "indent", "constants"),
    [
        (GO, "\t", ("return 1", "return 2")),
        (JS, "  ", ('return "a";', 'return "b";')),
        (PY, "        ", ("return 1", "return 2")),
        (VOID, "\t", ("return",)),
        # A `||` of the code isn't the separator: only ` | ` between spaces is.
        (
            'x("lictor: no implementado") # lictor-impostor: return a||b | return c',
            "",
            ("return a||b", "return c"),
        ),
    ],
)
def test_a_stub_line_gives_its_indentation_and_its_constants(
    line: str, indent: str, constants: tuple[str, ...]
) -> None:
    assert parse_stub_line(7, line) == DeclaredStub(7, indent, constants)


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        (
            'panic("lictor: no implementado")',
            "lleva «lictor: no implementado» sin «lictor-impostor:» y sus constantes",
        ),
        (
            "// lictor-impostor: return 1 | return 2",
            "lleva «lictor-impostor:» sin «lictor: no implementado» antes, en la misma línea",
        ),
        (
            '// lictor-impostor: return 1 | panic("lictor: no implementado")',
            "lleva «lictor-impostor:» sin «lictor: no implementado» antes, en la misma línea",
        ),
        (
            '// lictor-impostor: a panic("lictor: no implementado") // lictor-impostor: b',
            "lleva «lictor-impostor:» sin «lictor: no implementado» antes, en la misma línea",
        ),
        ('panic("lictor: no implementado") // lictor-impostor:', "tiene una constante vacía"),
        (
            'panic("lictor: no implementado") // lictor-impostor: return 1 |  | x',
            "tiene una constante vacía",
        ),
        (
            'panic("lictor: no implementado") // lictor-impostor: a | b | c',
            "tiene 3 constantes: son dos separadas por «|» entre espacios, o una si la función no "
            "devuelve nada",
        ),
        (
            'panic("lictor: no implementado") // lictor-impostor: a |  a',
            "tiene dos constantes iguales: tienen que ser distintas",
        ),
    ],
)
def test_a_line_that_looks_like_a_stub_and_isnt_says_why(line: str, reason: str) -> None:
    assert parse_stub_line(3, line) == StubFormProblem(3, reason)


def test_only_the_first_mark_of_the_constants_opens_them() -> None:
    line = 'panic("lictor: no implementado") // lictor-impostor: return 1 | lictor-impostor: 2'

    assert parse_stub_line(1, line) == DeclaredStub(1, "", ("return 1", "lictor-impostor: 2"))


def test_a_line_with_neither_mark_is_no_stub() -> None:
    assert parse_stub_line(1, '\tpanic("no implementado")') is None


def test_a_file_gives_every_stub_and_every_line_written_wrong() -> None:
    text = (
        f"package x\n\nfunc A() int {{\n{GO}\n}}\n\n"
        'func B() {\n\tpanic("lictor: no implementado")\n}\n'
    )

    stubs, problems = declared_stubs(text)

    assert [s.line for s in stubs] == [4]
    assert [p.line for p in problems] == [8]


@pytest.mark.parametrize(("variant", "constant"), [(0, "return 1"), (1, "return 2")])
def test_each_variant_replaces_the_whole_line_keeping_its_end(variant: int, constant: str) -> None:
    text = f"func A() int {{\r\n{GO}\r\n}}\r\n"

    changed, replaced = declared_variant(text, variant)

    assert changed == f"func A() int {{\r\n\t{constant}\r\n}}\r\n"
    assert replaced == 1


def test_a_variant_keeps_no_blank_the_stub_line_had_at_its_end() -> None:
    text = f"{GO}   \nfunc B() {{\n{VOID}\n}}"

    assert declared_variant(text, 1) == ("\treturn 2\nfunc B() {\n\treturn\n}", 2)


def test_a_stub_without_a_result_keeps_its_only_constant_in_both_variants() -> None:
    assert declared_variant(VOID + "\n", 0) == declared_variant(VOID + "\n", 1) == ("\treturn\n", 1)


def test_a_line_written_wrong_is_never_replaced() -> None:
    text = 'panic("lictor: no implementado")\n'

    assert declared_variant(text, 0) == (text, 0)


def test_both_variants_are_one_tree_only_when_every_stub_has_one_constant() -> None:
    one = DeclaredStub(1, "", ("return",))
    two = DeclaredStub(2, "", ("return 1", "return 2"))

    assert single_variant([one])
    assert not single_variant([one, two])


def test_a_line_written_wrong_is_named_with_its_file() -> None:
    problem = StubFormProblem(8, "tiene una constante vacía")

    assert form_problem_text("nif/nif.go", problem) == (
        "La línea nif/nif.go:8 tiene una constante vacía."
    )


def test_the_driver_hears_the_form_only_when_the_profile_asks_for_it() -> None:
    assert declared_stubs_rule(False) == ""
    assert declared_stubs_rule(True) == (
        "- Cada stub falla en una línea sola, dentro de su función, con el texto `lictor: no "
        "implementado`, y esa línea acaba con un comentario `lictor-impostor: <A> | <B>`: dos "
        "sentencias que la sustituyen devolviendo dos valores distintos de su tipo, ninguno su "
        'valor cero salvo en un booleano (1 y 2, "a" y "b", una lista de uno y otra de dos, una '
        "estructura con cada campo lleno), nunca nulos, y que solo usan lo que ya usa la firma. Si "
        "la función no devuelve nada, una sola (`return`). Lictor pone cada constante en lugar "
        "del stub para comprobar que cada test mira el resultado: un test que pasa con todas se "
        "rechaza.\n"
    )
