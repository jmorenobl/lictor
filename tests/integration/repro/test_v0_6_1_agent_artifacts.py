"""0.6.1 (F59): what the agent's own commands generate never reaches the candidate.

The 0.6.0 told the implementer to measure coverage with the profile's `agent_run`. pytest-cov
leaves `.coverage` at the root of the copy, the `.gitignore` that `lictor init` wrote didn't name
it, so `capture` took it into the tree: `L0-rutas` discarded it twice and the run stopped at the
first scenario saying a person had to change `.coverage`. The fake agent never ran the command,
so no test saw it. These cases run the commands for real.
"""

import re
import shlex
from pathlib import Path

from lictor.adapters.config.loader import load_config
from lictor.application.ports import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    ReproSetupError,
    git,
    good_impl,
    green_ids,
    require,
    run_lictor,
    shell,
    write,
)

# A minimum, so the prompt names the command (`agent_measures`); low, so any implementation
# meets it.
COVERAGE = {"id": "coverage", "mode": "block", "min_score": 1}
MEASURE = re.compile(r"coverage: al menos [^,]+, con `([^`]+)`")


def _with_pytest_cov(root: Path) -> None:
    added = shell(["uv", "add", "-q", "--dev", "--offline", "pytest-cov"], root)
    if added.returncode != 0:
        added = shell(["uv", "add", "-q", "--dev", "pytest-cov"], root)
    require(added.returncode == 0, f"uv add pytest-cov falló: {added.stderr}")
    write(root, "tests/unit/test_smoke.py", "import iva\n\n\ndef test_ok():\n    assert iva\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "test: unitarios con cobertura")


def _measuring_impl(task: AgentTask, scenario_id: str, attempt: int = 1) -> None:
    """Measure as the prompt says, in the copy, and then implement."""
    found = MEASURE.search(task.prompt)
    if found is None:
        raise ReproSetupError("el prompt no dice cómo medir la cobertura")
    measured = shell(shlex.split(found.group(1)), task.cwd)
    require(measured.returncode == 0, f"la medida falló: {measured.stdout}{measured.stderr}")
    left = (task.cwd / ".coverage").exists()
    require(left, "la medida no dejó .coverage: el caso no prueba nada")
    good_impl(task, scenario_id, attempt)


def test_the_coverage_the_agent_measures_stays_out_of_its_change(
    make_project: ProjectFactory,
) -> None:
    root = make_project(sync=False, config_changes={"controls": [COVERAGE]})
    _with_pytest_cov(root)
    # A project whose `.gitignore` was written by the 0.6.0 init, without `.coverage`.
    ignore = root / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.startswith(".coverage")]
    require(kept != lines, "el .gitignore de init no nombra .coverage")
    ignore.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
    git(root, "commit", "-q", "-am", "chore: .gitignore de la 0.6.0")

    outcome = run_lictor(root, Agent(impl=_measuring_impl))

    assert outcome.exit_code == 0, outcome
    assert set(green_ids(root)) == {"IVA-001", "IVA-002", "IVA-003"}
    assert not git(root, "ls-files", ".coverage*"), "nada de la medida llega a un commit"


def test_the_agents_commands_leave_nothing_init_doesnt_ignore(
    make_project: ProjectFactory,
) -> None:
    root = make_project(sync=False, config_changes={"controls": [COVERAGE]})
    _with_pytest_cov(root)
    config = load_config(root)
    commands = [
        *config.agent_commands,
        *(control.agent_run for control in config.controls if control.agent_run),
    ]
    require(any("--cov" in command for command in commands), "ningún comando mide la cobertura")

    for command in commands:
        shell(shlex.split(command), root)

    assert not git(root, "status", "--short", "--untracked-files=all"), commands
