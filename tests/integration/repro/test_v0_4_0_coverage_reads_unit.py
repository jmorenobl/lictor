"""ADR-0008 (F46): `coverage` scores the run of `unit`, which runs the unit tests once.

With the real `python-uv` profile, `uv` and `pytest-cov`: `unit` gets the arguments of
`coverage` when its plugin is there, `coverage` runs nothing, and its score is the one a run of
its own would give. Every case needs the controls the light project drops: without them `unit`
and `coverage` wouldn't run, and an absence would hold because nothing ran.
"""

import json
import shlex
from pathlib import Path

from typer.testing import CliRunner

from lictor.cli import app
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    all_controls,
    git,
    require,
    run_lictor,
    shell,
    smoke,
    write,
)

UNIT_TEST = "from iva import hello\n\n\ndef test_hello() -> None:\n    assert hello()\n"
UNCOVERED = (
    'def hello() -> str:\n    return "Hello from iva!"\n\n\n'
    "def nobody_calls_me(x: int) -> int:\n    if x > 0:\n        return x\n    return -x\n"
)
LENT = "unit (con los argumentos de coverage, que solo corre en check)"


def _add_pytest_cov(root: Path) -> None:
    added = shell(["uv", "add", "-q", "--dev", "--offline", "pytest-cov"], root)
    if added.returncode != 0:
        added = shell(["uv", "add", "-q", "--dev", "pytest-cov"], root)
    require(added.returncode == 0, f"uv add pytest-cov falló: {added.stderr}")


def _commit(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def _check(root: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["check", "--cwd", str(root), *args])
    return result.exit_code, result.output


def _command(log: Path) -> list[str]:
    """The command a log says ran: every log starts with it (ADR-0009)."""
    first = log.read_text(encoding="utf-8").splitlines()[0]
    require(first.startswith("Comando: "), f"{log} no empieza por su comando")
    return shlex.split(first.removeprefix("Comando: "))


def _runs_of_the_unit_tests(evidence: Path) -> list[Path]:
    return [log for log in sorted(evidence.rglob("*.log")) if "tests/unit" in _command(log)]


@smoke
def test_unit_runs_once_and_coverage_scores_that_run(make_project: ProjectFactory) -> None:
    root = make_project(sync=False)
    write(root, "src/iva/__init__.py", UNCOVERED)
    write(root, "tests/unit/test_hello.py", UNIT_TEST)
    _add_pytest_cov(root)
    _commit(root, "test: unitarios con cobertura")

    code, output = _check(root, "--level", "L2")

    assert code == 0, output
    evidence = root / ".lictor" / "check"
    (unit_log,) = _runs_of_the_unit_tests(evidence)
    assert unit_log.name == "unit.log"
    assert "--cov=src" in _command(unit_log)
    # coverage ran nothing: it has no log of its own, and points to unit's.
    assert not (evidence / "coverage.log").exists()
    assert "✓ coverage lee unit: 37,5 % (3 de 6 líneas, 0 de 2 ramas)" in output

    # The score two runs gave, with coverage running the unit tests again, is the same.
    again = root.parent / "again.json"
    report = f"--cov-report=json:{again}"
    alone = shell(
        ["uv", "run", "pytest", "tests/unit", "-q", "--cov=src", "--cov-branch", report], root
    )
    require(alone.returncode == 0, f"la cobertura aparte falló: {alone.stdout}{alone.stderr}")
    totals = json.loads(again.read_text(encoding="utf-8"))["totals"]
    assert (totals["covered_lines"], totals["num_statements"]) == (3, 6)
    assert (totals["covered_branches"], totals["num_branches"]) == (0, 2)


@all_controls
def test_the_projects_fail_under_doesnt_fail_unit(make_project: ProjectFactory) -> None:
    """A threshold nobody wrote in lictor.json would block with no test in red."""
    root = make_project(sync=False)
    write(root, "src/iva/__init__.py", UNCOVERED)
    write(root, "tests/unit/test_hello.py", UNIT_TEST)
    _add_pytest_cov(root)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8") + "\n[tool.coverage.report]\nfail_under = 99\n",
        encoding="utf-8",
    )
    _commit(root, "test: unitarios con un umbral propio")

    code, output = _check(root, "--control", "unit")

    assert code == 0, output
    assert "✓ unit" in output
    assert "--cov-fail-under=0" in _command(root / ".lictor" / "check" / "unit.log")


@all_controls
def test_without_pytest_cov_unit_runs_as_it_is_and_coverage_is_skipped(
    make_project: ProjectFactory,
) -> None:
    root = make_project(sync=False)
    write(root, "tests/unit/test_hello.py", UNIT_TEST)
    _commit(root, "test: unitarios sin cobertura")

    code, output = _check(root, "--level", "L2")

    assert code == 0, output
    assert "✓ unit" in output
    assert (
        "- coverage omitido: El entorno de verificación no tiene el plugin pytest_cov.plugin"
        in (output)
    )
    command = _command(root / ".lictor" / "check" / "unit.log")
    assert not any(token.startswith("--cov") for token in command)


@all_controls
def test_coverage_only_in_check_measures_there_and_never_in_the_run(
    make_project: ProjectFactory,
) -> None:
    """With coverage in `check`, unit runs as it is in each attempt and with it in `check`."""
    root = make_project(
        sync=False, config_changes={"controls": [{"id": "coverage", "stage": "check"}]}
    )
    _add_pytest_cov(root)
    _commit(root, "chore: pytest-cov")

    assert run_lictor(root, Agent()) == Outcome(0)

    runs = root / ".lictor" / "runs"
    in_the_run = _runs_of_the_unit_tests(runs)
    require(in_the_run, "el run no ejecutó unit")
    for log in in_the_run:
        assert not any(token.startswith("--cov") for token in _command(log)), log

    code, output = _check(root, "--level", "L2")

    assert code == 0, output
    assert "--cov=src" in _command(root / ".lictor" / "check" / "unit.log")
    assert f"✓ {LENT}" in output
    assert "✓ coverage (solo en check) lee unit:" in output
