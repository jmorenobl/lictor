"""F85, amendment of ADR-0016: a Then that can't read the altered result, where it reads its value.

The code meets IVA-001 and IVA-002 before the first run and gives the totals as XML, which the
driver's Then parses. Altered, the XML can't be parsed: the Then fails with an error, not with its
assertion, in both modes. When that happens in the statement that compares the scenario's value,
or in one that feeds it, the test is proved by what the real plugins report; a helper that parses on
one line and compares something else on another still goes to the person.
"""

from pathlib import Path

import pytest

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    GOOD_2,
    GOOD_3,
    MODULE_PATH,
    TOTAL_CHECK,
    Agent,
    Outcome,
    events_of,
    false_greens,
    run_lictor,
    write,
)
from tests.integration.repro.test_v0_8_0_interface_impostor import (
    _code_already_meets,
    _driver_over_the_code,
)

_XML = (
    "\n\ndef factura_xml(importe: float) -> str:\n"
    '    """La factura con su total, como XML."""\n'
    '    return f"<factura><total>{general(importe)}</total></factura>\\n"\n'
    "\n\ndef factura_xml_reducido(importe: float) -> str:\n"
    '    """La factura con el total reducido, como XML."""\n'
    '    return f"<factura><total>{reducido(importe)}</total></factura>\\n"\n'
)

# The When asks for the XML; each Then parses it, in one of three shapes.
_OVER_XML = (
    DRIVER.replace("import iva\n", "from xml.etree import ElementTree\n\nimport iva\n", 1)
    .replace("lambda: iva.general(importe)", "lambda: iva.factura_xml(importe)")
    .replace("lambda: iva.reducido(importe)", "lambda: iva.factura_xml_reducido(importe)")
)
_ONE_STATEMENT = _OVER_XML.replace(
    TOTAL_CHECK, 'assert float(ElementTree.fromstring(calculo()).findtext("total")) == esperado'
)
_TWO_LINES = _OVER_XML.replace(
    TOTAL_CHECK,
    "raiz = ElementTree.fromstring(calculo())\n"
    '    assert float(raiz.findtext("total")) == esperado',
)
# Weak: it parses what the code returns, and compares the value with constants of its own.
_HELPER = _OVER_XML.replace(TOTAL_CHECK, "_comprueba(calculo, esperado)") + (
    "\n\ndef _comprueba(calculo, esperado):\n"
    "    ElementTree.fromstring(calculo())\n"
    "    assert esperado in (121, 110)\n"
)


def _implement_keeping_the_xml(task: AgentTask, _sid: str, _attempt: int) -> None:
    write(task.cwd, MODULE_PATH, GOOD_3 + _XML)


def _measured(root: Path) -> dict[str, tuple[str, str]]:
    [measured] = events_of(root, "driver.measured")
    return {sid: (m["outcome"], m["interface"]) for sid, m in measured["data"]["interface"].items()}


@pytest.mark.parametrize("driver", [_ONE_STATEMENT, _TWO_LINES], ids=["una", "dos_lineas"])
def test_a_then_that_can_t_read_the_altered_xml_where_it_compares_the_value_proves_it(
    make_project: ProjectFactory, driver: str
) -> None:
    root = make_project()
    _code_already_meets(root, GOOD_2 + _XML)
    agent = Agent(driver=_driver_over_the_code(driver), impl=_implement_keeping_the_xml)

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls == ["driver", "IVA-003"]
    [sealed] = events_of(root, "driver.sealed")
    assert sealed["data"]["already_met"] == ["IVA-001", "IVA-002"]
    assert _measured(root) == {
        "IVA-001": ("probado", "no_aserta"),
        "IVA-002": ("probado", "no_aserta"),
    }
    assert false_greens(root) == []


def test_a_helper_that_parses_on_one_line_and_compares_on_another_goes_to_the_person(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    _code_already_meets(root, GOOD_2 + _XML)
    agent = Agent(driver=_driver_over_the_code(_HELPER), impl=_implement_keeping_the_xml)

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(10), agent.calls
    assert agent.calls == ["driver"]
    [rejected] = events_of(root, "driver.rejected")
    assert rejected["data"]["final"] is True
    assert _measured(root) == {
        "IVA-001": ("solo_el_valor", "no_aserta"),
        "IVA-002": ("solo_el_valor", "no_aserta"),
    }
