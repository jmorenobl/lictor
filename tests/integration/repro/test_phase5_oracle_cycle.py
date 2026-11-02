"""Phase 5 (A2, M2, M3, A7): the sealed oracle is valid and scenarios move through it soundly."""

from pathlib import Path

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    GOOD_2,
    GOOD_3,
    MODULE_PATH,
    STUB,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_driver,
    green_ids,
    pending,
    quarantined,
    require,
    run_lictor,
    write,
)

NEW_SCENARIO = (
    "\n  @IVA-004\n  Escenario: Cero\n    Dado una factura con importe 0\n"
    "    Cuando calculo el IVA general\n    Entonces el total es 0\n"
)
NEW_FUNCTION = '\n\ndef nuevo(x: float) -> float:\n    """N."""\n    return x\n'
VACUOUS_DRIVER = (
    "import iva\n\n\ndef test_iva_001():\n    iva.general(100)\n\n\n"
    "def test_iva_002():\n    iva.reducido(100)\n\n\n"
    "def test_iva_003():\n    iva.negativo(-5)\n"
)


def _write_extended_driver(task: AgentTask) -> None:
    write(task.cwd, DRIVER_PATH, f"{DRIVER}\n\ndef test_iva_004():\n    assert iva.nuevo(0) == 0\n")


def _write_extended_module(task: AgentTask, content: str = GOOD_3) -> None:
    write(task.cwd, MODULE_PATH, content + NEW_FUNCTION)


def _add_new_scenario(root: Path) -> None:
    feature = root / "features" / "iva.feature"
    feature.write_text(feature.read_text(encoding="utf-8") + NEW_SCENARIO, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "spec: escenario nuevo")


@pending(5, "A2")
def test_f30_driver_without_assertions_is_rejected(make_project: ProjectFactory) -> None:
    root = make_project()

    def driver(task: AgentTask, _attempt: int) -> None:
        good_driver(task)
        write(task.cwd, DRIVER_PATH, VACUOUS_DRIVER)

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        wrong = STUB.replace("raise NotImplementedError", "return 0").replace("-> bool", "-> float")
        write(task.cwd, MODULE_PATH, wrong)

    agent = Agent(driver=driver, impl=impl)
    run_lictor(root, agent)

    assert false_greens(root) == []


@pending(5, "A2")
def test_d10_broken_driver_is_not_blamed_on_the_scenario(make_project: ProjectFactory) -> None:
    root = make_project()

    def driver(task: AgentTask, _attempt: int) -> None:
        good_driver(task)
        write(task.cwd, DRIVER_PATH, DRIVER.replace("iva.general", "iva.gnral"))

    agent = Agent(driver=driver)
    run_lictor(root, agent)

    assert agent.calls.count("IVA-001") < 3, agent.calls
    assert "IVA-001" not in quarantined(root)


@pending(5, "M2")
def test_d17_collateral_green_skips_the_agent(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id == "IVA-003":
            raise KeyboardInterrupt
        if scenario_id == "IVA-001":
            write(task.cwd, MODULE_PATH, GOOD_2)

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require("IVA-001" in green_ids(root), "IVA-001 debía quedar verde")
    assert "IVA-002" not in agent.calls
    assert "Lictor-Collateral" in git(root, "log", "--format=%B")


@pending(5, "M2")
def test_d17_new_run_registers_collateral_green_in_baseline(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id != "IVA-001":
            raise KeyboardInterrupt
        write(task.cwd, MODULE_PATH, GOOD_2)

    agent = Agent(impl=impl)
    require(run_lictor(root, agent).killed, "la ejecución debía morir tras IVA-001")

    second = run_lictor(root, agent, new=True)

    assert second.exit_code != 10, second


@pending(5, "M3")
def test_d28_driver_prompt_lists_existing_greens(make_project: ProjectFactory) -> None:
    root = make_project()
    require(run_lictor(root, Agent()) == Outcome(0), "la primera pasada debía quedar verde")
    _add_new_scenario(root)

    def driver(task: AgentTask, _attempt: int) -> None:
        _write_extended_driver(task)

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        _write_extended_module(task)

    agent = Agent(driver=driver, impl=impl)
    run_lictor(root, agent)

    require(agent.prompts["driver"], "el driver debía ampliarse")
    prompt = agent.prompts["driver"][0]
    line = next(text for text in prompt.splitlines() if "ya funcionan" in text)
    assert "IVA-001" in line, line


@pending(5, "M3")
def test_d28_changing_a_green_scenario_reopens_it(make_project: ProjectFactory) -> None:
    root = make_project()
    require(run_lictor(root, Agent()) == Outcome(0), "la primera pasada debía quedar verde")
    feature = root / "features" / "iva.feature"
    feature.write_text(
        feature.read_text(encoding="utf-8").replace("el total es 121", "el total es 122"),
        encoding="utf-8",
    )
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "spec: cambia un verde")

    def driver(task: AgentTask, _attempt: int) -> None:
        write(task.cwd, DRIVER_PATH, DRIVER.replace("== 121", "== 122"))

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, GOOD_3.replace("1.21", "1.22"))

    agent = Agent(driver=driver, impl=impl)
    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert "IVA-001" in agent.calls


def test_d12_missing_unit_dir_does_not_block_an_honest_agent(make_project: ProjectFactory) -> None:
    root = make_project()

    def driver(task: AgentTask, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, STUB)
        write(task.cwd, DRIVER_PATH, DRIVER)

    agent = Agent(driver=driver)
    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), events_of(root, "verify.finished")[:1]
    assert len(green_ids(root)) == 3
