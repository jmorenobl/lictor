"""0.1.1: findings of the greenfield pass `romanos` (`.audits/romanos/fricciones.md`).

F13: `lictor status` while a run works must change nothing the run guards. It used to open the
checkpoint for writing, and closing it moved the WAL into the main file: the attempt in progress
failed at `L0-estado` and was spent, with nobody to blame.

F09: in a repository without a single commit, `doctor` and `check` ended in «Error interno» with
git's trace and exit 1, and `run` only spoke of uncommitted changes.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from lictor.cli import app
from lictor.domain.model import AgentStatus, AgentTask
from lictor.domain.rules import NO_COMMITS
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    SUBPROCESS_TIMEOUT_S,
    Agent,
    Outcome,
    events_of,
    git,
    good_impl,
    green_ids,
    require,
    run_lictor,
)

LICTOR = [sys.executable, "-c", "from lictor.cli import app; app()"]


def _lictor(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI in a process of its own, as a person in another terminal would."""
    return subprocess.run(
        [*LICTOR, *args, "--cwd", str(root)],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )


def test_f13_status_and_report_during_an_attempt_spend_nothing(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    looked: list[subprocess.CompletedProcess[str]] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if scenario_id == "IVA-001" and attempt == 1:
            require((root / ".lictor" / "checkpoints.sqlite").is_file(), "no hay checkpoint")
            looked.append(_lictor(root, "status"))
            looked.append(_lictor(root, "status", "--json"))
            looked.append(_lictor(root, "report"))
        return good_impl(task, scenario_id, attempt)

    agent = Agent(impl=impl)

    assert run_lictor(root, agent) == Outcome(0)

    assert [result.returncode for result in looked] == [0, 0, 0], [r.stderr for r in looked]
    assert "Ejecución:" in looked[0].stdout
    assert not events_of(root, "state.tampered")
    assert [e["data"]["failed_at"] for e in events_of(root, "verify.finished")] == [None] * 3
    assert agent.calls == ["driver", "IVA-001", "IVA-002", "IVA-003"]
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]


def test_f13_status_without_a_run_creates_no_checkpoint(make_project: ProjectFactory) -> None:
    root = make_project()

    result = _lictor(root, "status")

    assert result.returncode == 0, result.stderr
    assert "No hay ninguna ejecución" in result.stdout
    assert not (root / ".lictor" / "checkpoints.sqlite").exists()


def _without_commits(root: Path) -> Path:
    """The same project, as it stands before its first commit."""
    shutil.rmtree(root / ".git")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    return root


def test_f09_doctor_without_commits_is_one_more_failed_check(make_project: ProjectFactory) -> None:
    root = _without_commits(make_project())

    result = CliRunner().invoke(app, ["doctor", "--cwd", str(root)])

    assert result.exit_code == 10
    assert f"✗ Repositorio con commits: {NO_COMMITS}" in result.output
    assert "- Entorno de verificación: no se construye: no hay ningún commit" in result.output
    assert "Error interno" not in result.output
    assert "rev-parse" not in result.output


def test_f09_check_without_commits_exits_10(make_project: ProjectFactory) -> None:
    root = _without_commits(make_project())

    result = CliRunner().invoke(app, ["check", "--cwd", str(root)])

    assert result.exit_code == 10
    assert NO_COMMITS in result.output
    assert "Error interno" not in result.output


def test_f09_run_without_commits_says_so(make_project: ProjectFactory) -> None:
    root = _without_commits(make_project())
    agent = Agent()

    assert run_lictor(root, agent) == Outcome(10)

    assert agent.calls == []
    reason = events_of(root, "run.finished")[-1]["data"]["reason"]
    assert NO_COMMITS in reason
