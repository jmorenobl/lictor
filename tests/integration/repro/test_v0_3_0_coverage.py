"""0.3.0, phase 5 (F29): the profile's coverage control keeps its percentage as a score.

With the real `python-uv` profile and `pytest-cov`, `lictor check` shows it beside the result, and
a minimum the project asks for by `id` fails the control below it.
"""

from pathlib import Path

from typer.testing import CliRunner

from lictor.cli import app
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import git, require, shell, smoke, write

UNIT_TEST = "from iva import hello\n\n\ndef test_hello() -> None:\n    assert hello()\n"
UNCOVERED = (
    'def hello() -> str:\n    return "Hello from iva!"\n\n\n'
    "def nobody_calls_me(x: int) -> int:\n    if x > 0:\n        return x\n    return -x\n"
)


def _with_unit_tests_and_pytest_cov(root: Path) -> None:
    added = shell(["uv", "add", "-q", "--dev", "--offline", "pytest-cov"], root)
    if added.returncode != 0:
        added = shell(["uv", "add", "-q", "--dev", "pytest-cov"], root)
    require(added.returncode == 0, f"uv add pytest-cov falló: {added.stderr}")
    write(root, "tests/unit/test_hello.py", UNIT_TEST)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "test: unitarios con cobertura")


def _check_coverage(root: Path) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["check", "--cwd", str(root), "--control", "coverage"])
    return result.exit_code, result.output


@smoke
def test_check_shows_the_coverage_as_a_score(make_project: ProjectFactory) -> None:
    root = make_project(sync=False)
    _with_unit_tests_and_pytest_cov(root)

    code, output = _check_coverage(root)

    assert code == 0, output
    assert "✓ coverage" in output
    assert "100,0 % (2 de 2 líneas)" in output


@smoke
def test_a_minimum_coverage_asked_for_by_id_fails_below_it(make_project: ProjectFactory) -> None:
    change = {"id": "coverage", "mode": "block", "min_score": 90}
    root = make_project(sync=False, config_changes={"controls": [change]})
    write(root, "src/iva/__init__.py", UNCOVERED)
    _with_unit_tests_and_pytest_cov(root)

    code, output = _check_coverage(root)

    assert code == 10, output
    assert "✗ coverage" in output
    assert "La cobertura, 37,5 % (3 de 6 líneas, 0 de 2 ramas), no llega al mínimo de 90 %" in (
        output
    )
