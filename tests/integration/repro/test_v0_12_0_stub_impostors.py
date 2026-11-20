"""ADR-0012: the stubs' impostors ask for an assertion of a Then, with doubles.

Before, a fresh driver's pending test was accepted if it failed with `return None` or with
`return True` in some way: a test that read `.total` of what a stub returned, with no assert,
failed by `AttributeError` and was sealed. And a right test whose stub is an order called from a
Given passed with both and rejected the driver (F64). Now some constant has to make an assertion
of the driver fail in a Then; the doubles let a test that reads an attribute reach it, and a test
that doesn't depend on the stubs goes to the impostor of the interface.
"""

from collections.abc import Callable

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    GOOD_3,
    MODULE_PATH,
    STUB,
    TOTAL_CHECK,
    UNIT_TEST,
    Agent,
    Outcome,
    events_of,
    false_greens,
    green_ids,
    run_lictor,
    write,
)
from tests.integration.repro.test_v0_8_0_interface_impostor import (
    _code_already_meets,
    _driver_over_the_code,
)

# The interface returns an invoice, and the Then reads its total.
_INVOICE = '"""Interfaz."""\n\n\nclass Factura:\n    """Factura."""\n\n' + (
    "    def __init__(self, total: float) -> None:\n        self.total = total\n"
)
_OBJECT_STUB = STUB.replace('"""Interfaz."""\n', _INVOICE).replace(
    "(importe: float) -> float:", "(importe: float) -> Factura:"
)
_OBJECT_GOOD = (
    _OBJECT_STUB.replace("raise NotImplementedError", "return Factura(round(importe * 1.21, 2))", 1)
    .replace("raise NotImplementedError", "return Factura(round(importe * 1.10, 2))", 1)
    .replace("raise NotImplementedError", "return importe < 0", 1)
)
_OBJECT_ORACLE = {
    "IVA-001": "iva.general(100).total == 121",
    "IVA-002": "iva.reducido(100).total == 110",
    "IVA-003": "iva.negativo(-5) is True",
}


def _object_driver(check: str) -> Callable[[AgentTask, int], None]:
    def write_driver(task: AgentTask, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, _OBJECT_STUB)
        write(task.cwd, DRIVER_PATH, DRIVER.replace(TOTAL_CHECK, check))
        write(task.cwd, "tests/unit/test_smoke.py", UNIT_TEST)

    return write_driver


def _object_impl(task: AgentTask, _scenario_id: str, _attempt: int) -> None:
    write(task.cwd, MODULE_PATH, _OBJECT_GOOD)


def test_a_test_that_reads_an_attribute_of_what_a_stub_returns_is_accepted(
    make_project: ProjectFactory,
) -> None:
    """`None.total` fails by an error: a double lets the Then's assertion fail instead."""
    root = make_project()
    agent = Agent(driver=_object_driver("assert calculo().total == esperado"), impl=_object_impl)

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls[0] == "driver"
    assert "driver" not in agent.calls[1:]
    assert events_of(root, "driver.rejected") == []
    [measured] = events_of(root, "driver.measured")
    stub = measured["data"]["stubs"]["IVA-001"]
    assert stub["by_constant"]["None"] == "no_aserta"
    assert stub["by_constant"]["un doble de Lictor"] == "entonces"
    assert (stub["outcome"], stub["verdict"]) == ("probado", "aceptado")
    assert set(green_ids(root)) == {"IVA-001", "IVA-002", "IVA-003"}
    assert false_greens(root, _OBJECT_ORACLE) == []


def test_a_test_that_reads_without_asserting_is_rejected(make_project: ProjectFactory) -> None:
    """It failed with None and True by an error, and was sealed: it passes with any value."""
    root = make_project()
    weak = _object_driver("calculo().total")
    good = _object_driver("assert calculo().total == esperado")

    def driver(task: AgentTask, attempt: int) -> None:
        (weak if attempt == 1 else good)(task, attempt)

    agent = Agent(driver=driver, impl=_object_impl)

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls[:2] == ["driver", "driver"]
    [rejected] = events_of(root, "driver.rejected")
    assert rejected["data"]["reason"] == "impostor"
    error = str(rejected["data"]["error"])
    assert (
        "El test test_iva_001 del escenario IVA-001 pasa también cuando los stubs devuelven otro "
        "valor en lugar de lanzar NotImplementedError"
    ) in error
    assert "en los Dados" not in error
    assert false_greens(root, _OBJECT_ORACLE) == []


# An order: it records the invoice and returns nothing. The Given calls it; what the scenario
# checks is the total, which the code already computes.
_ORDER = (
    "\n\ndef registrar(importe: float) -> None:\n"
    '    """Registrar."""\n'
    "    raise NotImplementedError\n"
)
_ORDER_DONE = _ORDER.replace("raise NotImplementedError", "return None")
_GIVEN = "def factura(importe):\n    return importe\n"


def test_a_test_whose_stub_is_an_order_in_a_given_goes_to_the_interface(
    make_project: ProjectFactory,
) -> None:
    """It passes with every constant, as a right one would: the interface proves it (F64)."""
    root = make_project()
    _code_already_meets(
        root, GOOD_3.replace("return importe < 0", "raise NotImplementedError") + _ORDER
    )
    driver = DRIVER.replace(
        _GIVEN, "def factura(importe):\n    iva.registrar(importe)\n    return importe\n"
    )
    assert driver != DRIVER

    def implement(task: AgentTask, _scenario_id: str, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, GOOD_3 + _ORDER_DONE)

    agent = Agent(driver=_driver_over_the_code(driver), impl=implement)

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert events_of(root, "driver.rejected") == []
    [measured] = events_of(root, "driver.measured")
    stub = measured["data"]["stubs"]["IVA-001"]
    assert (stub["outcome"], stub["interface"], stub["verdict"]) == (
        "a_la_interfaz",
        "probado",
        "aceptado",
    )
    [red] = [p for p in (root / ".lictor" / "runs").glob("*/driver/*-red") if p.is_dir()]
    assert (red / "stubs-interfaz-menos" / "acceptance.xml").is_file()
    [sealed] = events_of(root, "driver.sealed")
    assert sealed["data"]["stub_unchecked"] == []
    assert set(green_ids(root)) == {"IVA-001", "IVA-002", "IVA-003"}
    assert false_greens(root) == []
