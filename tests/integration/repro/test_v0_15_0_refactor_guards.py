"""F71 and F72: the refactor's guards no longer throw away improvements of adopted code.

With the real profile and `uv`. F72: `L0-supresiones` lets an implementer or a refactor move or join
the suppressions that were there, and still refuses one more, or one whose text changed. F71: the
refactor's prompt says how many lines and branches its green left without coverage, and the
profile's coverage measure says it in its last line, with the green's.
"""

import re
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
    all_controls,
    events_of,
    false_greens,
    git,
    good_impl,
    green_ids,
    require,
    run_lictor,
    shell,
    with_refactor,
    write,
)

ALL = ["IVA-001", "IVA-002", "IVA-003"]
# Three functions that import inside a block, each with its suppression, as the adoption pass had.
THREE = "".join(
    f"\n\ndef _serializa_{n}(x: object) -> str:\n"
    "    if x is not None:\n"
    "        from json import dumps  # noqa: PLC0415\n\n"
    "        return dumps(x)\n"
    '    return ""\n'
    for n in (1, 2, 3)
)
# The same, written once and less indented, as the refactor wrote it: one suppression for three.
ONE = (
    "\n\ndef _serializa(x: object) -> str:\n"
    "    from json import dumps  # noqa: PLC0415\n\n"
    '    return dumps(x) if x is not None else ""\n'
    + "".join(f"\n\n_serializa_{n} = _serializa\n" for n in (1, 2, 3))
)


UTIL = "src/iva/util.py"


def _adopted(make_project: ProjectFactory) -> Path:
    """A project whose code already had the three suppressions, as adopted code has."""
    root = make_project()
    write(root, UTIL, '"""Utilidades."""\n' + THREE)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: código que ya estaba")
    return root


def _outcomes(root: Path) -> dict[str, tuple[str, str | None]]:
    return {
        e["scenarioid"]: (e["data"]["outcome"], e["data"]["failed_at"])
        for e in events_of(root, "scenario.refactored")
    }


def _with(task: AgentTask, scenario_id: str, extra: str) -> None:
    write(task.cwd, MODULE_PATH, GOOD_BY_SCENARIO[scenario_id] + extra)


@with_refactor
def test_a_refactor_that_joins_three_suppressions_into_one_is_applied(
    make_project: ProjectFactory,
) -> None:
    """F72: the refactor of the adoption pass that joined three `noqa` into one."""
    root = _adopted(make_project)

    def refactor(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id == "IVA-001":
            write(task.cwd, UTIL, '"""Utilidades."""\n' + ONE)

    assert run_lictor(root, Agent(impl=good_impl, refactor=refactor)) == Outcome(0)

    assert green_ids(root) == ALL
    assert _outcomes(root)["IVA-001"] == ("applied", None)
    assert (root / UTIL).read_text(encoding="utf-8").count("noqa") == 1
    assert false_greens(root) == []


@with_refactor
def test_a_refactor_that_moves_a_suppressed_function_to_a_new_module_is_applied(
    make_project: ProjectFactory,
) -> None:
    """F72: a module per responsibility, as the profile asks, takes the suppressions with it."""
    root = _adopted(make_project)

    def refactor(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id != "IVA-001":
            return
        write(task.cwd, "src/iva/serializa.py", '"""Serializa."""\n' + THREE)
        (task.cwd / UTIL).unlink()

    assert run_lictor(root, Agent(impl=good_impl, refactor=refactor)) == Outcome(0)

    assert _outcomes(root)["IVA-001"] == ("applied", None)
    assert (root / "src" / "iva" / "serializa.py").read_text(encoding="utf-8").count("noqa") == 3
    assert false_greens(root) == []


@with_refactor
def test_a_refactor_that_widens_a_suppression_is_discarded(make_project: ProjectFactory) -> None:
    root = _adopted(make_project)
    wide = THREE.replace("# noqa: PLC0415", "# noqa", 1)

    def refactor(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id == "IVA-001":
            write(task.cwd, UTIL, '"""Utilidades."""\n' + wide)

    assert run_lictor(root, Agent(impl=good_impl, refactor=refactor)) == Outcome(0)

    assert _outcomes(root)["IVA-001"] == ("discarded", "L0-supresiones")
    reasons = [
        path.read_text(encoding="utf-8")
        for path in (root / ".lictor" / "runs").rglob("*-refactor-verify*/error.txt")
    ]
    assert any("# noqa" in reason for reason in reasons)
    assert false_greens(root) == []


UNIT_TEST = (
    "from iva import general\n\n\ndef test_general() -> None:\n    assert general(100) == 121\n"
)
UNCOVERED = (
    "\n\ndef nobody_calls_me(x: int) -> int:\n    if x > 0:\n        return x\n    return -x\n"
)


def _add_pytest_cov(root: Path) -> None:
    added = shell(["uv", "add", "-q", "--dev", "--offline", "pytest-cov"], root)
    if added.returncode != 0:
        added = shell(["uv", "add", "-q", "--dev", "pytest-cov"], root)
    require(added.returncode == 0, f"uv add pytest-cov falló: {added.stderr}")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: pytest-cov")


def _measure_command(prompt: str) -> list[str]:
    """The coverage command the prompt gives, as the agent reads it."""
    line = next(line for line in prompt.splitlines() if line.startswith("  - coverage: `"))
    return line.split("`")[1].split()


@all_controls
@with_refactor
def test_the_refactor_hears_what_its_green_left_without_coverage_and_measures_the_same(
    make_project: ProjectFactory,
) -> None:
    """F71: the green's counts in the prompt, and the profile's measure ends with the same."""
    root = make_project(sync=False)
    _add_pytest_cov(root)
    measured: list[str] = []

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        _with(task, scenario_id, UNCOVERED if scenario_id == "IVA-001" else "")
        write(task.cwd, "tests/unit/test_iva.py", UNIT_TEST)

    def refactor(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id != "IVA-001":
            return
        command = _measure_command(task.prompt)
        require(command[:4] == ["uv", "run", "python", "-I"], f"otra orden: {command}")
        done = subprocess.run(
            [sys.executable, *command[3:]],
            cwd=task.cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        measured.append(done.stdout)

    assert run_lictor(root, Agent(impl=impl, refactor=refactor)) == Outcome(0)

    prompts = [
        path.read_text(encoding="utf-8")
        for path in (root / ".lictor" / "runs").rglob("IVA-001/*-refactor.prompt.md")
    ]
    [prompt] = prompts
    line = next(line for line in prompt.splitlines() if line.startswith("  - coverage: `"))
    told = re.search(r"El verde dejaba (\d+) líneas y (\d+) ramas sin cubrir", line)
    require(told is not None, f"el prompt no da lo que dejaba el verde: {line}")
    assert told is not None
    lines, branches = told.groups()
    assert int(lines) > 0
    [output] = measured
    last = output.strip().splitlines()[-1]
    # Nothing changed: the measure counts what Lictor counted for the green, and says it.
    assert last == f"{lines} líneas y {branches} ramas sin cubrir; el verde, {lines} y {branches}."
    assert false_greens(root) == []
