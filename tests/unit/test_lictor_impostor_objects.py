"""The impostor of the interface with objects of value (ADR-0011, amendment of objects of value).

A dataclass, a `NamedTuple`, a pydantic model or an enumeration's member is altered as a copy of
its class with every field altered; what goes back to the interface goes back real, a mutable one
too while the driver hasn't changed it; and an exception that only an altered value raised proves
nothing. The table fixes what each way of checking an object proves, as `_CHECKS` does for
collections; the runs check the rest end to end, in real pytest processes.
"""

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from pydantic import BaseModel, ConfigDict

from lictor.adapters.config.profiles.python_uv.lictor_impostor import (
    _UNALTERED,
    LESS,
    MORE,
    _Impostor,
    alter,
)
from lictor.adapters.readers.junit import JUnitReader
from lictor.domain.interface_proof import interface_proofs
from lictor.domain.model import TestCaseResult
from lictor.domain.reports import JUnitReport
from tests.unit.test_lictor_impostor import THEN_CONFTEST, _pytest, _told


@dataclass(frozen=True)
class Linea:
    ref: str
    unidades: int


@dataclass(frozen=True)
class Pedido:
    total: int
    cliente: str
    lineas: tuple[Linea, ...] = ()


@dataclass
class Cesta:
    total: int
    refs: list[str] = field(default_factory=list)


class Punto(NamedTuple):
    x: int
    y: int


class Factura(BaseModel):
    model_config = ConfigDict(frozen=True)

    numero: str
    importe: int


class Estado(enum.Enum):
    ALTA = "alta"
    BAJA = "baja"
    PENDIENTE = "pendiente"


class Unico(enum.Enum):
    SOLO = 1


class Permiso(enum.Flag):
    LEER = 1
    ESCRIBIR = 2


class Nivel(enum.IntFlag):
    BAJO = 1
    ALTO = 2


class Servicio:
    """A class that isn't of value: its attributes are state, and it is left alone."""

    def __init__(self) -> None:
        self.total = 12


_PEDIDO = Pedido(12, "ana", (Linea("pan", 2),))

# Each way a Then checks an object of value, and what the impostor proves of it.
_OBJECT_CHECKS: list[tuple[str, Any, Callable[[Any], object], str]] = [
    (
        "igual a uno construido",
        _PEDIDO,
        lambda p: p == Pedido(12, "ana", (Linea("pan", 2),)),
        "probado",
    ),
    ("un campo", _PEDIDO, lambda p: p.total == 12, "probado"),
    ("un campo de texto", _PEDIDO, lambda p: p.cliente == "ana", "probado"),
    ("anidado", _PEDIDO, lambda p: p.lineas == (Linea("pan", 2),), "probado"),
    ("mutable", Cesta(5, ["pan"]), lambda c: c.total == 5, "probado"),
    ("namedtuple", Punto(1, 2), lambda p: p == Punto(1, 2), "probado"),
    ("pydantic", Factura(numero="F-1", importe=10), lambda f: f.importe == 10, "probado"),
    ("enum", Estado.ALTA, lambda e: e is Estado.ALTA, "probado"),
    ("enum por valor", Estado.BAJA, lambda e: e.value == "baja", "probado"),
    ("lista de objetos", [Linea("pan", 2)], lambda xs: xs == [Linea("pan", 2)], "probado"),
    ("IntFlag, como el entero que es", Nivel.ALTO, lambda n: n == Nivel.ALTO, "probado"),
    # Weak: they don't compare with the scenario's value.
    ("no es None", _PEDIDO, lambda p: p is not None, "sobrevive"),
    ("de su clase", _PEDIDO, lambda p: isinstance(p, Pedido), "sobrevive"),
    ("tiene el campo", _PEDIDO, lambda p: hasattr(p, "total"), "sobrevive"),
    ("un campo positivo", _PEDIDO, lambda p: p.total > 0, "sobrevive"),
    ("un campo verdadero", _PEDIDO, lambda p: bool(p.cliente), "sobrevive"),
    ("igual a sí mismo", _PEDIDO, lambda p: (q := p) == q, "sobrevive"),
    # A known limit of collections, inside an object: the first of a list of one.
    ("el primero de uno, dentro", _PEDIDO, lambda p: p.lineas[0].unidades == 2, "no_aserta"),
]


def _judged(value: Any, check: Callable[[Any], object]) -> str:
    results = []
    for mode in (MORE, LESS):
        try:
            results.append("aserción" if not check(alter(value, mode)) else "pasa")
        except Exception:
            results.append("error")
    if "pasa" in results:
        return "sobrevive"
    return "no_aserta" if "error" in results else "probado"


@pytest.mark.parametrize(
    ("value", "check", "outcome"),
    [row[1:] for row in _OBJECT_CHECKS],
    ids=[row[0] for row in _OBJECT_CHECKS],
)
def test_what_the_impostor_proves_of_each_way_of_checking_an_object(
    value: Any, check: Callable[[Any], object], outcome: str
) -> None:
    assert check(value), "el control tiene que pasar"
    assert _judged(value, check) == outcome


@pytest.mark.parametrize(
    "value",
    [Unico.SOLO, Permiso.LEER | Permiso.ESCRIBIR, Servicio(), Pedido],
    ids=["un solo miembro", "Flag combinado", "un servicio", "la clase"],
)
def test_what_isn_t_an_object_of_value_with_another_value_stays(value: Any) -> None:
    assert alter(value, MORE) is _UNALTERED


def test_an_altered_object_is_a_copy_of_its_class_built_without_its_code() -> None:
    altered = alter(_PEDIDO, MORE)

    assert type(altered) is Pedido
    assert altered == Pedido(13, "ana·", (Linea("pan·", 3), Linea("pan", 2)))
    assert Pedido(12, "ana", (Linea("pan", 2),)) == _PEDIDO  # the real one is untouched


def test_an_enumeration_gives_the_next_member_or_the_one_before() -> None:
    assert (alter(Estado.ALTA, MORE), alter(Estado.ALTA, LESS)) == (Estado.BAJA, Estado.PENDIENTE)


def _impostor() -> _Impostor:
    return _Impostor("mas", [], [], ["then"], {})


def test_what_goes_back_to_the_interface_goes_back_real() -> None:
    impostor = _impostor()
    frozen = impostor._altered(lambda: _PEDIDO, (), {}, "then", 0)
    cesta = Cesta(5, ["pan"])
    mutable = impostor._altered(lambda: cesta, (), {}, "then", 1)

    assert impostor.real(frozen) is _PEDIDO
    assert impostor.real(frozen.lineas) is _PEDIDO.lineas  # a field it altered, too
    assert impostor.real(mutable) is cesta  # untouched by the driver: what the interface gave


def test_a_mutable_object_the_driver_changed_is_its_own() -> None:
    impostor = _impostor()
    cesta = Cesta(5, ["pan"])
    mutable = impostor._altered(lambda: cesta, (), {}, "then", 0)

    mutable.refs = ["leche"]

    assert impostor.real(mutable) is mutable


# End to end: an exception only an altered value raised, and what goes back real.
CODE = """
from dataclasses import dataclass


@dataclass
class Pedido:
    id: int
    total: int


PEDIDOS = {1000: 12}


def crear():
    return Pedido(1000, 12)


def facturar(pedido):
    return f"F-{pedido.id}-{PEDIDOS[pedido.id]}"


def nuevo_id():
    return 7


def buscar(n):
    return {7: "ana"}[n]
"""

DRIVER = """
from pedidos import Pedido, buscar, crear, facturar, nuevo_id


def test_fuerte():
    assert crear() == Pedido(1000, 12)


def test_mutable_que_vuelve():
    assert facturar(crear()) is not None


def test_id_que_vuelve():
    assert buscar(nuevo_id()) is not None


def test_factura():
    assert facturar(crear()) == "F-1000-12"
"""

EXPECTED = {
    "test_fuerte": "probado",
    # The altered order goes back real: facturar runs on what crear gave, and the weak check
    # survives, instead of being proved by the KeyError the impostor would swallow.
    "test_mutable_que_vuelve": "sobrevive",
    # An integer Python shares can't go back real: buscar raises only for the altered one.
    "test_id_que_vuelve": "provocada",
    "test_factura": "probado",
}


def _run(project: Path, mode: str) -> dict[str, TestCaseResult]:
    report = project / f"out-{mode}.xml"
    _pytest(project, {**_told(project, mode), "trace": True, "root": str(project)}, report)
    result = JUnitReader().read(report)
    assert isinstance(result, JUnitReport), result
    return {case.name: case for case in result.cases}


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, TestCaseResult]]:
    project = tmp_path_factory.mktemp("objetos")
    (project / "src" / "pedidos").mkdir(parents=True)
    (project / "src" / "pedidos" / "__init__.py").write_text(CODE, encoding="utf-8")
    (project / "tests" / "acceptance").mkdir(parents=True)
    (project / "tests" / "acceptance" / "test_calc.py").write_text(DRIVER, encoding="utf-8")
    (project / "tests" / "acceptance" / "conftest.py").write_text(THEN_CONFTEST, encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    return {mode: _run(project, mode) for mode in ("control", MORE, LESS)}


@pytest.mark.parametrize(("name", "outcome"), sorted(EXPECTED.items()))
def test_what_the_rule_makes_of_objects_that_go_back(
    runs: dict[str, dict[str, TestCaseResult]], name: str, outcome: str
) -> None:
    proofs = interface_proofs(
        [name], runs["control"], {m: runs[m] for m in (MORE, LESS)}, ("tests/acceptance/",)
    )

    assert proofs[name].outcome == outcome


def test_the_control_numbers_the_calls_and_says_which_raised(
    runs: dict[str, dict[str, TestCaseResult]],
) -> None:
    provoked = runs[MORE]["test_id_que_vuelve"]

    assert runs["control"]["test_id_que_vuelve"].interface_raised_at == frozenset()
    assert provoked.interface_swallowed_at == ((1, "then"),)


def test_the_trace_names_each_call_by_step_function_and_arguments(
    runs: dict[str, dict[str, TestCaseResult]],
) -> None:
    trace = runs["control"]["test_factura"].interface_trace

    assert len(trace) == 2  # crear() and facturar(p)
    first, second = (entry.split(":") for entry in trace)
    assert first[0] == second[0] == "0"  # the only step of the test, from 0
    assert first[2] == ""  # crear takes nothing
    assert len(second[2]) == 8  # facturar, one argument


def test_what_goes_back_real_is_traced_as_the_real_one(
    runs: dict[str, dict[str, TestCaseResult]],
) -> None:
    """An object of value hashes by its fields, whatever its address: the altered order that goes
    back real to facturar is the control's argument."""
    assert runs[MORE]["test_factura"].interface_trace == (
        runs["control"]["test_factura"].interface_trace
    )
