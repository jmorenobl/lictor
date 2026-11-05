"""0.1.1: findings of the greenfield pass `romanos` (`.audits/romanos/fricciones.md`).

F13: `lictor status` while a run works must change nothing the run guards. It used to open the
checkpoint for writing, and closing it moved the WAL into the main file: the attempt in progress
failed at `L0-estado` and was spent, with nobody to blame.
"""

import subprocess
import sys
from pathlib import Path

from lictor.domain.model import AgentStatus, AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    SUBPROCESS_TIMEOUT_S,
    Agent,
    Outcome,
    events_of,
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
