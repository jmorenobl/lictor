"""Phase 7: prerequisites are checked before any agent call."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lictor.application.run_files import exclusive_run
from lictor.cli import app
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import Agent, events_of, git, require, run_lictor, write

MISSING_PROGRAM = {
    "id": "secretos",
    "level": "L4",
    "run": "programa-que-no-existe-xyz --check",
    "report": "exit-code",
    "mode": "block",
}


def _without_identity(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git(root, "config", "--unset", "user.name")
    git(root, "config", "--unset", "user.email")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "")
    monkeypatch.setenv("EMAIL", "")


def _break_prepare(tmp_path: Path) -> None:
    path = tmp_path / "perfiles" / "python-uv.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["verification"]["prepare"] += " --opcion-que-no-existe"
    path.write_text(json.dumps(profile), encoding="utf-8")


def _doctor(root: Path) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["doctor", "--cwd", str(root)])
    return result.exit_code, result.output


def test_e29_missing_git_identity_is_caught_before_the_driver(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project()
    git(root, "config", "--unset", "user.name")
    git(root, "config", "--unset", "user.email")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "")
    monkeypatch.setenv("EMAIL", "")
    agent = Agent()

    run_lictor(root, agent)

    assert agent.calls == []


def test_missing_identity_stops_with_40_and_gits_own_reason(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project()
    _without_identity(root, monkeypatch)

    outcome = run_lictor(root, Agent())

    assert outcome.exit_code == 40
    (finished,) = events_of(root, "run.finished")
    # Ours first, then git's own message, in whatever language git speaks.
    assert finished["data"]["reason"].startswith("git no tiene identidad")
    assert "fatal: " in finished["data"]["reason"]
    # The cheap checks come first: nothing is downloaded for a run that can't commit.
    assert events_of(root, "env.built") == []


def test_a_prepare_that_fails_stops_before_the_driver(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    root = make_project()
    _break_prepare(tmp_path)
    agent = Agent()

    outcome = run_lictor(root, agent)

    assert outcome.exit_code == 40
    assert agent.calls == []
    assert "No se pudo preparar el entorno" in events_of(root, "run.finished")[0]["data"]["reason"]


def test_doctor_passes_on_a_project_from_lictor_init(make_project: ProjectFactory) -> None:
    root = make_project()

    code, output = _doctor(root)

    assert code == 0, output
    assert "✓ Entorno de verificación" in output
    assert not (root / ".lictor" / "verify-env").exists()  # a run's own isn't touched
    assert events_of(root, "env.built") == []  # nor is the runs' journal
    assert (root / ".lictor" / "doctor" / "journal.jsonl").is_file()
    require(not git(root, "status", "--short"), "doctor ensució el árbol")


def test_doctor_reports_every_problem_and_a_missing_program_is_40(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(extra_controls=[MISSING_PROGRAM])
    _without_identity(root, monkeypatch)

    code, output = _doctor(root)

    assert code == 40
    assert "✗ Identidad git" in output
    assert "No se encuentra 'programa-que-no-existe-xyz'" in output
    assert "✓ Entorno de verificación" in output


def test_doctor_reports_a_failing_prepare(make_project: ProjectFactory, tmp_path: Path) -> None:
    root = make_project()
    _break_prepare(tmp_path)

    code, output = _doctor(root)

    assert code == 40
    assert "✗ Entorno de verificación: No se pudo preparar" in output
    assert (root / ".lictor" / "doctor" / "build.log").is_file()


def test_doctor_beside_a_run_checks_everything_else(make_project: ProjectFactory) -> None:
    root = make_project()

    with exclusive_run(root, "lictor run"):
        code, output = _doctor(root)

    assert code == 0, output
    assert "Entorno de verificación: no comprobado: hay una ejecución en curso" in output


def test_doctor_only_warns_of_what_isnt_committed_in_the_persons_checkout(
    make_project: ProjectFactory,
) -> None:
    """`run` from there starts from the last commit in a worktree of its own (0.3.0)."""
    root = make_project()
    write(root, "notas.txt", "a mano\n")

    code, output = _doctor(root)

    assert code == 0, output
    assert "⚠ Árbol git limpio" in output
    assert "notas.txt" in output


def test_doctor_inside_a_linked_worktree_still_needs_it_clean(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    worktree = root.with_name(f"{root.name}-enlazado")
    git(root, "worktree", "add", "-q", "-b", "trabajo", str(worktree))
    write(worktree, "notas.txt", "a mano\n")

    code, output = _doctor(worktree)

    assert code == 10, output
    assert "✗ Árbol git limpio" in output
