"""Phase 7: a ✓ of `lictor check` means what a ✓ of `lictor run` means."""

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lictor.application.run_files import exclusive_run
from lictor.cli import app
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import all_controls, git, require, smoke, write
from tests.integration.repro.test_phase4_environment import PERMISSIVE_RUFF, _user_home_with

LOCAL_VENV = {
    "id": "venv-local",
    "level": "L4",
    "run": "test -f .venv/pyvenv.cfg",
    "report": "exit-code",
    "mode": "block",
}


def _check(root: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["check", "--cwd", str(root), *args])
    return result.exit_code, result.output


def test_check_doesnt_pass_on_what_only_the_working_tree_has(
    make_project: ProjectFactory,
) -> None:
    root = make_project(extra_controls=[LOCAL_VENV])
    require((root / ".venv" / "pyvenv.cfg").is_file(), "la copia no tiene su .venv")

    code, output = _check(root, "--level", "L4")

    assert code == 10
    assert "✗ venv-local" in output
    assert f"Verificado: HEAD {git(root, 'rev-parse', 'HEAD')[:12]}" in output


@all_controls
def test_check_ignores_the_users_configuration(
    make_project: ProjectFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project()
    write(root, "src/iva/extra.py", "import os\n")
    _user_home_with(tmp_path, monkeypatch, {".config/ruff/ruff.toml": PERMISSIVE_RUFF})

    code, output = _check(root, "--level", "L1")

    assert code == 10
    assert "✗ lint" in output
    assert "árbol de trabajo (1 ficheros sin confirmar)" in output
    assert git(root, "status", "--short").strip() == "?? src/iva/extra.py"


@smoke
def test_check_passes_and_filters_by_level_and_id(make_project: ProjectFactory) -> None:
    root = make_project()

    by_level = _check(root, "--level", "L1")
    by_id = _check(root, "--control", "types")

    assert by_level[0] == 0, by_level[1]
    assert "✓ lint" in by_level[1]
    assert "✓ types" in by_level[1]
    assert by_id[0] == 0, by_id[1]
    assert "✓ types" in by_id[1]
    assert "lint" not in by_id[1]


def test_check_keeps_going_and_shows_where_each_failure_left_its_output(
    make_project: ProjectFactory,
) -> None:
    failing = {"id": "medida", "level": "L5", "run": "false", "report": "exit-code"}
    root = make_project(extra_controls=[failing, {**LOCAL_VENV, "level": "L5"}])

    code, output = _check(root, "--level", "L5", "--keep-going")

    assert code == 10
    assert "✗ medida" in output
    assert "✗ venv-local" in output
    assert "salida: .lictor/check/medida.log" in output


def test_check_prints_the_mutation_score(make_project: ProjectFactory, tmp_path: Path) -> None:
    script = tmp_path / "puntua.py"
    stats = {"killed": 9, "survived": 1, "total": 10}
    script.write_text(
        f"import json, sys\njson.dump({stats!r}, open(sys.argv[1], 'w'))\n", encoding="utf-8"
    )
    mutation = {
        "id": "mutation",
        "level": "L5",
        # -B: inside Lictor's own verification this interpreter belongs to its hashed environment.
        "run": f"{sys.executable} -I -B {script} {{out}}",
        "report": "mutation",
        "mode": "measure",
    }
    root = make_project(extra_controls=[mutation])

    code, output = _check(root, "--level", "L5")

    assert code == 0, output
    assert "✓ mutation" in output
    assert ": 90,0 % (9 de 10 muertos; 1 vivos, 0 por tiempo)" in output


def test_check_waits_for_no_run(make_project: ProjectFactory) -> None:
    root = make_project()

    with exclusive_run(root, "lictor run"):
        code, output = _check(root)

    assert code == 10
    assert "lictor check" in output
