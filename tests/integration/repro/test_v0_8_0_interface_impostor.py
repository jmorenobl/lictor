"""ADR-0011 (F58): a new scenario the code already meets is proved by the impostor of the interface.

The code meets IVA-001 and IVA-002 before the first run, and the fresh driver doesn't replace it
with stubs: both pass at the red. The impostor alters what the interface returns to the driver; a
test whose own assertion fails with the result + 1 and - 1 checks it, and the scenario goes green
with no call to the agent and no person. A test that ignores the result still stops the run.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lictor.application.usecases import check_red
from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    GOOD_2,
    GOOD_3,
    MODULE_PATH,
    TOTAL_CHECK,
    UNIT_TEST,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    green_ids,
    known_limitation,
    require,
    run_lictor,
    write,
)


def _code_already_meets(root: Path, code: str = GOOD_2) -> None:
    """IVA-001 and IVA-002 are implemented before Lictor ever ran; IVA-003 is still a stub."""
    write(root, MODULE_PATH, code)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: IVA general y reducido, antes de Lictor")


def _driver_over_the_code(driver: str = DRIVER) -> Callable[[AgentTask, int], None]:
    def write_driver(task: AgentTask, _attempt: int) -> None:
        write(task.cwd, DRIVER_PATH, driver)
        write(task.cwd, "tests/unit/test_smoke.py", UNIT_TEST)

    return write_driver


def _green_events(root: Path) -> dict[str, dict[str, object]]:
    return {str(e["scenarioid"]): e["data"] for e in events_of(root, "scenario.green")}


def test_a_new_scenario_the_code_already_meets_goes_green_without_a_person(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    _code_already_meets(root)
    agent = Agent(driver=_driver_over_the_code())

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls == ["driver", "IVA-003"]
    assert events_of(root, "driver.rejected") == []
    greens = _green_events(root)
    for sid in ("IVA-001", "IVA-002"):
        assert greens[sid]["proof"] == "interface"
        assert greens[sid]["collateral"] is True
    message = git(root, "log", "-1", "--format=%B", str(greens["IVA-001"]["commit"]))
    assert "Lictor-Proof: interfaz" in message
    assert "Lictor-Asserted-By" not in message
    [sealed] = events_of(root, "driver.sealed")
    assert sealed["data"]["already_met"] == ["IVA-001", "IVA-002"]
    assert sealed["data"]["stub_impostors"] == 1  # IVA-003's stub, which the red check replaced
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "verde (ya se cumplía)" in report
    assert false_greens(root) == []


def test_the_red_check_records_in_which_step_each_impostor_caught_each_test(
    make_project: ProjectFactory,
) -> None:
    """The facts come from the real runner of the Gherkin: the step and the calls of each step.

    Each test calls the interface only in its Then, through what its When left.
    """
    root = make_project()
    _code_already_meets(root)
    agent = Agent(driver=_driver_over_the_code())
    require(run_lictor(root, agent) == Outcome(0), "el run debía acabar en verde")

    [measured] = events_of(root, "driver.measured")
    data = measured["data"]
    assert data["red"] == {"IVA-003": {"step": "then", "caught": "sin_implementar"}}
    assert data["stubs"] == {
        "IVA-003": {
            "by_constant": {"return None": "entonces", "return True": "pasa"},
            "rule_c": "entonces",
        }
    }
    for sid in ("IVA-001", "IVA-002"):
        found = data["interface"][sid]
        assert found["outcome"] == "probado"
        assert found["calls"] == {"given": 0, "when": 0, "then": 1, "outside": 0}
        assert found["failed_step"] == {"mas": "then", "menos": "then"}
        assert found["altered"]["mas"] == {"given": 0, "when": 0, "then": 1, "outside": 0}
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "- **Interfaz**: 2 `probado`" in report


_CHECKS_THAT_IGNORE_THE_RESULT = [
    pytest.param("calculo()", id="sin-assert"),
    pytest.param("calculo().real", id="atributo-suelto", marks=pytest.mark.exhaustive),
    pytest.param("assert calculo() is not None", id="no-none", marks=pytest.mark.exhaustive),
    pytest.param(
        "assert isinstance(calculo(), float)", id="isinstance", marks=pytest.mark.exhaustive
    ),
    pytest.param("assert calculo()", id="verdadero", marks=pytest.mark.exhaustive),
]


@pytest.mark.parametrize("check", _CHECKS_THAT_IGNORE_THE_RESULT)
def test_a_test_that_ignores_the_result_still_stops_the_run(
    make_project: ProjectFactory, check: str
) -> None:
    """The code meets the scenario, but nothing tells this test from one that checks nothing."""
    root = make_project()
    _code_already_meets(root)
    agent = Agent(driver=_driver_over_the_code(DRIVER.replace(TOTAL_CHECK, check)))

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(10), agent.calls
    assert agent.calls == ["driver", "driver"]
    rejected = events_of(root, "driver.rejected")
    assert {event["data"]["reason"] for event in rejected} == {"sin_prueba"}
    error = str(rejected[-1]["data"]["error"])
    assert "sigue pasando cuando la interfaz devuelve otro valor" in error
    assert "sin llegar a la interfaz" not in error
    assert "lictor register" in error
    assert green_ids(root) == []
    assert false_greens(root) == []


@known_limitation(
    "un driver que copia en el test lo que devuelve el código, en lugar del valor del Gherkin",
    until="fase 2c (juez semántico del driver)",
)
def test_a_test_that_copies_a_wrong_result_of_the_code_is_a_false_green(
    make_project: ProjectFactory,
) -> None:
    """ADR-0011, consequences: the impostor proves the test reads the result, not its value."""
    root = make_project()
    wrong = "importe * 1.20"
    _code_already_meets(root, GOOD_2.replace("importe * 1.21", wrong))
    copied = DRIVER.replace(
        TOTAL_CHECK, "assert calculo() == (120 if esperado == 121 else esperado)"
    )

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, GOOD_3.replace("importe * 1.21", wrong))

    agent = Agent(driver=_driver_over_the_code(copied), impl=impl)

    require(run_lictor(root, agent) == Outcome(0), "el run debía acabar en verde")

    assert false_greens(root) == []


@pytest.fixture
def interrupted_after_registering(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stop the run once, right after the red check moved the branch to its registration."""
    original = check_red.CheckRed._advance
    calls: list[int] = []

    def advance_and_die(self: check_red.CheckRed, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(check_red.CheckRed, "_advance", advance_and_die)
    return calls


def test_a_run_stopped_after_registering_resumes_and_tests_them_as_greens(
    make_project: ProjectFactory, interrupted_after_registering: list[int]
) -> None:
    root = make_project()
    _code_already_meets(root)
    agent = Agent(driver=_driver_over_the_code())
    require(run_lictor(root, agent).killed, "el run debía pararse tras registrar")
    head = git(root, "rev-parse", "HEAD").strip()
    require("Lictor-Proof: interfaz" in git(root, "log", "-1", "--format=%B"), "sin registro")

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls == ["driver", "IVA-003"]
    assert git(root, "rev-list", "--count", f"{head}..HEAD").strip() != "0"
    assert green_ids(root).count("IVA-001") == 1
    assert false_greens(root) == []
