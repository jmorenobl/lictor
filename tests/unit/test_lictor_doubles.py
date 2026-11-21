"""The doubles of the stubs' impostors (ADR-0012), and what rule C decides of each way of checking.

The doubles are checked as a value first: what they answer, that they end, that they copy. Then two
tables of tests a driver writes over what a stub returned (one written for the design, one by its
review) run with None, True and the two doubles, and the domain's rule judges each. A correct row
is accepted unless it is a known limit; a weak one that is rejected today, because it passes with
None and with True, is never accepted. A row changes only with a deliberate change of the doubles
or of the rule.
"""

import copy
import dataclasses
import importlib.util
import json
import math
import operator
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_doubles
from lictor.adapters.config.profiles.python_uv.lictor_doubles import (
    BUDGET,
    DEPTH,
    EMPTY,
    ONE,
    LictorDoubleExhaustedError,
)
from lictor.domain.model import TestCaseResult
from lictor.domain.rules import StubConstant, impostor_source
from lictor.domain.stub_proof import stub_proof

D = Decimal
_PROFILE = Path(lictor_doubles.__file__).parent.parent / "python-uv.json"
_PROFILE_IMPOSTORS: dict[str, Any] = json.loads(_PROFILE.read_text(encoding="utf-8"))["oracle"][
    "impostors"
]


@pytest.fixture(autouse=True)
def _fresh_budget() -> None:
    lictor_doubles.reset()


def test_a_double_equals_nothing_but_itself() -> None:
    for value in (None, 0, "", False, True, [], (), date(2026, 1, 1), D("0")):
        assert value != ONE
        assert value != EMPTY
    same = ONE
    assert same == ONE
    assert ONE != EMPTY


def test_what_a_test_does_with_a_double_gives_another_double() -> None:
    kind = type(ONE)
    assert all(
        type(child) is kind
        for child in (ONE.vence, ONE[0], ONE(), ONE.total + 1, 1 - ONE, round(ONE.total, 2))
    )
    assert ONE.vence is ONE.vence
    assert ONE.total != ONE.envio  # each name its own child
    assert ONE[0] != ONE[1]
    assert type(ONE[[1]]) is kind  # a key a dict can't hold


def test_one_is_one_element_and_empty_none() -> None:
    assert (bool(ONE), len(ONE), len(list(ONE))) == (True, 1, 1)
    assert (bool(EMPTY), len(EMPTY), list(EMPTY)) == (False, 0, [])
    assert not EMPTY.vence
    assert ONE < 0
    assert ONE > 0
    assert not EMPTY < 0
    assert not EMPTY > 0


def test_a_double_converts_to_nothing_a_scenario_expects() -> None:
    assert math.isnan(float(ONE))
    assert int(ONE) not in range(-1000, 1000)
    assert f"{ONE:.2f}" == "<doble de Lictor>"
    assert repr(EMPTY) == "<doble vacío de Lictor>"
    assert type(ONE).__name__ == "ReturnedByLictor"


def test_a_double_has_no_special_names() -> None:
    assert not hasattr(ONE, "__dataclass_fields__")
    assert getattr(ONE, "__attrs_attrs__", None) is None


def test_a_double_copies_and_pickles_as_itself() -> None:
    assert copy.copy(ONE) is ONE
    assert copy.deepcopy([ONE]) == [ONE]
    rebuild, arguments = EMPTY.__reduce__()  # what pickle keeps of it
    assert rebuild(*arguments) is EMPTY
    assert type(copy.deepcopy(ONE).vence) is type(ONE)


def _follow(steps: int) -> None:
    node: Any = ONE
    for _ in range(steps):
        node = node.siguiente


def _loop() -> None:
    while ONE.pendiente:
        pass


def test_a_stub_returns_its_constant() -> None:
    def interfaz() -> object:
        return lictor_doubles.stub("ONE")

    assert interfaz() is ONE
    assert lictor_doubles.stub("EMPTY") is EMPTY
    assert lictor_doubles.stub("True") is True


_CLASS_STUB = """
class Registro:
    def __init__(self, nif: str) -> None:
        raise NotImplementedError

    def huella(self) -> str:
        raise NotImplementedError
"""


@pytest.mark.parametrize("constant", _PROFILE_IMPOSTORS["replacements"], ids=lambda c: c["name"])
def test_a_stub_in_an_init_returns_none_so_its_object_is_built(
    constant: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With `return True` there, building the object raised TypeError before any Then (F68)."""
    monkeypatch.setitem(sys.modules, "lictor_doubles", lictor_doubles)
    source, replaced = impostor_source(_CLASS_STUB, _PROFILE_IMPOSTORS["pattern"], constant["code"])
    assert replaced == 2
    module = tmp_path / "registros.py"  # the stubs as an impostor's clone leaves them
    module.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("registros", module)
    assert spec is not None
    assert spec.loader is not None
    registros = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registros)

    registro = registros.Registro("89890001K")

    expected = {"None": None, "True": True, "un doble de Lictor": ONE}
    assert registro.huella() is expected.get(constant["name"], EMPTY)


def test_an_unknown_constant_is_a_mistake_of_the_profile() -> None:
    with pytest.raises(KeyError):
        lictor_doubles.stub("ninguno")


def test_a_chain_of_attributes_ends() -> None:
    _follow(DEPTH)
    with pytest.raises(LictorDoubleExhaustedError, match="no tiene nada más dentro"):
        _follow(DEPTH + 1)


def test_a_loop_that_doesn_t_go_deeper_ends() -> None:
    with pytest.raises(LictorDoubleExhaustedError, match="demasiadas operaciones"):
        _loop()


def test_each_test_counts_its_operations_from_nothing() -> None:
    for _ in range(BUDGET - 1):
        bool(ONE)
    lictor_doubles.reset()
    for _ in range(BUDGET - 1):
        bool(ONE)


# The tables. Each row: a name, whether the check is correct or weak, the real result, and a Then
# that checks what the interface returned.


@dataclass
class Prestamo:
    isbn: str
    vence: date
    vencido: bool = False


P1 = Prestamo("111", date(2026, 12, 18))
P2 = Prestamo("222", date(2026, 12, 20))


def _no_assert(r: Any) -> bool:
    _ = r.vence  # lee y no compara
    return True


def _bucle(r: Any) -> bool:
    return all(p.isbn == "111" for p in r)


def _si(r: Any) -> bool:
    if r:
        return r.isbn == "111"
    return True


def _codigo_resta(r: Any) -> bool:
    # el código de src usa lo que devolvió un stub suyo: `disponibles = stock() - prestados`
    return (r - 1) == 2


def _desempaqueta(r: Any) -> bool:
    subtotal, iva = r
    return subtotal == 10 and iva == 2


def _primero_desempaquetado(r: Any) -> bool:
    (unico,) = r
    return unico == "ana"


@dataclass
class Nodo:
    valor: int
    siguiente: "Nodo | None" = None


def _recorre(r: Any) -> bool:
    valores = []
    while r:
        valores.append(r.valor)
        r = r.siguiente
    return valores == [1, 2]


def _hasta_el_final(r: Any) -> bool:
    while r.siguiente is not None:
        r = r.siguiente
    return r.valor == 2


# nombre, clase, valor real, comprobación
_MINE: list[tuple[str, str, Any, Callable[[Any], bool]]] = [
    ("número", "correcto", 5, lambda r: r == 5),
    ("Decimal", "correcto", Decimal("0.60"), lambda r: r == Decimal("0.60")),
    ("texto", "correcto", "ana", lambda r: r == "ana"),
    ("es 1 (APA-009)", "correcto", 1, lambda r: r == 1),
    ("es True", "correcto", True, lambda r: r is True),
    ("es False / not", "correcto", False, lambda r: not r),
    ("es None", "correcto", None, lambda r: r is None),
    ("fecha", "correcto", date(2026, 12, 18), lambda r: r == date(2026, 12, 18)),
    ("atributo fecha (REN-001)", "correcto", P1, lambda r: r.vence == date(2026, 12, 18)),
    ("atributo bool sí", "correcto", Prestamo("1", date.today(), True), lambda r: r.vencido),
    ("atributo bool no", "correcto", P1, lambda r: not r.vencido),
    ("atributo is False", "correcto", P1, lambda r: r.vencido is False),
    ("lista entera", "correcto", [P1, P2], lambda r: r == [P1, P2]),
    ("lista vacía ==", "correcto", [], lambda r: r == []),
    ("list() vacía (CON-004)", "correcto", [], lambda r: list(r) == []),
    (
        "isbn de cada uno (CON-003)",
        "correcto",
        [P1, P2],
        lambda r: [p.isbn for p in r] == ["111", "222"],
    ),
    ("isbn de cada uno, vacía", "correcto", [], lambda r: [p.isbn for p in r] == []),
    ("el primero (RES-001)", "correcto", ["ana"], lambda r: r[0] == "ana"),
    ("el primero, objeto", "correcto", [P1], lambda r: r[0].isbn == "111"),
    ("el último", "correcto", [P1, P2], lambda r: r[-1].isbn == "222"),
    ("rebanada", "correcto", ["ana"], lambda r: r[:1] == ["ana"]),
    ("cuántos, 2", "correcto", [P1, P2], lambda r: len(r) == 2),
    ("cuántos, 1", "correcto", [P1], lambda r: len(r) == 1),
    ("cuántos, 0", "correcto", [], lambda r: len(r) == 0),
    ("ordenada", "correcto", [5, 2], lambda r: sorted(r) == [2, 5]),
    ("conjunto", "correcto", [1, 2], lambda r: set(r) == {1, 2}),
    ("suma", "correcto", [5, 2], lambda r: sum(r) == 7),
    ("suma 0", "correcto", [], lambda r: sum(r) == 0),
    ("unida", "correcto", ["a", "b"], lambda r: ", ".join(r) == "a, b"),
    ("por clave", "correcto", {"ana": 3}, lambda r: r["ana"] == 3),
    ("get", "correcto", {"ana": 3}, lambda r: r.get("ana") == 3),
    ("dict entero", "correcto", {"ana": 3}, lambda r: r == {"ana": 3}),
    ("contiene", "correcto", ["111"], lambda r: "111" in r),
    ("no contiene", "correcto", ["222"], lambda r: "111" not in r),
    ("no contiene, isbn", "correcto", [P2], lambda r: "111" not in [p.isbn for p in r]),
    ("método del resultado", "correcto", {"a": 1}, lambda r: r.keys() == {"a": 1}.keys()),
    ("str()", "correcto", "ana", lambda r: str(r) == "ana"),
    ("desempaqueta 2", "correcto", (10, 2), _desempaqueta),
    ("desempaqueta 1", "correcto", ["ana"], _primero_desempaquetado),
    ("índices 0 y 1", "correcto", (10, 2), lambda r: r[0] == 10 and r[1] == 2),
    ("recorre una lista enlazada", "correcto", Nodo(1, Nodo(2)), _recorre),
    ("hasta el final de la lista", "correcto", Nodo(1, Nodo(2)), _hasta_el_final),
    ("el código usa el stub", "correcto", 3, _codigo_resta),
    # Débiles: no comparan con el valor del escenario.
    ("sin assert, lee .vence", "débil", P1, _no_assert),
    ("is not None", "débil", P1, lambda r: r is not None),
    ("isinstance", "débil", P1, lambda r: isinstance(r, Prestamo)),
    ("assert r", "débil", P1, bool),
    ("assert r.vence", "débil", P1, lambda r: bool(r.vence)),
    ("!= otro", "débil", 5, lambda r: r != 4),
    ("atributo != otro", "débil", P1, lambda r: r.isbn != "999"),
    ("len > 0", "débil", [P1], lambda r: len(r) > 0),
    ("bucle vacuo", "débil", [P1], _bucle),
    ("if r: assert", "débil", P1, _si),
    ("dos llamadas iguales", "débil", 5, lambda r: operator.eq(r, r)),
    ("no lee el resultado", "débil", 5, lambda r: True),
    ("type(r).__name__", "débil", P1, lambda r: type(r).__name__ == "Prestamo"),
    ("hasattr", "débil", P1, lambda r: hasattr(r, "vence")),
    ("el mismo atributo dos veces", "débil", P1, lambda r: r.vence == r.vence),
]


class Estado(Enum):
    PAGADO = 1


_TOTAL, _ENVIO = D("12.10"), D("2.90")


@dataclass
class Ped:
    total: D = _TOTAL
    envio: D = _ENVIO
    inicio: date = date(2026, 11, 2)
    vence: date = date(2026, 11, 16)
    cantidad: int = 1
    lineas: list = field(default_factory=lambda: ["111"])
    mensaje: str = "No hay ejemplares: vencido"
    error: object = None
    errores: list = field(default_factory=list)
    ok: bool = True
    estado: Estado = Estado.PAGADO
    fecha: str = "2026-11-16"
    rango: tuple = (1, 5)
    stock: dict = field(default_factory=lambda: {"111": 2})


P = Ped()
V1 = Prestamo("111", date(2026, 1, 1), True)


def _enumera(r):
    for i, p in enumerate(r):
        if p.isbn != ["111", "222"][i]:
            return False
    return len(r) == 2


def _rango(r):
    a, b = r.rango
    return (a, b) == (1, 5)


def _items(r):
    return {k: v for k, v in r.items()} == {"ana": 3}


def _traga(r):
    try:
        return r.total == D("12.10")
    except Exception:
        return True


def _valores_positivos(r):
    return all(v > 0 for _, v in r.items())


_FABLE: list[tuple[str, str, Any, Callable[[Any], bool]]] = [
    # correctas: comparan con el valor del escenario
    ("round(r, 2) directo", "correcto", 12.1, lambda r: round(r, 2) == 12.1),
    ("round(r.total, 2)", "correcto", P, lambda r: round(r.total, 2) == D("12.10")),
    ("resta de fechas .days", "correcto", P, lambda r: (r.vence - r.inicio).days == 14),
    (
        "vence == inicio + 14 días",
        "correcto",
        P,
        lambda r: r.vence == r.inicio + timedelta(days=14),
    ),
    ("formato :.2f", "correcto", P, lambda r: f"{r.total:.2f}" == "12.10"),
    ("suma de dos atributos", "correcto", P, lambda r: r.total + r.envio == D("15.00")),
    ("atributo * 2", "correcto", P, lambda r: r.total * 2 == D("24.20")),
    ("int(atributo)", "correcto", P, lambda r: int(r.cantidad) == 1),
    ("Decimal(atributo)", "correcto", P, lambda r: D(r.total) == D("12.10")),
    ("abs(r - x) < tol directo", "correcto", 12.1, lambda r: abs(r - 12.1) < 0.01),
    ("abs(atributo - x) < tol", "correcto", P, lambda r: abs(r.total - D("12.10")) < D("0.01")),
    ("math.isclose(float(atributo))", "correcto", P, lambda r: math.isclose(float(r.total), 12.1)),
    ("entre dos cotas", "correcto", P, lambda r: D("12.10") <= r.total <= D("12.10")),
    ("asdict()", "correcto", P, lambda r: dataclasses.asdict(r)["total"] == D("12.10")),
    ("dataclass entera", "correcto", P, lambda r: r == Ped()),
    ("json.dumps(lista del atributo)", "correcto", P, lambda r: json.dumps(r.lineas) == '["111"]'),
    (
        "re.search en un atributo",
        "correcto",
        P,
        lambda r: re.search("vencido$", r.mensaje) is not None,
    ),
    (
        "date.fromisoformat(atributo)",
        "correcto",
        P,
        lambda r: date.fromisoformat(r.fecha) == date(2026, 11, 16),
    ),
    ("strftime", "correcto", P, lambda r: r.vence.strftime("%d/%m") == "16/11"),
    ("isoformat", "correcto", P, lambda r: r.vence.isoformat() == "2026-11-16"),
    ("quantize", "correcto", P, lambda r: r.total.quantize(D("0.01")) == D("12.10")),
    ("texto contiene", "correcto", P, lambda r: "vencido" in r.mensaje),
    ("startswith", "correcto", P, lambda r: r.mensaje.startswith("No hay")),
    ("atributo is None", "correcto", P, lambda r: r.error is None),
    ("not r.errores", "correcto", P, lambda r: not r.errores),
    ("r.errores == []", "correcto", P, lambda r: r.errores == []),
    ("r.ok is True", "correcto", P, lambda r: r.ok is True),
    ("enum", "correcto", P, lambda r: r.estado == Estado.PAGADO),
    ("atributo in tupla", "correcto", P, lambda r: r.estado in (Estado.PAGADO,)),
    (
        "isinstance y atributo",
        "correcto",
        P1,
        lambda r: isinstance(r, Prestamo) and r.isbn == "111",
    ),
    ("any(isbn == …)", "correcto", [P1, P2], lambda r: any(p.isbn == "222" for p in r)),
    ("not any(vencido)", "correcto", [P1, P2], lambda r: not any(p.vencido for p in r)),
    (
        "max de fechas",
        "correcto",
        [P1, P2],
        lambda r: max(p.vence for p in r) == date(2026, 12, 20),
    ),
    ("suma de un campo", "correcto", [P, P], lambda r: sum(p.total for p in r) == D("24.20")),
    ("Counter", "correcto", ["a", "a"], lambda r: Counter(r) == Counter({"a": 2})),
    (
        "sorted con key",
        "correcto",
        [P2, P1],
        lambda r: [p.isbn for p in sorted(r, key=lambda p: p.isbn)] == ["111", "222"],
    ),
    ("enumerate + len", "correcto", [P1, P2], _enumera),
    ("desempaqueta un atributo", "correcto", P, _rango),
    ("items()", "correcto", {"ana": 3}, _items),
    ("values()", "correcto", {"ana": 3}, lambda r: list(r.values()) == [3]),
    ("dict anidado", "correcto", P, lambda r: r.stock["111"] == 2),
    ("len de un atributo", "correcto", P, lambda r: len(r.lineas) == 1),
    (
        "valor y cantidad con and",
        "correcto",
        P,
        lambda r: r.total == D("12.10") and r.cantidad == 1,
    ),
    ("split", "correcto", "a,b", lambda r: r.split(",") == ["a", "b"]),
    ("r in esperados", "correcto", "ana", lambda r: r in ["ana", "luis"]),
    (
        "zip con esperados",
        "correcto",
        [P1, P2],
        lambda r: all(p.isbn == e for p, e in zip(r, ["111", "222"], strict=True)),
    ),
    # débiles
    ("r in (None, True)", "débil", None, lambda r: r in (None, True)),
    ("r is None or r is True", "débil", None, lambda r: r is None or r is True),
    ("not callable(r)", "débil", None, lambda r: not callable(r)),
    ("not hasattr(r, 'error')", "débil", None, lambda r: not hasattr(r, "error")),
    ("isinstance(r, (bool, NoneType))", "débil", True, lambda r: isinstance(r, (bool, type(None)))),
    ("not isinstance(r, Exception)", "débil", None, lambda r: not isinstance(r, Exception)),
    ("r is not False", "débil", None, lambda r: r is not False),
    ("str(r) != ''", "débil", 5, lambda r: str(r) != ""),
    ("len(str(r)) < 10", "débil", None, lambda r: len(str(r)) < 10),
    ("atributo is not None", "débil", P, lambda r: r.total is not None),
    ("atributo > 0", "débil", P, lambda r: r.total > 0),
    ("isinstance(atributo, date)", "débil", P, lambda r: isinstance(r.vence, date)),
    ("all(vencido) quizá vacía", "débil", [V1], lambda r: all(p.vencido for p in r)),
    ("len >= 0", "débil", [P1], lambda r: len(r) >= 0),
    ("try/except que traga", "débil", P, _traga),
    ("valores > 0, quizá vacío", "débil", {"a": 1}, _valores_positivos),
    ("total == envio + resto (coherencia)", "débil", P, lambda r: r.total == r.envio + D("9.20")),
    (
        "dos atributos distintos iguales",
        "débil",
        Ped(total=D("2.90")),
        lambda r: r.total == r.envio,
    ),
]


# What C decides of each row: accepted, rejected, or sent to the impostor of the interface. The
# correct ones it rejects are its known limits: they never reach an assertion with any constant,
# since they convert what the interface returns, or pass when it is empty.
_OUTCOMES = {
    "número": "aceptado",  # correcto
    "Decimal": "aceptado",  # correcto
    "texto": "aceptado",  # correcto
    "es 1 (APA-009)": "aceptado",  # correcto
    "es True": "aceptado",  # correcto
    "es False / not": "aceptado",  # correcto
    "es None": "aceptado",  # correcto
    "fecha": "aceptado",  # correcto
    "atributo fecha (REN-001)": "aceptado",  # correcto
    "atributo bool sí": "aceptado",  # correcto
    "atributo bool no": "aceptado",  # correcto
    "atributo is False": "aceptado",  # correcto
    "lista entera": "aceptado",  # correcto
    "lista vacía ==": "aceptado",  # correcto
    "list() vacía (CON-004)": "aceptado",  # correcto
    "isbn de cada uno (CON-003)": "aceptado",  # correcto
    "isbn de cada uno, vacía": "aceptado",  # correcto
    "el primero (RES-001)": "aceptado",  # correcto
    "el primero, objeto": "aceptado",  # correcto
    "el último": "aceptado",  # correcto
    "rebanada": "aceptado",  # correcto
    "cuántos, 2": "aceptado",  # correcto
    "cuántos, 1": "aceptado",  # correcto
    "cuántos, 0": "aceptado",  # correcto
    "ordenada": "aceptado",  # correcto
    "conjunto": "aceptado",  # correcto
    "suma": "aceptado",  # correcto
    "suma 0": "aceptado",  # correcto
    "unida": "aceptado",  # correcto
    "por clave": "aceptado",  # correcto
    "get": "aceptado",  # correcto
    "dict entero": "aceptado",  # correcto
    "contiene": "aceptado",  # correcto
    "no contiene": "rechazado",  # correcto
    "no contiene, isbn": "rechazado",  # correcto
    "método del resultado": "aceptado",  # correcto
    "str()": "aceptado",  # correcto
    "desempaqueta 2": "rechazado",  # correcto
    "desempaqueta 1": "aceptado",  # correcto
    "índices 0 y 1": "aceptado",  # correcto
    "recorre una lista enlazada": "aceptado",  # correcto
    "hasta el final de la lista": "rechazado",  # correcto
    "el código usa el stub": "aceptado",  # correcto
    "sin assert, lee .vence": "rechazado",  # débil
    "is not None": "aceptado",  # débil
    "isinstance": "aceptado",  # débil
    "assert r": "aceptado",  # débil
    "assert r.vence": "aceptado",  # débil
    "!= otro": "a la interfaz",  # débil
    "atributo != otro": "rechazado",  # débil
    "len > 0": "aceptado",  # débil
    "bucle vacuo": "aceptado",  # débil
    "if r: assert": "aceptado",  # débil
    "dos llamadas iguales": "a la interfaz",  # débil
    "no lee el resultado": "a la interfaz",  # débil
    "type(r).__name__": "aceptado",  # débil
    "hasattr": "aceptado",  # débil
    "el mismo atributo dos veces": "rechazado",  # débil
    "round(r, 2) directo": "aceptado",  # correcto
    "round(r.total, 2)": "aceptado",  # correcto
    "resta de fechas .days": "aceptado",  # correcto
    "vence == inicio + 14 días": "aceptado",  # correcto
    "formato :.2f": "aceptado",  # correcto
    "suma de dos atributos": "aceptado",  # correcto
    "atributo * 2": "aceptado",  # correcto
    "int(atributo)": "aceptado",  # correcto
    "Decimal(atributo)": "rechazado",  # correcto
    "abs(r - x) < tol directo": "aceptado",  # correcto
    "abs(atributo - x) < tol": "aceptado",  # correcto
    "math.isclose(float(atributo))": "aceptado",  # correcto
    "entre dos cotas": "aceptado",  # correcto
    "asdict()": "rechazado",  # correcto
    "dataclass entera": "aceptado",  # correcto
    "json.dumps(lista del atributo)": "rechazado",  # correcto
    "re.search en un atributo": "rechazado",  # correcto
    "date.fromisoformat(atributo)": "rechazado",  # correcto
    "strftime": "aceptado",  # correcto
    "isoformat": "aceptado",  # correcto
    "quantize": "aceptado",  # correcto
    "texto contiene": "aceptado",  # correcto
    "startswith": "aceptado",  # correcto
    "atributo is None": "aceptado",  # correcto
    "not r.errores": "aceptado",  # correcto
    "r.errores == []": "aceptado",  # correcto
    "r.ok is True": "aceptado",  # correcto
    "enum": "aceptado",  # correcto
    "atributo in tupla": "aceptado",  # correcto
    "isinstance y atributo": "aceptado",  # correcto
    "any(isbn == …)": "aceptado",  # correcto
    "not any(vencido)": "aceptado",  # correcto
    "max de fechas": "aceptado",  # correcto
    "suma de un campo": "aceptado",  # correcto
    "Counter": "aceptado",  # correcto
    "sorted con key": "aceptado",  # correcto
    "enumerate + len": "aceptado",  # correcto
    "desempaqueta un atributo": "rechazado",  # correcto
    "items()": "aceptado",  # correcto
    "values()": "aceptado",  # correcto
    "dict anidado": "aceptado",  # correcto
    "len de un atributo": "aceptado",  # correcto
    "valor y cantidad con and": "aceptado",  # correcto
    "split": "aceptado",  # correcto
    "r in esperados": "aceptado",  # correcto
    "zip con esperados": "aceptado",  # correcto
    "r in (None, True)": "a la interfaz",  # débil
    "r is None or r is True": "a la interfaz",  # débil
    "not callable(r)": "a la interfaz",  # débil
    "not hasattr(r, 'error')": "a la interfaz",  # débil
    "isinstance(r, (bool, NoneType))": "a la interfaz",  # débil
    "not isinstance(r, Exception)": "a la interfaz",  # débil
    "r is not False": "a la interfaz",  # débil
    "str(r) != ''": "a la interfaz",  # débil
    "len(str(r)) < 10": "a la interfaz",  # débil
    "atributo is not None": "rechazado",  # débil
    "atributo > 0": "aceptado",  # débil
    "isinstance(atributo, date)": "aceptado",  # débil
    "all(vencido) quizá vacía": "rechazado",  # débil
    "len >= 0": "rechazado",  # débil
    "try/except que traga": "a la interfaz",  # débil
    "valores > 0, quizá vacío": "rechazado",  # débil
    "total == envio + resto (coherencia)": "aceptado",  # débil
    "dos atributos distintos iguales": "aceptado",  # débil
}
_ROWS = [*_MINE, *_FABLE]
_CONSTANTS = (
    StubConstant("", "None"),
    StubConstant("", "True"),
    StubConstant("", "ONE", double=True),
    StubConstant("", "EMPTY", double=True),
)
_VALUES = {"None": None, "True": True, "ONE": ONE, "EMPTY": EMPTY}
_WORDS = {"probado": "aceptado", "a_la_interfaz": "a la interfaz"}


def _ran(check: Callable[[Any], bool], value: Any) -> TestCaseResult:
    """The test's case as pytest and the profile's plugins would report it, in a Then."""
    lictor_doubles.reset()
    try:
        holds = check(value)
    except Exception:  # what a test that never reaches its assertion raises
        return TestCaseResult("t", status="failure", properties={"lictor.failure": "error"})
    if holds:
        return TestCaseResult("t")
    properties = {
        "lictor.failure": "assertion",
        "lictor.frame": "tests/acceptance/t.py",
        "lictor.step": "then",
    }
    return TestCaseResult("t", status="failure", properties=properties)


def _judged(check: Callable[[Any], bool]) -> str:
    cases = {name: _ran(check, value) for name, value in _VALUES.items()}
    outcome = stub_proof(cases, _CONSTANTS, ["tests/acceptance/"]).outcome
    return _WORDS.get(outcome, "rechazado")


def test_every_row_is_in_the_table_once() -> None:
    names = [row[0] for row in _ROWS]
    assert len(set(names)) == len(names)
    assert set(names) == set(_OUTCOMES)


@pytest.mark.parametrize(("name", "kind", "real", "check"), _ROWS, ids=[row[0] for row in _ROWS])
def test_what_c_decides_of_each_way_of_checking(
    name: str, kind: str, real: Any, check: Callable[[Any], bool]
) -> None:
    assert _ran(check, real).status == "passed", "el control tiene que pasar"
    found = _judged(check)
    assert found == _OUTCOMES[name]
    rejected_today = all(_ran(check, value).status == "passed" for value in (None, True))
    if kind == "débil" and rejected_today:
        assert found != "aceptado", "un débil que hoy se caza no puede pasar a aceptarse"


def test_the_tables_add_up() -> None:
    counted = Counter((kind, _OUTCOMES[name]) for name, kind, _, _ in _ROWS)
    assert counted == {
        ("correcto", "aceptado"): 79,
        ("correcto", "rechazado"): 10,
        ("débil", "aceptado"): 13,
        ("débil", "a la interfaz"): 13,
        ("débil", "rechazado"): 7,
    }
