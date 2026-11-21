"""0.13.0 (ADR-0013, F60): the refactor sees the complexity its green left, and can't raise it.

With the real profile and `uv`: the `complexity` control measures every verification without
blocking; the refactor's prompt names the functions its green left above the limit, wherever they
are, with a command to measure them; and a refactor that leaves more above the limit than its
green is discarded at `L0-regresión`, with the green kept.
"""

import subprocess
import sys
from pathlib import Path

from lictor.application.ports import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    GOOD_BY_SCENARIO,
    MODULE_PATH,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_impl,
    green_ids,
    run_lictor,
    with_refactor,
    write,
)

ALL = ["IVA-001", "IVA-002", "IVA-003"]
# Complexity 12 with Lictor's definition: 6 `if`, 5 operands after the first and a ternary.
COMPLEX = '''

def _clasifica(a: bool, b: bool, c: bool, d: bool) -> int:
    """Una función con muchas ramas."""
    if a and b and c:
        return 1
    if a or d:
        return 2
    if b:
        return 3
    if c and d:
        return 4
    if a and d:
        return 7
    return 5 if d else 6
'''
SIMPLE = '''

def _clasifica(a: bool, b: bool, c: bool, d: bool) -> int:
    """La misma función, sin ramas que nadie usa."""
    return 1 if a else 2
'''


def _outcomes(root: Path) -> dict[str, tuple[str, str | None]]:
    return {
        e["scenarioid"]: (e["data"]["outcome"], e["data"]["failed_at"])
        for e in events_of(root, "scenario.refactored")
    }


def _with(task: AgentTask, scenario_id: str, extra: str) -> None:
    write(task.cwd, MODULE_PATH, GOOD_BY_SCENARIO[scenario_id] + extra)


def _measure_command(prompt: str) -> str:
    """The command the prompt gives to measure complexity, as the agent would read it."""
    line = next(line for line in prompt.splitlines() if line.startswith("Mídela tú"))
    return line.split("`")[1]


@with_refactor
def test_the_refactor_hears_what_its_green_left_and_lowering_it_is_applied(
    make_project: ProjectFactory,
) -> None:
    measured: list[str] = []

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        _with(task, scenario_id, COMPLEX if scenario_id == "IVA-001" else "")

    def refactor(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id != "IVA-001":
            return
        # The command the prompt names measures the copy with the profile's own module.
        script = _measure_command(task.prompt).split()[-2]
        done = subprocess.run(
            [sys.executable, "-I", "-B", script, "src"],
            cwd=task.cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        measured.append(done.stdout)
        _with(task, scenario_id, SIMPLE)

    root = make_project()
    agent = Agent(impl=impl, refactor=refactor)

    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ALL
    [first] = agent.prompts["refactor:IVA-001"]
    assert "- src/iva/__init__.py:" in first
    assert "`_clasifica`: 12" in first
    assert "Baja al menos una" in first
    assert "{lictor_files}" not in first
    assert any(
        "_clasifica" in line and line.lstrip().startswith("12") for line in measured[0].splitlines()
    )
    assert _outcomes(root)["IVA-001"] == ("applied", None)
    [second] = agent.prompts["refactor:IVA-002"]
    assert "- Ninguna función supera el límite." in second
    assert false_greens(root) == []


@with_refactor
def test_a_refactor_that_leaves_more_complexity_is_discarded_and_the_green_stays(
    make_project: ProjectFactory,
) -> None:
    def refactor(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        _with(task, scenario_id, COMPLEX)

    root = make_project()

    assert run_lictor(root, Agent(impl=good_impl, refactor=refactor)) == Outcome(0)

    assert green_ids(root) == ALL
    assert _outcomes(root) == dict.fromkeys(ALL, ("discarded", "L0-regresión"))
    assert "_clasifica" not in (root / MODULE_PATH).read_text(encoding="utf-8")
    reasons = [
        line
        for path in (root / ".lictor" / "runs").rglob("*-refactor-verify*/error.txt")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any("complejidad por encima del límite" in line for line in reasons)
    assert not git(root, "log", "--format=%s", "--grep", "^refactor(")
    assert false_greens(root) == []


def test_every_verification_measures_complexity_without_blocking(
    make_project: ProjectFactory,
) -> None:
    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        _with(task, scenario_id, COMPLEX)

    root = make_project()

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    assert green_ids(root) == ALL
    finished = [e for e in events_of(root, "verify.finished") if e["data"].get("controls")]
    complexity = [e["data"]["controls"]["complexity"] for e in finished]
    assert complexity
    assert all(c["mode"] == "measure" and not c["passed"] for c in complexity)
    assert complexity[-1]["complexity"] == {"limit": 10, "over": 1, "excess": 2, "peak": 2}
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "1 función supera 10 (la peor, 12)" in report
