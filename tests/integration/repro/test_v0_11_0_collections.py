"""ADR-0011, amended for collections: one is altered with elements of its own form, at its start.

The code meets IVA-001 and IVA-002 before the first run, as in `test_v0_8_0_interface_impostor`,
but returns a list of objects. In 0.10.0 the impostor grew it with its sentinel at the end: a
right test that read each element failed by an error on the sentinel (`no_aserta`), and one that
read the first survived. Both are proved now. What it still can't tell apart says which exception,
in which step and on which line.
"""

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    GOOD_2,
    MODULE_PATH,
    ORACLE,
    TOTAL_CHECK,
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


def _lines(general: str, reducido: str) -> str:
    """The same code, returning a list of `Linea` built from each function's own expression."""
    return (
        GOOD_2.replace(
            '"""Interfaz."""\n',
            '"""Interfaz."""\n\n\nclass Linea:\n    """Línea."""\n\n'
            "    def __init__(self, valor: float) -> None:\n"
            "        self.valor = valor\n",
        )
        .replace("def general(importe: float) -> float:", "def general(importe: float) -> list:")
        .replace("def reducido(importe: float) -> float:", "def reducido(importe: float) -> list:")
        .replace("return round(importe * 1.21, 2)", f"return {general}")
        .replace("return round(importe * 1.10, 2)", f"return {reducido}")
    )


# The total and then the base: the first line is the one the scenario is about.
_TOTAL_FIRST = _lines(
    "[Linea(round(importe * 1.21, 2)), Linea(importe)]",
    "[Linea(round(importe * 1.10, 2)), Linea(importe)]",
)
_ONE_NUMBER = (
    GOOD_2.replace("-> float:", "-> list:", 2)
    .replace("return round(importe * 1.21, 2)", "return [round(importe * 1.21, 2)]")
    .replace("return round(importe * 1.10, 2)", "return [round(importe * 1.10, 2)]")
)
_ONLY_THE_TOTAL = _lines("[Linea(round(importe * 1.21, 2))]", "[Linea(round(importe * 1.10, 2))]")


# What the code of these cases meets, checked outside the suite as `false_greens` does.
_LINES_ORACLE = {
    **ORACLE,
    "IVA-001": "iva.general(100)[0].valor == 121",
    "IVA-002": "iva.reducido(100)[0].valor == 110",
}

_ONE_NUMBER_ORACLE = {
    **ORACLE,
    "IVA-001": "iva.general(100)[0] == 121",
    "IVA-002": "iva.reducido(100)[0] == 110",
}


def _proved_at_once(
    make_project: ProjectFactory, code: str, check: str, oracle: dict[str, str] | None = None
) -> None:
    root = make_project()
    _code_already_meets(root, code)

    def negative_too(task: AgentTask, _scenario_id: str, _attempt: int) -> None:
        write(
            task.cwd, MODULE_PATH, code.replace("raise NotImplementedError", "return importe < 0")
        )

    driver = _driver_over_the_code(DRIVER.replace(TOTAL_CHECK, check))
    agent = Agent(driver=driver, impl=negative_too)

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls == ["driver", "IVA-003"]
    assert events_of(root, "driver.rejected") == []
    [sealed] = events_of(root, "driver.sealed")
    assert sealed["data"]["already_met"] == ["IVA-001", "IVA-002"]
    assert set(green_ids(root)) == {"IVA-001", "IVA-002", "IVA-003"}
    assert false_greens(root, oracle or _LINES_ORACLE) == []


def test_a_test_that_reads_each_element_of_a_list_of_objects_is_proved(
    make_project: ProjectFactory,
) -> None:
    _proved_at_once(
        make_project, _ONLY_THE_TOTAL, "assert [l.valor for l in calculo()] == [esperado]"
    )


def test_a_test_that_reads_the_first_element_is_proved(make_project: ProjectFactory) -> None:
    _proved_at_once(make_project, _TOTAL_FIRST, "assert calculo()[0].valor == esperado")


def test_the_first_of_a_list_of_one_number_is_proved_by_its_statement(
    make_project: ProjectFactory,
) -> None:
    """The first of a list of one number: with one less, its index fails before its assertion.

    It was the known limit of the amendment, sent to the person as `solo_el_valor`. Since 0.21.0
    the `IndexError` is raised in the statement that compares the scenario's value, which the
    Gherkin proves it reads: the test is proved (F85, amendment of ADR-0016).
    """
    _proved_at_once(
        make_project,
        _ONE_NUMBER,
        "assert calculo()[0] == esperado",
        {
            **ORACLE,
            "IVA-001": "iva.general(100)[0] == 121",
            "IVA-002": "iva.reducido(100)[0] == 110",
        },
    )
