"""F84: the refactor improves what its scenario changed, and `L0-alcance` discards what leaves it.

With the real profile and `uv`. In the 4a pass with the 0.20.0, 9 of 21 refactors changed code
files their scenario never touched; two of them rewrote the old sending the project's hints kept
out of reach. A refactor that changes a code file its green didn't is discarded and the green
stays; one that changes only the green's files, or takes code out to a new one, is applied.
"""

from pathlib import Path

from lictor.application.ports import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    MODULE_PATH,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_impl,
    good_refactor,
    green_ids,
    run_lictor,
    with_refactor,
    write,
)

ALL = ["IVA-001", "IVA-002", "IVA-003"]
# Code that was there before the run, and no scenario touches.
OLD_PATH = "src/iva/antiguo.py"
OLD = '"""El envío antiguo."""\n\n\ndef envia(x: int) -> int:\n    return x\n'


def _project(make_project: ProjectFactory) -> Path:
    root = make_project()
    write(root, OLD_PATH, OLD)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: el envío antiguo")
    return root


def _outcomes(root: Path) -> dict[str, tuple[str, str | None]]:
    return {
        e["scenarioid"]: (e["data"]["outcome"], e["data"]["failed_at"])
        for e in events_of(root, "scenario.refactored")
    }


@with_refactor
def test_a_refactor_that_changes_code_its_scenario_didnt_is_discarded_and_the_green_stays(
    make_project: ProjectFactory,
) -> None:
    def refactor(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_refactor(task, scenario_id, attempt)
        write(task.cwd, OLD_PATH, OLD.replace("return x", "return int(x)"))

    root = _project(make_project)
    agent = Agent(impl=good_impl, refactor=refactor)

    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ALL
    assert _outcomes(root) == dict.fromkeys(ALL, ("discarded", "L0-alcance"))
    assert (root / OLD_PATH).read_text(encoding="utf-8") == OLD
    reasons = [
        path.read_text(encoding="utf-8")
        for path in (root / ".lictor" / "runs").rglob("*-refactor-verify*/error.txt")
    ]
    assert reasons
    assert all(OLD_PATH in reason and MODULE_PATH in reason for reason in reasons)
    [prompt, *_] = agent.prompts["refactor:IVA-001"]
    assert f"los ficheros que cambió el escenario ({MODULE_PATH})" in prompt
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "L0-alcance" in report
    assert false_greens(root) == []


@with_refactor
def test_a_refactor_of_its_scenarios_file_and_a_new_one_is_applied(
    make_project: ProjectFactory,
) -> None:
    def refactor(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_refactor(task, scenario_id, attempt)
        write(task.cwd, f"src/iva/apoyo_{scenario_id.lower().replace('-', '_')}.py", '"""A."""\n')

    root = _project(make_project)

    assert run_lictor(root, Agent(impl=good_impl, refactor=refactor)) == Outcome(0)

    assert green_ids(root) == ALL
    assert _outcomes(root) == dict.fromkeys(ALL, ("applied", None))
    assert (root / OLD_PATH).read_text(encoding="utf-8") == OLD
    assert false_greens(root) == []
