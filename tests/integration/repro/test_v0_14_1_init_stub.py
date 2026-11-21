"""0.14.1 (F68): a stub in an `__init__` no longer rejects every test of the driver.

The stubs' impostors put each constant where the stub raised. An `__init__` that returns True or a
double raises `TypeError` where the object is built, a Given or a When, so no test reached the
assertion of its Then and the driver was rejected whatever its tests: in the first adoption pass
the agent gave up classes for functions over `object`. Now every constant is None in an `__init__`,
and the methods, stubs too, return it.
"""

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    MODULE_PATH,
    UNIT_TEST,
    Agent,
    Outcome,
    events_of,
    false_greens,
    green_ids,
    run_lictor,
    write,
)

# As the first driver of that pass: the When builds the object, and the Then reads an attribute of
# what one of its methods returns. With None and True the Then fails by an error; only a double
# reaches its assertion, and the constructor stopped the double in the When.
_STUB = (
    '"""Interfaz."""\n\n\n'
    "class Factura:\n"
    '    """Factura."""\n\n'
    "    def __init__(self, total: float) -> None:\n"
    "        self.total = total\n\n\n"
    "class Tipo:\n"
    '    """Un tipo de IVA."""\n\n'
    "    def __init__(self, tasa: float) -> None:\n"
    "        raise NotImplementedError\n\n"
    "    def factura(self, importe: float) -> Factura:\n"
    '        """La factura de un importe."""\n'
    "        raise NotImplementedError\n\n\n"
    "def negativo(importe: float) -> bool:\n"
    '    """Negativo."""\n'
    "    raise NotImplementedError\n"
)
_GOOD = (
    _STUB.replace("raise NotImplementedError", "self.tasa = tasa", 1)
    .replace("raise NotImplementedError", "return Factura(round(importe * self.tasa, 2))", 1)
    .replace("raise NotImplementedError", "return importe < 0", 1)
)
_WHEN = "def {name}(importe):\n    return lambda: iva.{name}(importe)\n"
_BUILT = (
    "def {name}(importe):\n    tipo = iva.Tipo({tasa})\n    return lambda: tipo.factura(importe)\n"
)
_DRIVER = (
    DRIVER.replace(_WHEN.format(name="general"), _BUILT.format(name="general", tasa=1.21))
    .replace(_WHEN.format(name="reducido"), _BUILT.format(name="reducido", tasa=1.10))
    .replace("assert calculo() == esperado", "assert calculo().total == esperado")
)
_ORACLE = {
    "IVA-001": "iva.Tipo(1.21).factura(100).total == 121",
    "IVA-002": "iva.Tipo(1.10).factura(100).total == 110",
    "IVA-003": "iva.negativo(-5) is True",
}


def _driver(task: AgentTask, _attempt: int) -> None:
    write(task.cwd, MODULE_PATH, _STUB)
    write(task.cwd, DRIVER_PATH, _DRIVER)
    write(task.cwd, "tests/unit/test_smoke.py", UNIT_TEST)


def _impl(task: AgentTask, _scenario_id: str, _attempt: int) -> None:
    write(task.cwd, MODULE_PATH, _GOOD)


def test_a_driver_whose_constructor_is_a_stub_is_sealed_at_the_first_attempt(
    make_project: ProjectFactory,
) -> None:
    assert _DRIVER.count("iva.Tipo(") == 2
    assert "calculo().total" in _DRIVER
    root = make_project()
    agent = Agent(driver=_driver, impl=_impl)

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls.count("driver") == 1
    assert events_of(root, "driver.rejected") == []
    [measured] = events_of(root, "driver.measured")
    stub = measured["data"]["stubs"]["IVA-001"]
    assert stub["by_constant"]["un doble de Lictor"] == "entonces"
    assert (stub["outcome"], stub["verdict"]) == ("probado", "aceptado")
    assert set(green_ids(root)) == {"IVA-001", "IVA-002", "IVA-003"}
    assert false_greens(root, _ORACLE) == []
