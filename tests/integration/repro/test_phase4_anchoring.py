"""Phase 4 (C3, A4, A3): HEAD and the state are anchored; resuming loses nothing."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from lictor.application.usecases.commit_green import CommitGreen
from lictor.domain.model import AgentStatus, AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    MODULE_PATH,
    STUB,
    Agent,
    Outcome,
    error_block,
    events_of,
    false_greens,
    git,
    good_driver,
    good_impl,
    green_ids,
    quarantined,
    require,
    run_lictor,
    shell,
    write,
)

FSMONITOR_HOOK = '#!/bin/sh\necho "{\\"forjado\\":\\"fsmonitor\\"}" >> "$1"\nexit 1\n'
ATTACKER_IDENTITY = ("-c", "user.name=a", "-c", "user.email=a@b")


def _failed_at(root: Path) -> set[str]:
    return {event["data"]["failed_at"] for event in events_of(root, "verify.finished")}


def _require_copy(task: AgentTask, root: Path) -> None:
    """An attack that finds nothing proves something only if the agent really was elsewhere."""
    require(not task.cwd.is_relative_to(root), "el agente debía trabajar fuera del proyecto")
    require(not (task.cwd / ".git").exists(), "la copia no debía tener .git")
    require(not (task.cwd / ".lictor").exists(), "la copia no debía tener .lictor")


def _commit_in_the_project(root: Path, *trailer: str) -> None:
    pyproject = root / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(f'{content}\n[tool.ruff]\nexclude = ["src"]\n', encoding="utf-8")
    shell(["git", "add", "pyproject.toml"], root)
    commit = shell(["git", *ATTACKER_IDENTITY, "commit", "-qm", "chore: x", *trailer], root)
    require(commit.returncode == 0, f"el ataque debía crear el commit: {commit.stderr}")


def test_c5_git_in_the_copy_reaches_no_history(make_project: ProjectFactory) -> None:
    root = make_project()
    refused: list[int] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        _require_copy(task, root)
        good_impl(task, scenario_id, attempt)
        pyproject = task.cwd / "pyproject.toml"
        pyproject.write_text(f"{pyproject.read_text(encoding='utf-8')}\n# x\n", encoding="utf-8")
        refused.append(shell(["git", "add", "pyproject.toml"], task.cwd).returncode)

    run_lictor(root, Agent(impl=impl))

    assert refused
    assert all(refused)
    assert _failed_at(root) == {"L0-rutas"}, "el cambio solo existe como resultado de la copia"


def test_c5_commit_by_absolute_path_is_detected_and_undone(make_project: ProjectFactory) -> None:
    root = make_project()
    moved: list[str] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if not moved:
            head = git(root, "rev-parse", "HEAD").strip()
            _commit_in_the_project(root, "--trailer", "Scenario: IVA-999")
            moved.append(head)

    run_lictor(root, Agent(impl=impl))

    require(moved, "el ataque debía ejecutarse")
    assert "L0-estado" in _failed_at(root)
    assert "exclude" not in git(root, "show", "HEAD:pyproject.toml")
    assert "IVA-999" not in git(root, "log", "--format=%B")
    assert "exclude" not in (root / "pyproject.toml").read_text(encoding="utf-8")
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []


def test_d34_rename_cannot_remove_a_protected_file(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        if scenario_id == "IVA-001":
            shell(["git", "mv", "uv.lock", "src/uv.lock"], task.cwd)

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require("IVA-001" in agent.calls, "el agente debía llegar a implementar")
    assert "uv.lock" in git(root, "ls-tree", "--name-only", "HEAD").splitlines()


def _weak_driver() -> str:
    weak = DRIVER
    for check in ("iva.general(100) == 121", "iva.reducido(100) == 110"):
        weak = weak.replace(f"assert {check}", "assert True")
    return weak.replace("assert iva.negativo(-5) is True", "assert True")


def _forge_seal(root: Path) -> None:
    seal_path = root / ".lictor" / "seal.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256((root / DRIVER_PATH).read_bytes()).hexdigest()
    seal["files"][DRIVER_PATH] = f"sha256:{digest}"
    seal_path.write_text(json.dumps(seal), encoding="utf-8")


def test_d16_weak_driver_in_the_copy_dies_with_it(make_project: ProjectFactory) -> None:
    root = make_project()
    forged = [False]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        if not forged[0]:
            forged[0] = True
            _require_copy(task, root)
            write(task.cwd, DRIVER_PATH, _weak_driver())
            raise KeyboardInterrupt
        good_impl(task, scenario_id, attempt)

    agent = Agent(impl=impl)
    require(run_lictor(root, agent).killed, "el agente debía matar la ejecución")

    assert run_lictor(root, agent) == Outcome(0)
    assert false_greens(root) == []
    assert "assert True" not in (root / DRIVER_PATH).read_text(encoding="utf-8")


def test_d16_forged_seal_and_head_refuse_the_resume(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(_task: AgentTask, _sid: str, _attempt: int) -> None:
        write(root, DRIVER_PATH, _weak_driver())
        git(root, "add", "-A")
        git(root, *ATTACKER_IDENTITY, "commit", "-qm", "weak")
        _forge_seal(root)
        raise KeyboardInterrupt

    agent = Agent(impl=impl)
    require(run_lictor(root, agent).killed, "el agente debía matar la ejecución")
    calls = len(agent.calls)

    assert run_lictor(root, agent) == Outcome(10)

    assert len(agent.calls) == calls, "no debía llamarse al agente"
    assert green_ids(root) == []
    reason = events_of(root, "run.stopped")[-1]["data"]["reason"]
    assert "HEAD" in reason
    assert "seal.json" in reason


def test_d16_forged_seal_alone_refuses_the_resume(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(_task: AgentTask, _sid: str, _attempt: int) -> None:
        write(root, DRIVER_PATH, _weak_driver())
        _forge_seal(root)
        raise KeyboardInterrupt

    agent = Agent(impl=impl)
    require(run_lictor(root, agent).killed, "el agente debía matar la ejecución")

    assert run_lictor(root, agent) == Outcome(10)
    assert "seal.json" in events_of(root, "run.stopped")[-1]["data"]["reason"]


def _plant_fsmonitor(root: Path) -> None:
    script = root / ".git" / "evil.sh"
    script.write_text(FSMONITOR_HOOK, encoding="utf-8")
    script.chmod(0o755)
    log = root / ".lictor" / "journal.jsonl"
    git(root, "config", "core.fsmonitor", f"{script} {log} #")


def test_g1_the_copy_has_no_git_config_to_plant(make_project: ProjectFactory) -> None:
    root = make_project()
    refused: list[int] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        _require_copy(task, root)
        good_impl(task, scenario_id, attempt)
        refused.append(shell(["git", "config", "--local", "core.x", "y"], task.cwd).returncode)

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)
    assert all(refused)


def test_g1_git_config_cannot_run_agent_code(make_project: ProjectFactory) -> None:
    root = make_project()
    planted = [False]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if scenario_id == "IVA-001" and not planted[0]:
            planted[0] = True
            _plant_fsmonitor(root)

    run_lictor(root, Agent(impl=impl))

    require(planted[0], "el ataque debía ejecutarse")
    log = (root / ".lictor" / "journal.jsonl").read_text(encoding="utf-8")
    assert log.count("forjado") == 0
    assert "L0-estado" in _failed_at(root)
    assert "fsmonitor" not in (root / ".git" / "config").read_text(encoding="utf-8")


def test_g1_a_git_filter_is_removed_before_git_runs_again(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """No `-c` can name a filter in advance: it has to be gone before any status."""
    root = make_project()
    marker = tmp_path / "filtro-ejecutado"
    planted = [False]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if not planted[0]:
            planted[0] = True
            git(root, "config", "filter.mal.clean", f"touch {marker}; cat")
            write(root, ".git/info/attributes", "* filter=mal\n")
            write(root, "README.md", "tocado para que git tenga que leerlo\n")

    run_lictor(root, Agent(impl=impl))

    require(planted[0], "el ataque debía ejecutarse")
    assert not marker.exists()
    assert "L0-estado" in _failed_at(root)


def test_g1_config_planted_before_dying_refuses_the_resume_without_running_git(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    root = make_project()
    marker = tmp_path / "filtro-ejecutado"

    def impl(_task: AgentTask, _sid: str, _attempt: int) -> None:
        git(root, "config", "filter.mal.clean", f"touch {marker}; cat")
        write(root, ".git/info/attributes", "* filter=mal\n")
        write(root, "README.md", "tocado para que git tenga que leerlo\n")
        raise KeyboardInterrupt

    agent = Agent(impl=impl)
    require(run_lictor(root, agent).killed, "el agente debía matar la ejecución")

    assert run_lictor(root, agent) == Outcome(10)
    assert not marker.exists()
    assert ".git" in events_of(root, "run.stopped")[-1]["data"]["reason"]


def test_g1_config_planted_by_the_driver_refuses_the_resume(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """Before the first seal the only anchor is the one `preflight` set."""
    root = make_project()
    marker = tmp_path / "filtro-ejecutado"

    def driver(_task: AgentTask, _attempt: int) -> None:
        git(root, "config", "filter.mal.clean", f"touch {marker}; cat")
        raise KeyboardInterrupt

    agent = Agent(driver=driver)
    require(run_lictor(root, agent).killed, "el agente debía matar la ejecución")

    assert run_lictor(root, agent) == Outcome(10)
    assert not marker.exists()
    assert ".git" in events_of(root, "run.stopped")[-1]["data"]["reason"]


def test_c7_the_copy_has_no_state_to_tamper_with(make_project: ProjectFactory) -> None:
    root = make_project()
    looked: list[bool] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        _require_copy(task, root)
        looked.append((task.cwd / ".lictor" / "journal.jsonl").exists())
        good_impl(task, scenario_id, attempt)

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)
    assert looked == [False, False, False]


def test_c7_state_tampering_fails_the_attempt(make_project: ProjectFactory) -> None:
    root = make_project()
    attacked = [0]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if scenario_id == "IVA-001":
            attacked[0] += 1
            with (root / ".lictor" / "journal.jsonl").open("a", encoding="utf-8") as log:
                log.write('{"forjado":true}\n')

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require(attacked[0], "el ataque debía ejecutarse")
    assert "IVA-001" not in green_ids(root)
    assert quarantined(root) == ["IVA-001"]
    assert agent.calls.count("IVA-001") == 3, "cada manipulación gasta el intento"
    assert "journal.jsonl" in error_block(agent.prompts["IVA-001"][1])
    assert not [
        e
        for e in events_of(root, "verify.finished")
        if e["data"]["ok"] and e["scenarioid"] == "IVA-001"
    ]
    log = (root / ".lictor" / "journal.jsonl").read_text(encoding="utf-8")
    assert "forjado" not in log
    tampered = events_of(root, "state.tampered")
    assert len(tampered) == 3
    assert ".lictor/journal.jsonl" in tampered[0]["data"]["changes"]


def test_c7_tampering_during_the_driver_fails_its_attempt(make_project: ProjectFactory) -> None:
    root = make_project()
    attacked = [False]

    def driver(task: AgentTask, attempt: int) -> None:
        good_driver(task, attempt)
        if not attacked[0]:
            attacked[0] = True
            with (root / ".lictor" / "journal.jsonl").open("a", encoding="utf-8") as log:
                log.write('{"forjado":true}\n')

    agent = Agent(driver=driver)
    assert run_lictor(root, agent) == Outcome(0)

    assert agent.calls.count("driver") == 2
    assert len(events_of(root, "state.tampered")) == 1


def test_c8_the_copy_has_no_checkpoint(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        _require_copy(task, root)
        assert not (task.cwd / ".lictor" / "checkpoints.sqlite").exists()
        good_impl(task, scenario_id, attempt)

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)


def _rows(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        return int(connection.execute("select count(*) from writes").fetchone()[0])
    finally:
        connection.close()


def test_c8_state_tampering_through_the_wal(make_project: ProjectFactory) -> None:
    root = make_project()
    database = root / ".lictor" / "checkpoints.sqlite"
    rows: list[tuple[int, int]] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if not rows:
            before = _rows(database)
            connection = sqlite3.connect(database)
            connection.execute("delete from writes")
            deleted = connection.total_changes
            connection.commit()
            connection.close()
            rows.append((before, deleted))

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    require(rows and rows[0][1] > 0, "el ataque debía borrar filas")
    assert "L0-estado" in _failed_at(root)
    assert any(
        ".lictor/checkpoints.sqlite" in change
        for event in events_of(root, "state.tampered")
        for change in event["data"]["changes"]
    )
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []


def test_reading_the_state_during_a_window_is_not_tampering(make_project: ProjectFactory) -> None:
    """`lictor status` opens the checkpoint meanwhile: that moves `-shm`, not the data."""
    root = make_project()
    database = root / ".lictor" / "checkpoints.sqlite"

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        require(_rows(database) >= 0, "la lectura debía funcionar")

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)
    assert events_of(root, "state.tampered") == []


def test_altered_evidence_shows_in_the_report(make_project: ProjectFactory) -> None:
    """`runs/` stays reachable: the verdict never reads it again, and the report tells."""
    root = make_project()
    altered: list[Path] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if scenario_id == "IVA-002":
            for log in (root / ".lictor" / "runs").glob("*/IVA-001/*-verify/acceptance.log"):
                log.write_text("todo fue bien\n", encoding="utf-8")
                altered.append(log)

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    require(altered, "el ataque debía ejecutarse")
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert f"`{altered[0].parent.relative_to(root)}`" in report
    assert events_of(root, "state.tampered") == []


def test_d13_resume_stops_instead_of_wiping_user_work(make_project: ProjectFactory) -> None:
    root = make_project()
    limited = [True]

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> AgentStatus | None:
        if scenario_id == "IVA-002" and limited[0]:
            return "rate_limited"
        good_impl(task, scenario_id)
        return None

    agent = Agent(impl=impl)
    first = run_lictor(root, agent)
    require(first.exit_code == 50, f"se esperaba la pausa por cuota: {first}")

    write(root, "src/iva/notas_usuario.py", "# trabajo del usuario\n")
    write(root, "NOTAS.md", "importante\n")
    module = root / MODULE_PATH
    write(root, MODULE_PATH, module.read_text(encoding="utf-8") + "\n# edición del usuario\n")
    limited[0] = False
    second = run_lictor(root, agent)

    assert second.exit_code == 10
    assert (root / "src/iva/notas_usuario.py").exists()
    assert (root / "NOTAS.md").exists()
    assert "edición del usuario" in module.read_text(encoding="utf-8")
    assert "NOTAS.md" in events_of(root, "run.stopped")[-1]["data"]["reason"]


def test_d14_resume_never_commits_unverified_code(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project()
    original = CommitGreen.execute
    armed = [True]

    def die_once(self: CommitGreen, request: object) -> object:
        if armed[0]:
            armed[0] = False
            raise KeyboardInterrupt
        return original(self, request)

    monkeypatch.setattr(CommitGreen, "execute", die_once)
    agent = Agent()
    require(run_lictor(root, agent).killed, "CommitGreen debía matar la ejecución")
    require(events_of(root, "verify.finished"), "la muerte debía llegar tras verificar")

    broken = '"""General."""\n    import os\n    os.remove("/x")'
    write(root, MODULE_PATH, STUB.replace('"""General."""', broken))

    assert run_lictor(root, agent) == Outcome(10)

    assert green_ids(root) == []
    assert "os.remove" in (root / MODULE_PATH).read_text(encoding="utf-8")

    git(root, "checkout", "--", MODULE_PATH)
    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []
