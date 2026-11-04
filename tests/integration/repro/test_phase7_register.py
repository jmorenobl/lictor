"""Phase 7: adopting Lictor in a project whose code already meets some scenarios."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lictor.cli import app
from lictor.domain.model import AgentStatus, AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    GOOD_1,
    MODULE_PATH,
    UNIT_TEST,
    Agent,
    events_of,
    false_greens,
    git,
    good_impl,
    green_ids,
    run_lictor,
    write,
)


def _register(root: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["register", "--cwd", str(root), *args])
    return result.exit_code, result.output


def _with_iva_001_done(make_project: ProjectFactory) -> Path:
    """demo-iva whose code already meets IVA-001: the others are still stubs."""
    root = make_project()
    write(root, MODULE_PATH, GOOD_1)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "feat: IVA general, antes de Lictor")
    return root


def _driver_over_existing_code(task: AgentTask, _attempt: int = 1) -> None:
    """A driver for a project with code: tests and nothing else, the code stays as it is."""
    write(task.cwd, DRIVER_PATH, DRIVER)
    write(task.cwd, "tests/unit/test_smoke.py", UNIT_TEST)


def test_a_registered_scenario_is_green_by_hand_and_the_rest_by_lictor(
    make_project: ProjectFactory,
) -> None:
    root = _with_iva_001_done(make_project)

    code, output = _register(root, "IVA-001", "--yes")

    assert code == 0, output
    assert "Afirmas que el código de HEAD ya cumple IVA-001" in output
    message = git(root, "log", "-1", "--format=%B")
    assert "Scenario: IVA-001" in message
    assert "Lictor-Asserted-By: Test User <test@example.com>" in message
    assert "Lictor-Run" not in message

    agent = Agent(driver=_driver_over_existing_code)
    outcome = run_lictor(root, agent)

    assert outcome.exit_code == 0
    assert agent.calls == ["driver", "IVA-002", "IVA-003"]
    assert sorted(green_ids(root)) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "- **IVA-001**: registrado a mano en" in report
    assert "por Test User <test@example.com>; Lictor no lo ha probado" in report
    assert "La marca es informativa" in report
    status = CliRunner().invoke(app, ["status", "--cwd", str(root)])
    assert "IVA-001: green (registrado a mano)" in status.output
    assert "IVA-002: green\n" in status.output


def test_without_registering_the_same_project_stops_for_a_person(
    make_project: ProjectFactory,
) -> None:
    root = _with_iva_001_done(make_project)
    agent = Agent(driver=_driver_over_existing_code)

    outcome = run_lictor(root, agent)

    assert outcome.exit_code == 10
    assert agent.calls == ["driver", "driver"]
    reason = events_of(root, "run.finished")[0]["data"]["reason"]
    assert "`lictor register IVA-001`" in reason


@pytest.mark.parametrize(
    ("ids", "expected"),
    [
        (["IVA-404"], "No existe el escenario IVA-404"),
        (["IVA-001", "IVA-404"], "No existe el escenario IVA-404"),
    ],
)
def test_registering_is_all_or_nothing(
    make_project: ProjectFactory, ids: list[str], expected: str
) -> None:
    root = make_project()
    head = git(root, "rev-parse", "HEAD")

    code, output = _register(root, *ids, "--yes")

    assert code == 10
    assert expected in output
    assert git(root, "rev-parse", "HEAD") == head


def test_a_green_scenario_isnt_registered_again(make_project: ProjectFactory) -> None:
    root = make_project()
    assert _register(root, "IVA-001", "--yes")[0] == 0

    code, output = _register(root, "IVA-001", "--yes")

    assert code == 10
    assert "IVA-001 ya está en verde" in output


def test_without_a_terminal_nothing_is_asserted_on_anyones_behalf(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    head = git(root, "rev-parse", "HEAD")

    code, output = _register(root, "IVA-001")

    assert code == 10
    assert "--yes" in output
    assert git(root, "rev-parse", "HEAD") == head


def test_registering_needs_a_clean_tree_and_an_identity(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project()
    write(root, "src/iva/nuevo.py", "x = 1\n")

    assert _register(root, "IVA-001", "--yes")[0] == 10

    (root / "src" / "iva" / "nuevo.py").unlink()
    monkeypatch.setenv("GIT_COMMITTER_NAME", "")

    code, output = _register(root, "IVA-001", "--yes")

    assert code == 40
    assert "git no tiene identidad para los commits que hace Lictor: fatal: " in output


def test_a_run_halfway_keeps_its_branch(make_project: ProjectFactory) -> None:
    root = make_project()
    limited = [True]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if limited.pop() if limited else False:
            return "rate_limited"
        return good_impl(task, scenario_id, attempt)

    assert run_lictor(root, Agent(impl=impl)).exit_code == 50
    head = git(root, "rev-parse", "HEAD")

    code, output = _register(root, "IVA-002", "--yes")

    assert code == 10
    assert "ejecución a medias" in output
    assert git(root, "rev-parse", "HEAD") == head
