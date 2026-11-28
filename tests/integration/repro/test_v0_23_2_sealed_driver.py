"""0.23.2, G1: a sealed driver that maps a scenario to several cases stops the run with 10.

At the red the stub fails before a Go `t.Run` is reached, so each scenario is one case and the
driver is sealed; with the code done the subtests appear, and by name they are the same scenario.
The mapping is of every scenario at once, so each one spent every attempt on what none could fix.
Here the same shape in pytest-bdd: a second test bound to IVA-001 appears once the code computes.
"""

from pathlib import Path

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    MODULE_PATH,
    STUB,
    UNIT_TEST,
    Agent,
    Outcome,
    events_of,
    green_ids,
    quarantined,
    run_lictor,
    write,
)

# Bound only when the code already computes: what a `t.Run` after the stub's call does in Go.
SECOND_CASE = (
    "\n\ndef _ya_calcula():\n    try:\n        return iva.general(100) == 121\n"
    "    except Exception:\n        return False\n\n\n"
    'if _ya_calcula():\n\n    @scenario(FEATURE, "IVA general")\n'
    "    def test_iva_001_bis():\n        pass\n"
)


def _driver(task: AgentTask, _attempt: int = 1) -> None:
    write(task.cwd, MODULE_PATH, STUB)
    write(task.cwd, DRIVER_PATH, DRIVER + SECOND_CASE)
    write(task.cwd, "tests/unit/test_smoke.py", UNIT_TEST)


def _stop_reason(root: Path) -> str:
    (finished,) = events_of(root, "run.finished")
    assert finished["data"]["exit_code"] == 10
    return str(finished["data"]["reason"])


def test_two_verifications_with_the_scenario_in_two_cases_stop_the_run(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    agent = Agent(driver=_driver)

    assert run_lictor(root, agent) == Outcome(10)

    assert agent.calls == ["driver", "IVA-001", "IVA-001"], "no gasta el tercer intento"
    assert quarantined(root) == []
    assert green_ids(root) == []
    reason = _stop_reason(root)
    assert reason.startswith("El driver sellado da 2 casos para el escenario IVA-001")
    assert "test_iva_001_bis" in reason
    retry = agent.prompts["IVA-001"][1]
    assert "Si un test tuyo lleva el ID en el nombre" in retry
    assert "no puedes cambiarlos" in retry


def test_a_declared_conflict_doesnt_hide_the_streak(make_project: ProjectFactory) -> None:
    """V1: the conflict goes to quarantine at once, and the next scenario's mapping is the same."""
    root = make_project()
    agent = Agent(driver=_driver)
    agent.executor.scenario_modes = {"IVA-001": "conflict"}

    assert run_lictor(root, agent) == Outcome(10)

    assert agent.calls == ["driver", "IVA-001", "IVA-002"]
    assert "escenario IVA-001" in _stop_reason(root)


def test_one_attempt_per_scenario_doesnt_hide_the_streak(make_project: ProjectFactory) -> None:
    """V1: with `max_attempts: 1` a streak per scenario never came."""
    root = make_project(config_changes={"limits": {"max_attempts": 1}})
    agent = Agent(driver=_driver)

    assert run_lictor(root, agent) == Outcome(10)

    assert agent.calls == ["driver", "IVA-001", "IVA-002"]
    assert quarantined(root) == ["IVA-001"]
