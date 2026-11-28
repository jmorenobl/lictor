"""S23: a scenario taken out of `features/` after it was green is retired, and the next run says so.

`features/` is the project's accumulated contract: what is green stays and runs in every attempt.
Before 0.25.0 nothing saw a green that left it: the sealed driver still read it, the acceptance
didn't load, and the next driver met a crashed suite without knowing why. Now `doctor` warns of it,
the next driver hears to take its test out, its commit records it in `Lictor-Retired`, and the
report lists it; from then on it is no longer pending.
"""

from pathlib import Path

from typer.testing import CliRunner

from lictor.cli import app
from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    Agent,
    Outcome,
    git,
    require,
    run_lictor,
    write,
)

FEATURE = Path("features") / "iva.feature"
WITHOUT_THE_THIRD = DRIVER[: DRIVER.index('@scenario(FEATURE, "Importe negativo")')] + DRIVER[
    DRIVER.index("@given(") :
].replace(
    '\n\n@then("se rechaza por importe no válido")\ndef rechazado(importe):\n'
    "    assert iva.negativo(importe) is True\n",
    "\n",
)


def _retire_the_third(root: Path) -> None:
    text = (root / FEATURE).read_text(encoding="utf-8")
    write(root, str(FEATURE), text[: text.index("  @IVA-003")].rstrip() + "\n")
    git(root, "commit", "-qam", "feat: se retira IVA-003")


def _doctor(root: Path) -> str:
    return CliRunner().invoke(app, ["doctor", "--cwd", str(root)]).output


def test_a_green_scenario_taken_out_is_retired_by_the_next_run(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    require(run_lictor(root, Agent()) == Outcome(0), "el primer run debía acabar en verde")
    _retire_the_third(root)

    warned = _doctor(root)

    def driver(task: AgentTask, _attempt: int = 1) -> None:
        # The code is there already: the new driver only takes the retired one's test out.
        write(task.cwd, DRIVER_PATH, WITHOUT_THE_THIRD)

    agent = Agent(driver=driver)
    assert run_lictor(root, agent, new=True) == Outcome(0)

    assert "IVA-003 estaban en verde y ya no están en features/: se retiraron" in warned
    told = agent.prompts["driver"][0]
    assert "- Los escenarios IVA-003 se retiraron de la especificación: quita del driver" in told
    driver_commit = git(root, "log", "--format=%H", "--grep=driver de aceptación", "-1").strip()
    trailers = git(root, "log", "-1", "--format=%(trailers:key=Lictor-Retired)", driver_commit)
    assert "Lictor-Retired: IVA-003" in trailers
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "## Escenarios retirados" in report
    assert "IVA-003" in report.split("## Escenarios retirados")[1].split("##")[0]
    assert "se retiraron" not in _doctor(root)
