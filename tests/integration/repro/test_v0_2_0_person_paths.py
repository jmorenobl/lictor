"""0.2.0: a scenario that only a person can fulfil stops the run, it isn't a quarantine.

A scenario that needs a path the agent may never change, as `pyproject.toml` for a new dependency
or `.github/` for a pipeline, spent every attempt at `L0-rutas` and ended in quarantine with exit
30, which reads as «the agent couldn't». The prompt now says which paths are off limits and that
the change, if it is truly needed, must be made anyway so a person sees it; an agent that makes it
again after being told it was discarded has confirmed it. Then the run stops with exit 10, naming
the path, without spending the attempts left.
"""

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER_PATH,
    Agent,
    Outcome,
    events_of,
    git,
    good_impl,
    green_ids,
    quarantined,
    run_lictor,
    write,
)

DEPENDENCY = '\n[dependency-groups]\nextra = ["requests"]\n'


def _needs(path: str, content: str) -> Agent:
    """An agent that implements IVA-001 and, on every attempt, also changes `path`."""

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if scenario_id == "IVA-001":
            target = task.cwd / path
            before = target.read_text(encoding="utf-8") if target.is_file() else ""
            write(task.cwd, path, before + content)

    return Agent(impl=impl)


def test_the_same_forbidden_path_twice_stops_the_run_with_10(make_project: ProjectFactory) -> None:
    root = make_project()
    agent = _needs("pyproject.toml", DEPENDENCY)
    head = git(root, "rev-parse", "HEAD")

    assert run_lictor(root, agent) == Outcome(10)

    assert agent.calls == ["driver", "IVA-001", "IVA-001"], "no gasta el tercer intento"
    assert quarantined(root) == []
    assert green_ids(root) == []
    (finished,) = events_of(root, "run.finished")
    reason = finished["data"]["reason"]
    assert "IVA-001 necesita cambiar pyproject.toml, que solo puede tocar una persona" in reason
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "solo puede tocar una persona" in report
    assert git(root, "rev-parse", "HEAD") != head, "el driver sellado sí queda"
    assert "requests" not in (root / "pyproject.toml").read_text(encoding="utf-8")


def test_a_path_the_profile_forbids_anywhere_counts_too(make_project: ProjectFactory) -> None:
    root = make_project()
    agent = _needs("src/.github/workflows/despliegue.yml", "on: push\n")

    assert run_lictor(root, agent) == Outcome(10)

    reason = events_of(root, "run.finished")[-1]["data"]["reason"]
    assert "src/.github/workflows/despliegue.yml" in reason
    assert agent.calls.count("IVA-001") == 2


def test_different_paths_on_each_attempt_are_slips_not_a_need(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    slips = {1: ("src/conftest.py", ""), 2: ("README.md", "x\n")}

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if scenario_id == "IVA-001" and attempt in slips:
            write(task.cwd, *slips[attempt])

    agent = Agent(impl=impl)

    assert run_lictor(root, agent) == Outcome(0)

    assert agent.calls.count("IVA-001") == 3
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]


def test_the_prompts_say_what_only_a_person_may_change(make_project: ProjectFactory) -> None:
    root = make_project()
    agent = _needs("pyproject.toml", DEPENDENCY)

    run_lictor(root, agent)

    first, retry = agent.prompts["IVA-001"]
    driver = agent.prompts["driver"][0]
    for prompt in (first, driver):
        assert "en ninguna carpeta" in prompt
        assert ".github" in prompt
        assert "uv.lock" in prompt
    assert "solo una persona" in first
    assert "repite exactamente el mismo cambio" in retry
    assert "pyproject.toml" in retry


def test_insisting_on_the_sealed_driver_is_cheating_not_a_need(
    make_project: ProjectFactory,
) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if scenario_id == "IVA-001":
            write(task.cwd, DRIVER_PATH, "def test_iva_001():\n    pass\n")

    agent = Agent(impl=impl)

    assert run_lictor(root, agent) == Outcome(30)

    assert agent.calls.count("IVA-001") == 3
    assert quarantined(root) == ["IVA-001"]
    retry = agent.prompts["IVA-001"][1]
    assert "no se tocan nunca" in retry
    assert "repite exactamente el mismo cambio" not in retry
