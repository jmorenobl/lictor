"""ADR-0011, amendment of objects of value: a result that is an object is proved, not registered.

The code meets IVA-001 and IVA-002 before the first run, as in `test_v0_8_0_interface_impostor`,
but returns a frozen dataclass: before 0.18.0 the impostor couldn't alter it (`sin_alterar`) and a
person had to register a right test by hand. Now it alters every field of a copy of the class, the
Then's assertion fails in both modes, and the scenarios go green with no person. The Then also
reads its scenario's value (ADR-0016), which the Gherkin's impostor reports, with no run of its
own: the interface proved them.
"""

from pathlib import Path

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
    run_lictor,
    write,
)
from tests.integration.repro.test_v0_8_0_interface_impostor import (
    _code_already_meets,
    _driver_over_the_code,
)

_OF_VALUE = (
    GOOD_2.replace(
        '"""Interfaz."""\n',
        '"""Interfaz."""\n\nfrom dataclasses import dataclass\n\n\n'
        "@dataclass(frozen=True)\nclass Total:\n"
        '    """Total."""\n\n'
        "    valor: float\n"
        "    moneda: str = 'EUR'\n",
    )
    .replace("def general(importe: float) -> float:", "def general(importe: float) -> Total:")
    .replace("def reducido(importe: float) -> float:", "def reducido(importe: float) -> Total:")
    .replace("return round(importe * 1.21, 2)", "return Total(round(importe * 1.21, 2))")
    .replace("return round(importe * 1.10, 2)", "return Total(round(importe * 1.10, 2))")
)
_ORACLE = {
    **ORACLE,
    "IVA-001": "iva.general(100) == iva.Total(121)",
    "IVA-002": "iva.reducido(100) == iva.Total(110)",
}


def _negative(task: AgentTask, _sid: str, _attempt: int) -> None:
    write(
        task.cwd, MODULE_PATH, _OF_VALUE.replace("raise NotImplementedError", "return importe < 0")
    )


def test_an_object_of_value_the_code_already_meets_is_proved(make_project: ProjectFactory) -> None:
    root: Path = make_project()
    _code_already_meets(root, _OF_VALUE)
    driver = DRIVER.replace(TOTAL_CHECK, "assert calculo() == iva.Total(esperado)")
    agent = Agent(driver=_driver_over_the_code(driver), impl=_negative)

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls == ["driver", "IVA-003"]
    assert events_of(root, "driver.rejected") == []
    [sealed] = events_of(root, "driver.sealed")
    assert sealed["data"]["already_met"] == ["IVA-001", "IVA-002"]
    [measured] = events_of(root, "driver.measured")
    for sid in ("IVA-001", "IVA-002"):
        found = measured["data"]["interface"][sid]
        assert (found["outcome"], found["interface"]) == ("probado", "probado")
        assert found["failed_step"] == {"mas": "then", "menos": "then"}
        assert found["provoked"] == []
        assert found["gherkin"]["values"] == 1
        assert found["gherkin"]["unread"] == []
        assert found["gherkin"]["read"] == []  # no run of its own: the interface proved it
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "| Gherkin |" in report
    assert "| 1 valor |" in report
    assert not list((root / ".lictor" / "runs").rglob("gherkin-*"))
    assert false_greens(root, _ORACLE) == []
