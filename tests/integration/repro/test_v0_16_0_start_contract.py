"""ADR-0015: before a run, `doctor` sees what is missing and what the controls don't see.

With the real profile and `uv`. F66: a project without `pytest-bdd` stops in `doctor` and before
the driver, with exit 10 and what to add. F67 and F70: a `conftest.py` that imports the code and
that the agent can't change, tests no control runs and code no control reads are told by `doctor`
and when a run starts, without stopping anything; what the person accepts is only counted.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from lictor.cli import app
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import Agent, events_of, git, require, run_lictor, shell, write

ADOPTED = {
    "tests/conftest.py": (
        "import pytest\n\nfrom iva import hello\n\n\n"
        "@pytest.fixture\ndef iva():\n    return hello\n"
    ),
    "tests/integration/test_legacy.py": "import iva\n\n\ndef test_legacy():\n    assert iva\n",
    "examples/uso.py": "from iva import hello\n\nprint(hello)\n",
}


def _doctor(root: Path) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["doctor", "--cwd", str(root)])
    return result.exit_code, result.output


def _commit(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def test_f66_a_project_without_the_acceptances_plugin_stops_before_the_driver(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    shell(["uv", "remove", "--dev", "pytest-bdd", "--offline", "--no-sync"], root)
    require("pytest-bdd" not in (root / "pyproject.toml").read_text(), "pytest-bdd sigue ahí")
    _commit(root, "chore: sin pytest-bdd")

    code, output = _doctor(root)

    assert code == 10
    assert "✗ Dependencias de la verificación" in output
    assert "pytest_bdd.plugin" in output
    assert "uv add --dev pytest-bdd" in output

    agent = Agent()
    outcome = run_lictor(root, agent)

    assert outcome.exit_code == 10
    assert agent.calls == []
    assert events_of(root, "agent.started") == []
    (finished,) = events_of(root, "run.finished")
    assert "pytest_bdd.plugin" in finished["data"]["reason"]


def test_f67_f70_what_the_controls_dont_see_is_told_and_stops_nothing(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    for rel, text in ADOPTED.items():
        write(root, rel, text)
    _commit(root, "chore: código adoptado")

    code, output = _doctor(root)

    assert code == 0, output
    assert "✓ Dependencias de la verificación" in output
    spots = [line for line in output.splitlines() if "Lo que los controles no ven" in line]
    assert any(
        "Tests que ningún control ejecuta: tests/integration/test_legacy.py" in s for s in spots
    )
    assert any("tests/conftest.py (línea 3: hello)" in s for s in spots)
    assert any(
        "Código que usa el proyecto y que ningún control lee: examples/uso.py" in s for s in spots
    )

    config = json.loads((root / "lictor.json").read_text(encoding="utf-8"))
    config["blind_spots"] = {"accepted": ["tests/", "examples/"]}
    (root / "lictor.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    _commit(root, "chore: lo aceptado")

    code, output = _doctor(root)

    assert code == 0, output
    spots = [line for line in output.splitlines() if "Lo que los controles no ven" in line]
    assert spots == [
        "⚠ Lo que los controles no ven: Aceptados en lictor.json (blind_spots.accepted): 3 "
        "ficheros."
    ]


def test_a_new_run_records_what_its_controls_dont_see(make_project: ProjectFactory) -> None:
    root = make_project()
    write(root, "examples/uso.py", ADOPTED["examples/uso.py"])
    _commit(root, "chore: un ejemplo")

    run_lictor(root, Agent())

    (started,) = events_of(root, "run.started")
    recorded = started["data"]["blind_spots"]
    assert [s["kind"] for s in recorded["spots"]] == ["sin_lector"]
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    section = report.split("## Lo que los controles no ven")[1].split("##")[0]
    assert "examples/uso.py" in section
    assert (root / ".lictor" / "runs" / started["runid"] / "start" / "survey.json").is_file()
