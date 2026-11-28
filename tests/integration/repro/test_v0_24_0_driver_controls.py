"""G3: the driver's commit passes the controls that block every attempt before it is sealed.

It was the only commit Lictor made without them: a driver that didn't pass the lint entered the
branch with its warnings, and one the lint read would have failed every attempt with nothing the
implementer could change. Here the driver's stubs carry an unused import, which `ruff check src`
refuses: the driver is rejected, its retry is sealed; with `"driver": false` on the lint the person
exempts it, and the report says so.
"""

from pathlib import Path

from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    MODULE_PATH,
    STUB,
    Agent,
    Outcome,
    all_controls,
    events_of,
    false_greens,
    green_ids,
    run_lictor,
    write,
)

UNUSED = "import os\n\n" + STUB


def _driver(*stubs: str) -> object:
    """A driver whose stubs are `stubs[attempt - 1]`, the last one from then on."""

    def driver(task: object, attempt: int = 1) -> None:
        cwd: Path = task.cwd  # type: ignore[attr-defined]
        write(cwd, MODULE_PATH, stubs[min(attempt, len(stubs)) - 1])
        write(cwd, DRIVER_PATH, DRIVER)

    return driver


@all_controls
def test_a_driver_whose_commit_doesnt_pass_the_lint_is_rejected_and_its_retry_sealed(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    agent = Agent(driver=_driver(UNUSED, STUB))  # type: ignore[arg-type]

    assert run_lictor_ok(root, agent)

    rejected = events_of(root, "driver.rejected")
    assert [e["data"]["reason"] for e in rejected] == ["control"]
    told = agent.prompts["driver"][1]
    assert "El commit del driver no pasa el control 'lint', que bloquea cada intento" in told
    assert "F401" in told
    assert len(events_of(root, "driver.sealed")) == 1
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []


@all_controls
def test_a_control_the_person_exempts_is_not_passed_by_the_driver_and_the_report_says_so(
    make_project: ProjectFactory,
) -> None:
    root = make_project(config_changes={"controls": [{"id": "lint", "driver": False}]})
    agent = Agent(driver=_driver(UNUSED))  # type: ignore[arg-type]

    assert run_lictor_ok(root, agent)

    assert events_of(root, "driver.rejected") == []
    (sealed,) = events_of(root, "driver.sealed")
    assert sealed["data"]["driver_exempt"] == ["lint"]
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    exempt = (
        'El commit del driver no pasó `lint` antes del sello: lictor.json los marca con "driver"'
    )
    assert f"{exempt}: false." in report


def run_lictor_ok(root: Path, agent: Agent) -> bool:
    return run_lictor(root, agent) == Outcome(0)
