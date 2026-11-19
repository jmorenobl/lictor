"""The profile's impostor of the interface, run in real pytest processes as verification would.

Each test of the fake driver is one shape a test of a scenario takes; the domain's rule then judges
what the plugin reported, so the table of ADR-0011 is checked end to end, without Lictor's graph.
"""

import json
import math
import os
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from lictor.adapters.config.loader import DEFAULT_PROFILES_DIR
from lictor.adapters.config.profiles.python_uv import lictor_impostor
from lictor.adapters.config.profiles.python_uv.lictor_impostor import (
    LESS,
    MORE,
    SENTINEL,
    alter,
)
from lictor.adapters.readers.junit import JUnitReader
from lictor.domain.interface_proof import interface_proofs
from lictor.domain.model import TestCaseResult
from lictor.domain.reports import JUnitReport

CODE = """
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


class Resultado:
    def __init__(self, valor):
        self.valor = valor


def resultado():
    return Resultado(4)


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
}


def _run(project: Path, mode: str | None) -> dict[str, TestCaseResult]:
    plugin_dir = DEFAULT_PROFILES_DIR / "python_uv"
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "LICTOR"))},
        "PYTHONPATH": os.pathsep.join([str(plugin_dir), str(project / "src")]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    if mode is not None:
        env["LICTOR_IMPOSTOR"] = json.dumps(
            {
                "mode": mode,
                "writable": [str(project / "src")],
                "driver": [str(project / "tests" / "acceptance")],
            }
        )
    report = project / f"out-{mode}.xml"
    command = [sys.executable, "-B", "-m", "pytest", "-p", "lictor_outcome", "-p"]
    command += ["lictor_impostor", "-p", "no:cacheprovider", "-q", "tests", "--junitxml"]
    subprocess.run(
        [*command, str(report)],
        cwd=project,
        env=env,
        capture_output=True,
        check=False,
        timeout=120,
    )
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
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    return {mode: _run(project, mode) for mode in (None, "control", MORE, LESS)}


def test_every_test_passes_with_the_real_code(
    runs: dict[str | None, dict[str, TestCaseResult]],
) -> None:
    for mode in (None, "control"):
        assert {name: case.status for name, case in runs[mode].items()} == dict.fromkeys(
            EXPECTED, "passed"
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
        (True, False, False),
        (False, True, True),
        ("ab", "ab·", "a"),
        ("", "·", "·"),
        (b"ab", b"ab\0", b"a"),
        ([1, 2], [1, 2, SENTINEL], [1]),
        ((1,), (1, SENTINEL), ()),
        ([], [SENTINEL], [SENTINEL]),
        ({"a": 1}, {"a": 1, SENTINEL: SENTINEL}, {}),
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


def test_the_sentinel_is_none_of_the_values_a_test_expects() -> None:
    assert SENTINEL not in (None, 0, "", False, [], ())
    assert repr(SENTINEL) == "<impostor de Lictor>"
