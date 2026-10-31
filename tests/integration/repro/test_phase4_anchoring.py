"""Phase 4 (C3, A4, A3): HEAD and the state are anchored; resuming loses nothing."""

import hashlib
import json
import sqlite3

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
    events_of,
    false_greens,
    git,
    good_impl,
    green_ids,
    journal,
    pending,
    require,
    run_lictor,
    shell,
    write,
)

FSMONITOR_HOOK = (
    "#!/bin/sh\n"
    'echo "{\\"forjado\\":\\"fsmonitor\\"}" >> '
    '"$(git rev-parse --show-toplevel 2>/dev/null || pwd)/.lictor/journal.jsonl"\n'
    "exit 1\n"
)


@pending(4, "C3")
def test_c5_agent_commit_is_rejected(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        if scenario_id == "IVA-001":
            pyproject = task.cwd / "pyproject.toml"
            content = pyproject.read_text(encoding="utf-8")
            pyproject.write_text(f'{content}\n[tool.ruff]\nexclude = ["src"]\n', encoding="utf-8")
            shell(["git", "add", "pyproject.toml"], task.cwd)
            trailer = ["--trailer", "Scenario: IVA-999"]
            shell(["git", "commit", "-qm", "chore: x", *trailer], task.cwd)

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require("IVA-001" in agent.calls, "el agente debía llegar a implementar")
    assert "exclude" not in git(root, "show", "HEAD:pyproject.toml")
    assert "IVA-999" not in git(root, "log", "--format=%B")


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


@pending(4, "C3")
def test_d16_forged_seal_and_killed_lictor(make_project: ProjectFactory) -> None:
    root = make_project()
    forged = [False]

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        if forged[0]:
            return
        forged[0] = True
        seal_path = task.cwd / ".lictor" / "seal.json"
        weak = DRIVER
        for check in ("iva.general(100) == 121", "iva.reducido(100) == 110"):
            weak = weak.replace(f"assert {check}", "assert True")
        weak = weak.replace("assert iva.negativo(-5) is True", "assert True")
        write(task.cwd, DRIVER_PATH, weak)
        shell(["git", "add", "-A"], task.cwd)
        shell(["git", "commit", "-qm", "weak"], task.cwd)
        if seal_path.is_file():
            seal = json.loads(seal_path.read_text(encoding="utf-8"))
            digest = hashlib.sha256((task.cwd / DRIVER_PATH).read_bytes()).hexdigest()
            seal["files"][DRIVER_PATH] = f"sha256:{digest}"
            seal_path.write_text(json.dumps(seal), encoding="utf-8")
        raise KeyboardInterrupt

    agent = Agent(impl=impl)
    require(run_lictor(root, agent).killed, "el agente debía matar la ejecución")
    run_lictor(root, agent)

    assert false_greens(root) == []


@pending(4, "C3")
def test_g1_git_config_cannot_run_agent_code(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        if scenario_id == "IVA-001" and (task.cwd / ".git").is_dir():
            write(task.cwd, ".git/evil.sh", FSMONITOR_HOOK)
            (task.cwd / ".git" / "evil.sh").chmod(0o755)
            shell(["git", "config", "core.fsmonitor", ".git/evil.sh"], task.cwd)

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require(journal(root), "la ejecución debía dejar bitácora")
    log = (root / ".lictor" / "journal.jsonl").read_text(encoding="utf-8")
    assert log.count("forjado") == 0


@pending(4, "A4")
def test_c7_state_tampering_fails_the_attempt(make_project: ProjectFactory) -> None:
    root = make_project()
    attacked = [False]

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        journal_path = task.cwd / ".lictor" / "journal.jsonl"
        if scenario_id == "IVA-001" and journal_path.is_file():
            attacked[0] = True
            with journal_path.open("a", encoding="utf-8") as log:
                log.write('{"forjado":true}\n')

    run_lictor(root, Agent(impl=impl))

    assert not (attacked[0] and "IVA-001" in green_ids(root))


@pending(4, "C3")
def test_c8_state_tampering_through_the_wal(make_project: ProjectFactory) -> None:
    root = make_project()
    attacked = [False]

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        database = task.cwd / ".lictor" / "checkpoints.sqlite"
        if scenario_id == "IVA-001" and database.is_file():
            connection = sqlite3.connect(database)
            connection.execute("delete from writes")
            attacked[0] = connection.total_changes > 0
            connection.commit()
            connection.close()

    run_lictor(root, Agent(impl=impl))

    assert not (attacked[0] and "IVA-001" in green_ids(root))


@pending(4, "A3")
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
    run_lictor(root, agent)

    assert false_greens(root) == []
