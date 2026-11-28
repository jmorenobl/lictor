"""S20: `lictor check` before the first run doesn't fail an acceptance that has no driver yet.

A base just set up, with `doctor` green, made `lictor check --keep-going` fail at `acceptance`
(exit 4, `file or directory not found: tests/acceptance`), so a CI with `check` was born red. Now
the acceptance waits while nothing has a driver: no file under `paths.driver` and no scenario ever
green in git. Once either exists it runs; and a shallow clone, which can't see the greens, runs it
as before and says why.
"""

import shutil
from pathlib import Path

from typer.testing import CliRunner, Result

from lictor.cli import app
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import DRIVER_PATH, Agent, Outcome, git, run_lictor


def _check(root: Path, *chosen: str) -> Result:
    chosen = chosen or ("--control", "acceptance")
    return CliRunner().invoke(app, ["check", *chosen, "--cwd", str(root)])


def test_check_before_the_first_run_says_the_acceptance_has_no_driver_yet(
    make_project: ProjectFactory,
) -> None:
    root = make_project()

    result = _check(root)

    assert result.exit_code == 0, result.output
    assert "- acceptance: sin driver todavía" in result.output
    assert "✗ acceptance" not in result.output


def test_a_check_that_doesn_t_choose_the_acceptance_says_nothing_of_it(
    make_project: ProjectFactory,
) -> None:
    root = make_project()

    result = _check(root, "--level", "L2")

    assert "Verificado:" in result.output, result.output
    assert "acceptance" not in result.output


def test_without_scenarios_the_acceptance_is_the_project_s_own_and_runs(
    make_project: ProjectFactory,
) -> None:
    """Lictor's own acceptance is its repro cases: no `features/`, no driver, and it runs."""
    root = make_project()
    shutil.rmtree(root / "features")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: sin escenarios")

    result = _check(root)

    assert "sin driver todavía" not in result.output
    assert "✗ acceptance" in result.output, result.output


def test_once_a_scenario_was_green_a_driver_removed_fails_the_check(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    assert run_lictor(root, Agent()) == Outcome(0)
    shutil.rmtree(root / Path(DRIVER_PATH).parent)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: sin driver")

    result = _check(root)

    assert result.exit_code == 10, result.output
    assert "✗ acceptance" in result.output


def test_a_shallow_clone_runs_it_and_says_why(make_project: ProjectFactory, tmp_path: Path) -> None:
    root = make_project()
    git(root, "commit", "-q", "--allow-empty", "-m", "chore: otro")
    shallow = tmp_path / "superficial"
    git(tmp_path, "clone", "-q", "--depth", "1", root.resolve().as_uri(), str(shallow))

    result = _check(shallow)

    assert "✗ acceptance" in result.output
    assert "clon superficial" in result.output
