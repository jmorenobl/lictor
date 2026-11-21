"""0.14.0 (ADR-0014, F65): the refactor sees the clones its green left, and can't add more.

With the real profile and `uv`: the `duplication` control measures every verification without
blocking; the refactor's prompt names the clones its green left, wherever they are, with a command
to measure them; and a refactor that leaves more tokens in clones than its green is discarded at
`L0-regresión`, with the green kept.
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
_BODY = """
    total = 0
    for e in datos:
        if e is None:
            continue
        total += e * factor
        if total > limite:
            raise ValueError("demasiado")
    return round(-total / len(datos), 2)
"""
# Two functions with the same body: one clone of 50 tokens, 100 in clones.
REPEATED = f"""

def _suma(datos: list[int], factor: int, limite: int) -> float:{_BODY}

def _resta(datos: list[int], factor: int, limite: int) -> float:{_BODY}
"""
# The same, written once.
ONCE = f"""

def _suma(datos: list[int], factor: int, limite: int) -> float:{_BODY}

def _resta(datos: list[int], factor: int, limite: int) -> float:
    return _suma(datos, factor, limite)
"""


def _outcomes(root: Path) -> dict[str, tuple[str, str | None]]:
    return {
        e["scenarioid"]: (e["data"]["outcome"], e["data"]["failed_at"])
        for e in events_of(root, "scenario.refactored")
    }


def _with(task: AgentTask, scenario_id: str, extra: str) -> None:
    write(task.cwd, MODULE_PATH, GOOD_BY_SCENARIO[scenario_id] + extra)


def _measure_command(prompt: str) -> str:
    """The command the prompt gives to measure duplication, as the agent would read it."""
    line = next(line for line in prompt.splitlines() if line.startswith("Mídelo tú"))
    return line.split("`")[1]


@with_refactor
def test_the_refactor_hears_the_clones_its_green_left_and_removing_one_is_applied(
    make_project: ProjectFactory,
) -> None:
    measured: list[str] = []

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        _with(task, scenario_id, REPEATED if scenario_id == "IVA-001" else "")

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
        _with(task, scenario_id, ONCE)

    root = make_project()
    agent = Agent(impl=impl, refactor=refactor)

    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ALL
    [first] = agent.prompts["refactor:IVA-001"]
    clone = next(line for line in first.splitlines() if line.startswith("- src/iva/__init__.py:"))
    assert " y src/iva/__init__.py:" in clone
    assert clone.endswith(": 50 tokens")
    assert "Quita al menos uno" in first
    assert "más tokens en clones que el verde (100)" in first
    assert "{lictor_files}" not in first
    assert measured[0].splitlines()[1].startswith("100 tokens en clones")
    assert _outcomes(root)["IVA-001"] == ("applied", None)
    [second] = agent.prompts["refactor:IVA-002"]
    assert "- Ningún clon." in second
    assert false_greens(root) == []


@with_refactor
def test_a_refactor_that_leaves_more_repeated_code_is_discarded_and_the_green_stays(
    make_project: ProjectFactory,
) -> None:
    def refactor(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        _with(task, scenario_id, REPEATED)

    root = make_project()

    assert run_lictor(root, Agent(impl=good_impl, refactor=refactor)) == Outcome(0)

    assert green_ids(root) == ALL
    assert _outcomes(root) == dict.fromkeys(ALL, ("discarded", "L0-regresión"))
    assert "_suma" not in (root / MODULE_PATH).read_text(encoding="utf-8")
    reasons = [
        line
        for path in (root / ".lictor" / "runs").rglob("*-refactor-verify*/error.txt")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any("duplication: tokens en clones: 100, y en el verde 0" in line for line in reasons)
    assert not git(root, "log", "--format=%s", "--grep", "^refactor(")
    assert false_greens(root) == []


def test_every_verification_measures_duplication_without_blocking(
    make_project: ProjectFactory,
) -> None:
    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        _with(task, scenario_id, REPEATED)

    root = make_project()

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    assert green_ids(root) == ALL
    finished = [e for e in events_of(root, "verify.finished") if e["data"].get("controls")]
    duplication = [e["data"]["controls"]["duplication"] for e in finished]
    assert duplication
    assert all(d["mode"] == "measure" and not d["passed"] for d in duplication)
    assert duplication[-1]["duplication"]["clones"] == 1
    assert duplication[-1]["duplication"]["in_clones"] == 100
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "1 clon, 100 de " in report
