"""Phase 4 (D2): the agent works in a copy without `.git`, and its result enters as a tree."""

import stat
from pathlib import Path

from lictor.application.run_files import scratch_dir
from lictor.domain.model import AgentStatus, AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER_PATH,
    GOOD_1,
    MODULE_PATH,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_driver,
    good_impl,
    green_ids,
    require,
    run_lictor,
    shell,
    write,
)

ALL_GREEN = ["IVA-001", "IVA-002", "IVA-003"]


def _failed_at(root: Path) -> list[str | None]:
    return [event["data"]["failed_at"] for event in events_of(root, "verify.finished")]


def test_the_agent_works_outside_the_project_without_git_or_state(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    seen: list[Path] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        seen.append(task.cwd)
        assert not task.cwd.is_relative_to(root)
        assert not (task.cwd / ".git").exists()
        assert not (task.cwd / ".lictor").exists()
        assert (task.cwd / "pyproject.toml").is_file()
        inside = shell(["git", "rev-parse", "--is-inside-work-tree"], task.cwd)
        assert inside.returncode != 0
        good_impl(task, scenario_id, attempt)

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    assert len(set(seen)) == len(seen) == 3
    assert not any(path.exists() for path in seen)
    assert not git(root, "status", "--short")


def test_the_driver_works_in_a_copy_as_well(make_project: ProjectFactory) -> None:
    root = make_project()
    seen: list[Path] = []

    def driver(task: AgentTask, attempt: int) -> None:
        seen.append(task.cwd)
        good_driver(task, attempt)

    assert run_lictor(root, Agent(driver=driver)) == Outcome(0)

    assert not seen[0].is_relative_to(root)
    assert not seen[0].exists()


def test_each_attempt_starts_from_head_in_a_new_copy(make_project: ProjectFactory) -> None:
    root = make_project()
    write(root, "ignorado.log", "del usuario\n")
    git(root, "config", "core.excludesFile", str(root / "no-existe"))
    (root / ".git/info/exclude").write_text("ignorado.log\n", encoding="utf-8")
    leftovers: list[bool] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        if scenario_id != "IVA-001":
            good_impl(task, scenario_id, attempt)
            return
        leftovers.append((task.cwd / "src/iva/resto.py").exists())
        leftovers.append((task.cwd / "ignorado.log").exists())
        write(task.cwd, "src/iva/resto.py", '"""Resto."""\n')
        if attempt > 1:
            good_impl(task, scenario_id, attempt)

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    assert not any(leftovers)
    assert (root / "ignorado.log").is_file()


def test_the_result_enters_exactly_as_the_agent_left_it(make_project: ProjectFactory) -> None:
    """Binary content, a mode and a deletion: what a textual patch tends to lose."""
    root = make_project()
    blob = bytes(range(256)) * 8

    def driver(task: AgentTask, attempt: int) -> None:
        good_driver(task, attempt)
        write(task.cwd, "src/iva/sobrante.py", '"""Sobra."""\n')

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if scenario_id == "IVA-001":
            write(task.cwd, "src/iva/datos.bin", blob)
            script = task.cwd / "src/iva/guion.py"
            write(task.cwd, "src/iva/guion.py", '#!/usr/bin/env python\n"""Guion."""\n')
            script.chmod(script.stat().st_mode | stat.S_IXUSR)
            (task.cwd / "src/iva/sobrante.py").unlink()

    assert run_lictor(root, Agent(driver=driver, impl=impl)) == Outcome(0)

    assert (root / "src/iva/datos.bin").read_bytes() == blob
    assert (root / "src/iva/guion.py").stat().st_mode & stat.S_IXUSR
    assert "100755" in git(root, "ls-tree", "HEAD", "src/iva/guion.py")
    assert not (root / "src/iva/sobrante.py").exists()
    assert not git(root, "status", "--short")


def test_deleting_a_protected_file_is_rejected(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        (task.cwd / "pyproject.toml").unlink(missing_ok=True)

    run_lictor(root, Agent(impl=impl))

    assert set(_failed_at(root)) == {"L0-rutas"}
    assert "pyproject.toml" in git(root, "ls-tree", "--name-only", "HEAD")


def test_a_nested_repository_cannot_enter_the_commit(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        nested = task.cwd / "src/iva/vendido"
        nested.mkdir()
        require(shell(["git", "init", "-q"], nested).returncode == 0, "git init")
        write(nested, "a.txt", "a\n")
        identity = ["-c", "user.name=a", "-c", "user.email=a@b"]
        shell(["git", "add", "."], nested)
        shell(["git", *identity, "commit", "-qm", "x"], nested)

    run_lictor(root, Agent(impl=impl))

    assert green_ids(root) == []
    assert set(_failed_at(root)) == {"L0-rutas"}


def test_an_empty_nested_repository_costs_the_attempt_not_the_run(
    make_project: ProjectFactory,
) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if attempt == 1:
            nested = task.cwd / "src/iva/vacio"
            nested.mkdir()
            require(shell(["git", "init", "-q"], nested).returncode == 0, "git init")

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    assert green_ids(root) == ALL_GREEN


def test_a_gitignore_in_the_copy_can_only_hide_its_own_files(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        write(task.cwd, "src/iva/.gitignore", "__init__.py\n*.py\n.gitignore\n")
        write(task.cwd, "src/iva/_oculto.py", "x = 1\n")

    run_lictor(root, Agent(impl=impl))

    assert false_greens(root) == []
    listed = git(root, "ls-tree", "-r", "--name-only", "HEAD")
    assert "src/iva/_oculto.py" not in listed
    assert "src/iva/.gitignore" not in listed


def test_project_edits_during_a_run_survive_every_commit(make_project: ProjectFactory) -> None:
    """The working tree is the user's: nothing resets it any more (the `advance_head` case)."""
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        if scenario_id == "IVA-001":
            write(root, "README.md", "# edición del usuario\n")
            write(root, "NOTAS.md", "sin versionar\n")
        good_impl(task, scenario_id, attempt)

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    assert green_ids(root) == ALL_GREEN
    assert (root / "README.md").read_text(encoding="utf-8") == "# edición del usuario\n"
    assert (root / "NOTAS.md").read_text(encoding="utf-8") == "sin versionar\n"
    assert "README.md" in git(root, "status", "--short")
    assert false_greens(root) == []


def test_a_conflict_with_the_user_stops_without_losing_anything(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    edited = [False]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if scenario_id == "IVA-001" and not edited[0]:
            edited[0] = True
            write(root, MODULE_PATH, "# edición del usuario\n")
        good_impl(task, scenario_id, attempt)
        return None

    agent = Agent(impl=impl)
    assert run_lictor(root, agent) == Outcome(10)

    assert green_ids(root) == []
    assert (root / MODULE_PATH).read_text(encoding="utf-8") == "# edición del usuario\n"
    stopped = events_of(root, "run.stopped")[-1]["data"]
    assert MODULE_PATH in stopped["reason"]
    calls = len(agent.calls)

    git(root, "checkout", "--", MODULE_PATH)
    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ALL_GREEN
    assert agent.calls[calls:].count("IVA-001") == 0
    assert false_greens(root) == []


def test_a_conflict_with_the_driver_stops_without_losing_anything(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    edited = [False]

    def driver(task: AgentTask, attempt: int) -> None:
        if not edited[0]:
            edited[0] = True
            write(root, DRIVER_PATH, "# edición del usuario\n")
        good_driver(task, attempt)

    agent = Agent(driver=driver)
    assert run_lictor(root, agent) == Outcome(10)

    assert green_ids(root) == []
    assert (root / DRIVER_PATH).read_text(encoding="utf-8") == "# edición del usuario\n"
    assert DRIVER_PATH in events_of(root, "run.stopped")[-1]["data"]["reason"]

    (root / DRIVER_PATH).unlink()
    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ALL_GREEN
    assert false_greens(root) == []


def test_a_new_run_sweeps_what_a_killed_one_left_outside_the_project(
    make_project: ProjectFactory,
) -> None:
    root = make_project().resolve()

    with scratch_dir(root, "resto-") as left:
        assert run_lictor(root, Agent()) == Outcome(0)

        assert not left.exists()


def test_the_implementation_of_the_first_scenario_reaches_the_project(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    module = root / MODULE_PATH

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if scenario_id == "IVA-002":
            return "rate_limited"
        good_impl(task, scenario_id, attempt)
        return None

    assert run_lictor(root, Agent(impl=impl)) == Outcome(50)

    assert module.read_text(encoding="utf-8") == GOOD_1
    assert not git(root, "status", "--short")
