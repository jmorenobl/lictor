"""The profile's impostor of the interface, run in real pytest processes as verification would.

Each test of the fake driver is one shape a test of a scenario takes, run as the only Then of its
scenario (`THEN_CONFTEST` tells the plugins what the runner of the Gherkin would); the domain's rule
then judges what the plugin reported, so the table of ADR-0011 is checked end to end, without
Lictor's graph.
"""

import json
import math
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from defusedxml import ElementTree

from lictor.adapters.config.loader import DEFAULT_PROFILES_DIR
from lictor.adapters.config.profiles.python_uv import lictor_impostor
from lictor.adapters.config.profiles.python_uv.lictor_impostor import (
    LESS,
    MORE,
    SENTINEL,
    alter,
)
from lictor.adapters.readers.junit import JUnitReader
from lictor.domain.interface_proof import ALTERED_STEPS, interface_proofs
from lictor.domain.model import StepCounts, TestCaseResult
from lictor.domain.reports import JUnitReport

CODE = """
from datetime import date


class ErrorDeCalculo(Exception):
    pass


def tokenizar(texto):
    return texto.split()


def evaluar(texto):
    if texto == "1/0":
        raise ErrorDeCalculo("división por cero")
    partes = tokenizar(texto)
    return float(sum(float(p) for p in partes if p != "+"))


def es_par(n):
    return n % 2 == 0


def nombre():
    return "calc"


def lista():
    return [1, 2]


def nada():
    return None


def contar():
    yield 1


def vence():
    return date(2026, 12, 18)


def sustituido():
    return 5


class Resultado:
    def __init__(self, valor):
        self.valor = valor


def resultado():
    return Resultado(4)


class Raro(int):
    def __add__(self, other):
        raise RuntimeError("no se suma")


def raro():
    return Raro(3)


class Calculadora:
    def suma(self, a, b):
        return a + b

    @property
    def marca(self):
        return "x"

    @staticmethod
    def doble(x):
        return 2 * x

    @classmethod
    def nueva(cls):
        return cls()

    def __eq__(self, other):
        return isinstance(other, Calculadora)

    __hash__ = object.__hash__
"""

DRIVER = """
import pytest

from calc import (
    Calculadora,
    ErrorDeCalculo,
    contar,
    es_par,
    evaluar,
    lista,
    nada,
    nombre,
    resultado,
    sustituido,
    vence,
)


def test_igual():
    assert evaluar("2 + 4") == 6


def test_espera_uno():
    assert evaluar("1") == 1


def test_espera_true():
    assert es_par(2) is True


def test_vacuo():
    evaluar("2 + 4")


def test_atributo_suelto():
    evaluar("2 + 4").real


def test_debil():
    assert evaluar("2 + 4")


def test_no_none():
    assert evaluar("2 + 4") is not None


def test_isinstance():
    assert isinstance(evaluar("2 + 4"), float)


def test_distinto():
    assert evaluar("2 + 4") != 5


def test_menor():
    assert evaluar("2 + 4") < 7


def test_dos_llamadas():
    assert evaluar("1 + 1") == evaluar("2")


def test_raises():
    with pytest.raises(ErrorDeCalculo):
        evaluar("1/0")


def test_raises_cualquiera():
    with pytest.raises(Exception):
        evaluar("1/0")


def test_objeto():
    assert resultado().valor == 4


def test_metodo():
    assert Calculadora().suma(2, 3) == 5


def test_propiedad():
    assert Calculadora().marca == "x"


def test_estatico():
    assert Calculadora.doble(2) == 4


def test_de_clase():
    assert Calculadora.nueva() == Calculadora()


def test_lista():
    assert lista() == [1, 2]


def test_texto():
    assert nombre() == "calc"


def test_nada():
    assert nada() is None


def test_generador():
    assert list(contar()) == [1]


def test_sin_llamar():
    assert 6 == 6


def test_fecha():
    import datetime

    assert vence() == datetime.date(2026, 12, 18)


def test_stub_sustituido():
    assert sustituido() == 5
"""

# What pytest-bdd does around a step, for each test of test_calc.py: one Then, the whole test.
THEN_CONFTEST = """
from types import SimpleNamespace

import lictor_impostor
import lictor_outcome
import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if item.module.__name__ != "test_calc":
        yield
        return
    step = SimpleNamespace(type="then")
    lictor_impostor.pytest_bdd_before_step(step)
    outcome = yield
    if outcome.excinfo is None:
        lictor_impostor.pytest_bdd_after_step()
    else:
        lictor_outcome.pytest_bdd_step_error(
            request=item._request, step=step, step_func=item.obj, exception=outcome.excinfo[1]
        )
        lictor_impostor.pytest_bdd_step_error()
"""

# The runner of the Gherkin tells the plugin which step is in progress; here the test does.
STEPS_DRIVER = """
from types import SimpleNamespace

import pytest

import lictor_impostor as plugin

from calc import evaluar, nada, raro, resultado


@pytest.fixture
def preparado():
    return evaluar("3")


def paso(kind):
    plugin.pytest_bdd_before_step(SimpleNamespace(type=kind))


def test_un_fixture_pedido_en_un_cuando(request):
    paso("when")
    tres = request.getfixturevalue("preparado")
    plugin.pytest_bdd_after_step()
    paso("then")
    assert tres == 3


def test_por_pasos():
    paso("given")
    nada()
    resultado()
    plugin.pytest_bdd_after_step()
    evaluar("1")
    paso("when")
    total = evaluar("2 + 4")
    plugin.pytest_bdd_step_error()
    paso("then")
    assert total == 6
    assert evaluar("1") == 1


def test_raro():
    paso("then")
    assert raro() == 3
"""

EXPECTED = {
    "test_igual": "probado",
    "test_espera_uno": "probado",
    "test_espera_true": "probado",
    "test_raises": "probado",
    "test_metodo": "probado",
    "test_propiedad": "probado",
    "test_estatico": "probado",
    "test_lista": "probado",
    "test_texto": "probado",
    "test_nada": "probado",
    "test_vacuo": "sobrevive",
    "test_atributo_suelto": "sobrevive",
    "test_debil": "sobrevive",
    "test_no_none": "sobrevive",
    "test_isinstance": "sobrevive",
    "test_distinto": "sobrevive",
    "test_menor": "sobrevive",
    "test_dos_llamadas": "sobrevive",
    "test_raises_cualquiera": "sobrevive",
    "test_objeto": "sin_alterar",
    "test_de_clase": "sin_alterar",
    "test_generador": "no_llama",
    "test_sin_llamar": "no_llama",
    "test_fecha": "probado",
    "test_stub_sustituido": "probado",
}


def _told(project: Path, mode: str) -> dict[str, Any]:
    """What Lictor tells the plugin for one run, as `CheckRed` does."""
    return {
        "mode": mode,
        "writable": [str(project / "src")],
        "driver": [str(project / "tests" / "acceptance")],
        "steps": list(ALTERED_STEPS),
        "stubs": {},
    }


def _pytest(
    project: Path, told: dict[str, Any] | None, report: Path
) -> subprocess.CompletedProcess[bytes]:
    plugin_dir = DEFAULT_PROFILES_DIR / "python_uv"
    # A library of the test's, outside the project: where a runner of commands would be.
    library = [str(project / "lib")] if (project / "lib").is_dir() else []
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "LICTOR"))},
        "PYTHONPATH": os.pathsep.join([str(plugin_dir), str(project / "src"), *library]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    if told is not None:
        env["LICTOR_IMPOSTOR"] = json.dumps(told)
    command = [sys.executable, "-B", "-m", "pytest", "-p", "lictor_outcome", "-p"]
    command += ["lictor_impostor", "-p", "no:cacheprovider", "-q", "tests", "--junitxml"]
    return subprocess.run(
        [*command, str(report)],
        cwd=project,
        env=env,
        capture_output=True,
        check=False,
        timeout=120,
    )


def _run(project: Path, mode: str | None) -> dict[str, TestCaseResult]:
    report = project / f"out-{mode}.xml"
    _pytest(project, None if mode is None else _told(project, mode), report)
    result = JUnitReader().read(report)
    assert isinstance(result, JUnitReport), result
    return {case.name: case for case in result.cases}


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str | None, dict[str, TestCaseResult]]:
    project = tmp_path_factory.mktemp("proyecto")
    (project / "src" / "calc").mkdir(parents=True)
    (project / "src" / "calc" / "__init__.py").write_text(CODE, encoding="utf-8")
    (project / "tests" / "acceptance").mkdir(parents=True)
    (project / "tests" / "acceptance" / "test_calc.py").write_text(DRIVER, encoding="utf-8")
    (project / "tests" / "acceptance" / "test_pasos.py").write_text(STEPS_DRIVER, encoding="utf-8")
    (project / "tests" / "acceptance" / "conftest.py").write_text(THEN_CONFTEST, encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    runs = {mode: _run(project, mode) for mode in (None, "control", MORE, LESS)}
    runs["proyecto"] = {"": TestCaseResult(str(project))}  # where it is, for a run of its own
    return runs


def test_every_test_passes_with_the_real_code(
    runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    for mode in (None, "control"):
        assert {name: case.status for name, case in runs[mode].items()} == dict.fromkeys(
            [*EXPECTED, "test_por_pasos", "test_raro", "test_un_fixture_pedido_en_un_cuando"],
            "passed",
        )


def test_without_its_variable_the_impostor_reports_nothing(
    runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    assert all(not case.properties for case in runs[None].values())


@pytest.mark.parametrize(("name", "outcome"), sorted(EXPECTED.items()))
def test_each_shape_of_test_gets_the_verdict_of_the_adr(
    runs: dict[str | None, dict[str, TestCaseResult]], name: str, outcome: str
) -> None:
    proofs = interface_proofs(
        list(EXPECTED),
        runs["control"],
        {MORE: runs[MORE], LESS: runs[LESS]},
        ["tests/acceptance/"],
    )

    assert proofs[name].outcome == outcome, (name, proofs[name])


def test_the_control_counts_by_step_and_alters_nothing(
    runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    case = runs["control"]["test_por_pasos"]

    assert case.interface_calls == 5
    assert case.interface_by_step("calls") == StepCounts(given=2, when=1, then=1, outside=1)
    assert case.interface_by_step("altered") == StepCounts()


@pytest.mark.parametrize("mode", [MORE, LESS])
def test_each_mode_alters_only_what_the_when_and_the_then_get(
    runs: dict[str | None, dict[str, TestCaseResult]], mode: str
) -> None:
    """The Given's None and the call outside a step stay real; the altered total fails the Then's
    first assertion, so the Then calls nothing."""
    case = runs[mode]["test_por_pasos"]

    assert case.status == "failure"
    assert case.interface_by_step("calls") == StepCounts(given=2, when=1, then=0, outside=1)
    assert case.interface_by_step("altered") == StepCounts(when=1)
    assert case.interface_altered_none == 0


@pytest.mark.parametrize("mode", [MORE, LESS])
def test_a_fixture_a_when_requests_prepares_as_a_given_does(
    runs: dict[str | None, dict[str, TestCaseResult]], mode: str
) -> None:
    """pytest-bdd resolves a step's fixtures once the step is in progress: they stay real."""
    case = runs[mode]["test_un_fixture_pedido_en_un_cuando"]

    assert case.status == "passed"
    assert case.interface_by_step("calls") == StepCounts(outside=1)
    assert case.interface_altered == 0


@pytest.mark.parametrize("mode", [MORE, LESS])
def test_an_altered_none_is_counted_apart(
    runs: dict[str | None, dict[str, TestCaseResult]], mode: str
) -> None:
    case = runs[mode]["test_nada"]

    assert (case.interface_altered, case.interface_altered_none) == (1, 1)
    assert case.failed_step == "then"


def test_the_real_result_reaches_a_test_in_the_control(
    runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    """`raro` returns a value whose own arithmetic raises: the control passes as the red did."""
    case = runs["control"]["test_raro"]

    assert case.status == "passed"
    assert case.interface_by_step("calls") == StepCounts(then=1)


def test_a_function_whose_stub_lictor_replaced_is_left_unwrapped(
    runs: dict[str | None, dict[str, TestCaseResult]], tmp_path: Path
) -> None:
    """What a replaced stub returns is Lictor's constant: altering it would prove nothing."""
    project = Path(runs["proyecto"][""].name)
    source = project / "src" / "calc" / "__init__.py"
    lines = source.read_text(encoding="utf-8").splitlines()
    stub = lines.index("def sustituido():") + 2  # its body, counted from 1
    results = {}
    for mode in ("control", MORE, LESS):
        told = {**_told(project, mode), "stubs": {str(source): [stub]}}
        report = tmp_path / f"{mode}.xml"
        _pytest(project, told, report)
        read = JUnitReader().read(report)
        assert isinstance(read, JUnitReport), read
        results[mode] = {case.name: case for case in read.cases}

    assert results["control"]["test_stub_sustituido"].interface_calls == 0
    assert results[MORE]["test_stub_sustituido"].status == "passed"
    assert results[MORE]["test_igual"].status == "failure"  # the rest is still wrapped
    proofs = interface_proofs(
        ["test_stub_sustituido"],
        results["control"],
        {MORE: results[MORE], LESS: results[LESS]},
        ["tests/acceptance/"],
    )
    assert proofs["test_stub_sustituido"].outcome == "no_llama"


@pytest.mark.parametrize("key", ["steps", "stubs"])
def test_without_the_steps_to_alter_or_the_stubs_the_impostor_runs_no_test(
    runs: dict[str | None, dict[str, TestCaseResult]], tmp_path: Path, key: str
) -> None:
    """What Lictor doesn't say isn't guessed: the run tells nothing, and proves none."""
    project = Path(runs["proyecto"][""].name)
    told = _told(project, MORE)
    del told[key]

    done = _pytest(project, told, tmp_path / "out.xml")

    assert done.returncode != 0
    assert f"KeyError: '{key}'".encode() in done.stderr + done.stdout
    assert not (tmp_path / "out.xml").exists()


def test_a_call_inside_the_code_crosses_no_boundary(
    runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    """`evaluar` calls `tokenizar`: only the driver's call counts, and only its result changes."""
    assert runs["control"]["test_igual"].interface_calls == 1
    assert runs["control"]["test_igual"].interface_altered == 0
    assert runs[MORE]["test_igual"].interface_altered == 1


def test_the_impostor_s_error_is_classified_by_its_class(
    runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    assert runs[LESS]["test_raises"].failure_kind == "impostor"
    assert runs[MORE]["test_raises"].failure_kind == "assertion"
    assert runs[MORE]["test_raises"].frame == "tests/acceptance/test_calc.py"


@pytest.mark.parametrize(
    ("value", "more", "less"),
    [
        (6, 7, 5),
        (6.0, 7.0, 5.0),
        (Decimal("1.5"), Decimal("2.5"), Decimal("0.5")),
        (Fraction(1, 3), Fraction(4, 3), Fraction(-2, 3)),
        # A date or a datetime, a day more or less (ADR-0012, part of F62).
        (date(2026, 12, 18), date(2026, 12, 19), date(2026, 12, 17)),
        (datetime(2026, 1, 1, 9), datetime(2026, 1, 2, 9), datetime(2025, 12, 31, 9)),
        ([date(2026, 1, 1)], [date(2026, 1, 2), date(2026, 1, 1)], []),
        (True, False, False),
        (False, True, True),
        ("ab", "ab·", "a"),
        ("", "·", "·"),
        (b"ab", b"ab\0", b"a"),
        # A sequence changes at its start, growing with an element of its own form.
        ([1, 2], [3, 1, 2], [2]),
        (["a", "b"], ["b·", "a", "b"], ["b"]),
        ([[1], [2]], [[3, 2], [1], [2]], [[2]]),
        ([None], [SENTINEL, None], []),
        ((1,), (2, 1), ()),
        ([], [SENTINEL], [SENTINEL]),
        ({"a": 1}, {"a": 1, SENTINEL: 2}, {}),
        ({}, {SENTINEL: SENTINEL}, {SENTINEL: SENTINEL}),
        (frozenset({1}), frozenset({1, SENTINEL}), frozenset()),
        (set(), {SENTINEL}, {SENTINEL}),
        (None, SENTINEL, SENTINEL),
    ],
)
def test_each_known_kind_is_altered_both_ways(value: Any, more: Any, less: Any) -> None:
    assert alter(value, MORE) == more
    assert alter(value, LESS) == less
    assert alter(value, MORE) != value


@pytest.mark.parametrize(
    "value", [math.inf, -math.inf, math.nan, 1e20, object(), 1 + 2j, range(3), {1: 2}.keys()]
)
def test_what_can_t_be_altered_is_left_as_it_is(value: Any) -> None:
    assert alter(value, MORE) is lictor_impostor._UNALTERED
    assert alter(value, LESS) is lictor_impostor._UNALTERED


def test_a_sequence_of_objects_grows_with_one_of_them_not_with_the_sentinel() -> None:
    """Of an unknown kind, the element is the last one itself: it has every attribute it has."""
    first, last = object(), object()

    assert alter([first, last], MORE) == [last, first, last]
    assert alter([first, last], LESS) == [last]
    assert alter({"k": last}, MORE) == {"k": last, SENTINEL: last}


class _Loan:
    """What a library returns in a list: an object the impostor doesn't know how to alter."""

    def __init__(self, isbn: str) -> None:
        self.isbn = isbn


def _strong(value: Any) -> Callable[[Any], object]:
    return lambda got: got == value


# Tests a driver writes over what the interface returned, and what the impostor proves of each:
# the correct ones are proved, the weak ones survive, and what neither mode can tell apart is
# rejected as before. A row changes only with a deliberate change of `alter` (ADR-0011, its
# amendment for collections).
_TWO = [_Loan("222"), _Loan("111")]
_CHECKS: list[tuple[str, Any, Callable[[Any], object], str]] = [
    ("isbn de cada uno", _TWO, lambda x: [p.isbn for p in x] == ["222", "111"], "probado"),
    ("el primero", _TWO, lambda x: x[0].isbn == "222", "probado"),
    ("el segundo de tres", [*_TWO, _Loan("333")], lambda x: x[1].isbn == "111", "probado"),
    ("cuántos", _TWO, lambda x: len(x) == 2, "probado"),
    ("lista vacía", [], _strong([]), "probado"),
    ("lista de primitivos", [5, 2], _strong([5, 2]), "probado"),
    ("ordenada", [5, 2], lambda x: sorted(x) == [2, 5], "probado"),
    ("como conjunto", [1, 2], lambda x: set(x) == {1, 2}, "probado"),
    ("suma", [5, 2, 0], lambda x: sum(x) == 7, "probado"),
    ("unida", ["a", "b"], lambda x: ", ".join(x) == "a, b", "probado"),
    ("isbn unidos", _TWO, lambda x: ", ".join(p.isbn for p in x) == "222, 111", "probado"),
    ("desempaquetada", (10, 2), lambda x: x[0] == 10 and x[1] == 2, "probado"),
    (
        "valores de un dict",
        {"a": _TWO[0]},
        lambda d: [v.isbn for v in d.values()] == ["222"],
        "probado",
    ),
    ("dict entero", {"ana": 3}, _strong({"ana": 3}), "probado"),
    # Weak: they don't compare with the scenario's value.
    ("no vacía", _TWO, bool, "sobrevive"),
    ("al menos uno", _TWO, lambda x: len(x) > 0, "sobrevive"),
    ("de su clase", _TWO, lambda x: all(isinstance(p, _Loan) for p in x), "sobrevive"),
    ("contiene", [1, 2], lambda x: 2 in x, "sobrevive"),
    ("el primero existe", _TWO, lambda x: x[0] is not None, "sobrevive"),
    # Known limits: what changing the start of a sequence can't tell apart.
    ("el último", [1, 2], lambda x: x[-1] == 2, "sobrevive"),
    ("por clave", {"ana": 3, "luis": 1}, lambda d: d["ana"] == 3, "sobrevive"),
    ("el único, por índice", ["ana"], lambda x: x[0] == "ana", "no_aserta"),
    ("de cada uno, vacía", [], lambda x: [p.isbn for p in x] == [], "no_aserta"),
    # Texts (F85): `menos` drops the last character and, if it was blank, the last that isn't.
    ("un texto entero", "S", _strong("S"), "probado"),
    ("un texto con salto, entero", "S\n", _strong("S\n"), "probado"),
    ("sin los blancos", "S\n", lambda x: x.strip() == "S", "probado"),
    ("sin el salto", "S\n", lambda x: x.rstrip("\n") == "S", "probado"),
    ("sus líneas", "S\nT\n", lambda x: x.splitlines() == ["S", "T"], "probado"),
    ("sus palabras", "S T\n", lambda x: x.split() == ["S", "T"], "probado"),
    ("su última línea", "S\nT\n", lambda x: x.splitlines()[-1] == "T", "probado"),
    ("acaba en salto", "S\n", lambda x: x.endswith("\n"), "probado"),
    ("su largo", "S\n", lambda x: len(x) == 2, "probado"),
    ("unos bytes con salto", b"S\n", lambda x: x.strip() == b"S", "probado"),
    # What reads it as something else can't read it altered: the rule weighs where (F85).
    ("como número", "12\n", lambda x: int(x) == 12, "no_aserta"),
    ("como JSON", '{"a": 1}\n', lambda x: json.loads(x)["a"] == 1, "no_aserta"),
    (
        "como XML",
        "<r><a>S</a></r>\n",
        lambda x: ElementTree.fromstring(x).findtext("a") == "S",
        "no_aserta",
    ),
    # Weak, or what only changing its end can't tell: they survive.
    ("contiene el valor", "S\n", lambda x: "S" in x, "sobrevive"),
    ("empieza por él", "S\n", lambda x: x.startswith("S"), "sobrevive"),
    ("no vacío", "S\n", bool, "sobrevive"),
    ("su primera línea", "S\nT\n", lambda x: x.splitlines()[0] == "S", "sobrevive"),
]


def _judged(value: Any, check: Callable[[Any], object]) -> str:
    """What the proof says of a test that asserts `check` on what the interface returned."""
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
    ("value", "check", "outcome"), [row[1:] for row in _CHECKS], ids=[row[0] for row in _CHECKS]
)
def test_what_the_impostor_proves_of_each_way_of_checking_a_result(
    value: Any, check: Callable[[Any], object], outcome: str
) -> None:
    assert check(value), "el control tiene que pasar"
    assert _judged(value, check) == outcome


def test_the_sentinel_is_none_of_the_values_a_test_expects() -> None:
    assert SENTINEL not in (None, 0, "", False, [], ())
    assert repr(SENTINEL) == "<impostor de Lictor>"
    assert type(SENTINEL).__name__ == "AlteredByLictor"


# Coroutines and libraries (ADR-0011, amendment of coroutines and libraries): an API that is
# `async`, a command a runner invokes, and a value the driver hands back to the interface.
ASYNC_CODE = """
import asyncio
from dataclasses import dataclass


class ErrorDeTienda(Exception):
    pass


REGISTRADAS = []


def orden(funcion):
    REGISTRADAS.append(funcion)
    return funcion


async def precio(ref):
    await asyncio.sleep(0)
    return {"pan": 2, "leche": 3}[ref]


async def agotado(ref):
    raise ErrorDeTienda(ref)


async def total(refs):
    return sum([await precio(ref) for ref in refs])


async def en_tareas(refs):
    tareas = [asyncio.create_task(precio(ref)) for ref in refs]
    return sum([await tarea for tarea in tareas])


_GUARDADO = []


def guardar(cosa):
    _GUARDADO.append(cosa)


def guardadas():
    return len(_GUARDADO)


@orden
def comprar():
    guardar("pan")


def clave():
    return "pedido-7"


def estado(de):
    return {"pedido-7": "servido"}[de]


def cesta():
    return ["leche"]


_CESTA = []


def guardar_cesta(cosas):
    _CESTA[:] = cosas


def en_la_cesta():
    return list(_CESTA)


def validar(nif):
    return nif.upper()


@dataclass
class Nif:
    valor: str

    def __post_init__(self):
        validar(self.valor)
"""

# A runner of commands, as a library of the test's: it calls what the project registered.
RUNNER = """
def ejecutar(registradas):
    for funcion in registradas:
        funcion()
    return 0
"""

ASYNC_DRIVER = """
import asyncio
import inspect

import pytest
import runner

from tienda import (
    REGISTRADAS,
    ErrorDeTienda,
    Nif,
    agotado,
    cesta,
    clave,
    en_la_cesta,
    en_tareas,
    estado,
    guardadas,
    guardar_cesta,
    precio,
    total,
)


def test_corrutina():
    assert asyncio.run(precio("pan")) == 2


def test_corrutina_debil():
    assert asyncio.run(precio("pan")) is not None


async def _las_dos():
    return await asyncio.gather(precio("pan"), precio("leche"))


def test_gather():
    assert list(asyncio.run(_las_dos())) == [2, 3]


async def _en_una_tarea():
    return await asyncio.create_task(precio("pan"))


def test_tarea():
    assert asyncio.run(_en_una_tarea()) == 2


def test_corrutina_que_llama_a_otras():
    assert asyncio.run(total(["pan", "leche"])) == 5


def test_tareas_del_codigo():
    assert asyncio.run(en_tareas(["pan", "leche"])) == 5


def test_corrutina_lanza():
    with pytest.raises(ErrorDeTienda):
        asyncio.run(agotado("pan"))


def test_corrutina_lanza_cualquiera():
    with pytest.raises(Exception):
        asyncio.run(agotado("pan"))


def test_corrutina_sin_esperar():
    corrutina = precio("pan")
    corrutina.close()
    assert corrutina is not None


def test_sigue_siendo_de_corrutina():
    assert asyncio.iscoroutinefunction(precio)


def test_orden_por_un_runner():
    assert runner.ejecutar(REGISTRADAS) == 0


def test_orden_por_un_runner_y_lo_guardado():
    antes = guardadas()
    runner.ejecutar(REGISTRADAS)
    assert guardadas() - antes == 1


def test_entregada_a_una_libreria():
    assert pytest.raises(KeyError, estado, "otro")


def test_lo_devuelto_vuelve_a_ser_real():
    assert estado(clave()) == "servido"


def test_una_lista_cambiada_no_se_devuelve():
    cosas = cesta()
    cosas.append("pan")
    guardar_cesta(cosas)
    assert "pan" in en_la_cesta()


def test_dataclass_del_proyecto():
    Nif("12345678z")
"""

ASYNC_EXPECTED = {
    "test_corrutina": "probado",
    "test_gather": "probado",
    "test_tarea": "probado",
    "test_corrutina_que_llama_a_otras": "probado",
    "test_tareas_del_codigo": "probado",
    "test_corrutina_lanza": "probado",
    "test_lo_devuelto_vuelve_a_ser_real": "probado",
    "test_corrutina_debil": "sobrevive",
    "test_corrutina_lanza_cualquiera": "sobrevive",
    # The list it changed is its own: handing back the original would lose the change.
    "test_una_lista_cambiada_no_se_devuelve": "sobrevive",
    # Two counts of what the runner saved, both from the When on: known weak shape.
    "test_orden_por_un_runner_y_lo_guardado": "sobrevive",
    "test_corrutina_sin_esperar": "no_llama",
    "test_sigue_siendo_de_corrutina": "no_llama",
    "test_entregada_a_una_libreria": "no_llama",
    "test_dataclass_del_proyecto": "no_llama",
    "test_orden_por_un_runner": "a_traves",
}


@pytest.fixture(scope="module")
def async_runs(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str | None, dict[str, TestCaseResult]]:
    project = tmp_path_factory.mktemp("tienda")
    (project / "src" / "tienda").mkdir(parents=True)
    (project / "src" / "tienda" / "__init__.py").write_text(ASYNC_CODE, encoding="utf-8")
    (project / "lib").mkdir()
    (project / "lib" / "runner.py").write_text(RUNNER, encoding="utf-8")
    (project / "tests" / "acceptance").mkdir(parents=True)
    (project / "tests" / "acceptance" / "test_calc.py").write_text(ASYNC_DRIVER, encoding="utf-8")
    (project / "tests" / "acceptance" / "conftest.py").write_text(THEN_CONFTEST, encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    return {mode: _run(project, mode) for mode in (None, "control", MORE, LESS)}


def test_every_async_or_library_test_passes_with_the_real_code(
    async_runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    for mode in (None, "control"):
        assert {name: case.status for name, case in async_runs[mode].items()} == dict.fromkeys(
            ASYNC_EXPECTED, "passed"
        )


@pytest.mark.parametrize(("name", "outcome"), sorted(ASYNC_EXPECTED.items()))
def test_each_async_or_library_shape_gets_the_verdict_of_the_adr(
    async_runs: dict[str | None, dict[str, TestCaseResult]], name: str, outcome: str
) -> None:
    proofs = interface_proofs(
        list(ASYNC_EXPECTED),
        async_runs["control"],
        {MORE: async_runs[MORE], LESS: async_runs[LESS]},
        ["tests/acceptance/"],
    )

    assert proofs[name].outcome == outcome, (name, proofs[name])


def test_a_coroutine_counts_once_awaited_and_only_then(
    async_runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    assert async_runs["control"]["test_corrutina"].interface_by_step("calls") == StepCounts(then=1)
    assert async_runs["control"]["test_corrutina_sin_esperar"].interface_calls == 0
    # What the code awaits inside is the code calling itself: one call, the driver's.
    assert async_runs["control"]["test_corrutina_que_llama_a_otras"].interface_calls == 1
    assert async_runs[MORE]["test_gather"].interface_altered == 2


def test_a_library_is_named_and_what_it_calls_is_never_altered(
    async_runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    for mode in ("control", MORE, LESS):
        case = async_runs[mode]["test_orden_por_un_runner"]
        assert case.interface_by_step("via") == StepCounts(then=1)
        assert case.interface_entries(ALTERED_STEPS) == ("runner",)
        assert case.interface_altered == 0
    handed = async_runs["control"]["test_entregada_a_una_libreria"]
    assert handed.interface_by_step("handed") == StepCounts(then=1)
    (library,) = handed.interface_entries(ALTERED_STEPS)
    assert library.startswith("_pytest.")
    proof = interface_proofs([handed.name], async_runs["control"], {}, ["tests/acceptance/"])
    assert proof[handed.name].detail == "entrega"
    assert proof[handed.name].libraries == (library,)
    # The event loop is no library the driver called into, nor a dataclass's own __init__.
    for name in ("test_tareas_del_codigo", "test_dataclass_del_proyecto", "test_corrutina"):
        assert async_runs["control"][name].interface_by_step("via") == StepCounts(), name
