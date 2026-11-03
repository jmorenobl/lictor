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
    NEGATIVE_CHECK,
    STUB,
    TOTAL_CHECK,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_driver,
    good_impl,
    green_ids,
    quarantined,
    require,
    run_lictor,
    write,
)

# Its own step: with "calculo el IVA general" the existing code would already pass it.
NEW_SCENARIO = (
    "\n  @IVA-004\n  Escenario: Cero\n    Dado una factura con importe 0\n"
    "    Cuando calculo el IVA nuevo\n    Entonces el total es 0\n"
)
NEW_STUB = '\n\ndef nuevo(importe: float) -> float:\n    """N."""\n    raise NotImplementedError\n'
NEW_FUNCTION = NEW_STUB.replace("raise NotImplementedError", "return importe")
NEW_STEPS = (
    '\n\n@scenario(FEATURE, "Cero")\ndef test_iva_004():\n    pass\n\n\n'
    '@when("calculo el IVA nuevo", target_fixture="calculo")\n'
    "def nuevo(importe):\n    return lambda: iva.nuevo(importe)\n"
)
# Every step runs, none asserts anything.
VACUOUS_DRIVER = DRIVER.replace(TOTAL_CHECK, "calculo()").replace(
    NEGATIVE_CHECK, "iva.negativo(importe)"
)


def _write_extended_driver(task: AgentTask) -> None:
    write(task.cwd, MODULE_PATH, GOOD_3 + NEW_STUB)
    write(task.cwd, DRIVER_PATH, DRIVER + NEW_STEPS)


def _write_extended_module(task: AgentTask, content: str = GOOD_3) -> None:
    write(task.cwd, MODULE_PATH, content + NEW_FUNCTION)


def _add_new_scenario(root: Path) -> None:
    feature = root / "features" / "iva.feature"
    feature.write_text(feature.read_text(encoding="utf-8") + NEW_SCENARIO, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "spec: escenario nuevo")


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


def test_a_scenario_executed_from_a_copy_of_its_feature_does_not_link(
    make_project: ProjectFactory,
) -> None:
    """A project that lets its driver write features/ lets it write a copy, which pytest-bdd reads
    even as a `.txt`. With the profile's paths, the copy never leaves the agent's workspace."""
    paths = {
        "writable": ["src/", "tests/unit/"],
        "driver": ["tests/acceptance/", "features/"],
        "protected": ["features/", "tests/acceptance/", "lictor.json", "pyproject.toml", "uv.lock"],
    }
    root = make_project(config_changes={"paths": paths})
    feature = (root / "features" / "iva.feature").read_text(encoding="utf-8")

    def driver(task: AgentTask, attempt: int) -> None:
        good_driver(task, attempt)
        write(task.cwd, "features/copia.txt", feature.replace("el total es 121", "el total es 0"))
        copied = DRIVER.replace(
            '@scenario(FEATURE, "IVA general")',
            '@scenario("../../features/copia.txt", "IVA general")',
        )
        write(task.cwd, DRIVER_PATH, copied)

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, STUB.replace("raise NotImplementedError", "return 0", 1))

    outcome = run_lictor(root, Agent(driver=driver, impl=impl))

    assert outcome == Outcome(10)
    rejected = events_of(root, "driver.rejected")
    assert {event["data"]["reason"] for event in rejected} == {"mapeo"}
    assert "desde features/copia.txt, no desde features/iva.feature" in rejected[0]["data"]["error"]
    assert green_ids(root) == []


def test_d10_broken_driver_is_not_blamed_on_the_scenario(make_project: ProjectFactory) -> None:
    root = make_project()

    def driver(task: AgentTask, _attempt: int) -> None:
        good_driver(task)
        write(task.cwd, DRIVER_PATH, DRIVER.replace("iva.general", "iva.gnral"))

    agent = Agent(driver=driver)
    run_lictor(root, agent)

    assert agent.calls.count("IVA-001") < 3, agent.calls
    assert "IVA-001" not in quarantined(root)


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
        # The steps read the total from the Gherkin: the driver needs no change.
        write(task.cwd, DRIVER_PATH, DRIVER)

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


def test_reopened_green_that_passes_a_fresh_driver_waits_for_a_person(
    make_project: ProjectFactory,
) -> None:
    """A step may still hold the old value: only a person knows the change was only wording."""
    root = make_project()
    require(run_lictor(root, Agent()) == Outcome(0), "la primera pasada debía quedar verde")
    feature = root / "features" / "iva.feature"
    feature.write_text(
        feature.read_text(encoding="utf-8").replace(
            "Escenario: IVA reducido\n    Dado una factura con importe 100\n",
            "Escenario: IVA reducido\n    Dado una factura con importe 100\n"
            "    Y la factura es nacional\n",
        ),
        encoding="utf-8",
    )
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "spec: un paso más en un verde")
    head = git(root, "rev-parse", "HEAD").strip()
    step = '\n\n@given("la factura es nacional")\ndef nacional():\n    pass\n'
    agent = Agent(driver=lambda task, _attempt: write(task.cwd, DRIVER_PATH, DRIVER + step))

    stopped = run_lictor(root, agent)

    assert stopped == Outcome(10), agent.calls
    assert agent.calls == ["driver"]
    assert [event["scenarioid"] for event in events_of(root, "scenario.reopened")] == ["IVA-002"]
    assert events_of(root, "scenario.reregistered") == []
    assert git(root, "rev-parse", "HEAD").strip() == head
    [rejected] = events_of(root, "driver.rejected")
    assert (rejected["data"]["reason"], rejected["data"]["final"]) == (
        "especificacion_cambiada",
        True,
    )

    # The person checks the change was only wording and registers it, as the stop message says.
    trailer = ("--trailer", "Scenario: IVA-002")
    git(root, "commit", "-q", "--allow-empty", "-m", "test: solo cambió la redacción", *trailer)
    outcome = run_lictor(root, agent, new=True)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls == ["driver", "driver"]
    assert green_ids(root).count("IVA-002") == 2
    assert false_greens(root) == []


def test_baseline_registers_a_green_the_user_implemented_by_hand(
    make_project: ProjectFactory,
) -> None:
    """With a sealed driver, a pending test that passes was proved when the driver was sealed."""
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        if scenario_id != "IVA-001":
            raise KeyboardInterrupt
        good_impl(task, scenario_id, attempt)

    require(run_lictor(root, Agent(impl=impl)).killed, "la ejecución debía morir tras IVA-001")
    require(green_ids(root) == ["IVA-001"], "solo IVA-001 debía estar en verde")
    write(root, MODULE_PATH, GOOD_2)
    git(root, "commit", "-q", "-am", "feat: IVA reducido a mano")

    agent = Agent()
    outcome = run_lictor(root, agent, new=True)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls == ["IVA-003"]
    [collateral] = [e for e in events_of(root, "scenario.green") if e["data"].get("collateral")]
    assert collateral["scenarioid"] == "IVA-002"
    message = git(root, "log", "-1", "--format=%B", collateral["data"]["commit"])
    assert "Lictor-Collateral:" in message
    assert false_greens(root) == []


def test_a_driver_that_errors_on_every_attempt_is_named_as_suspicious(
    make_project: ProjectFactory,
) -> None:
    """It fails the red properly and with both impostors, but no implementation can pass it."""
    root = make_project()
    broken = DRIVER.replace(TOTAL_CHECK, "assert calculo().total == esperado")

    def driver(task: AgentTask, attempt: int) -> None:
        good_driver(task, attempt)
        write(task.cwd, DRIVER_PATH, broken)

    agent = Agent(driver=driver)
    outcome = run_lictor(root, agent)

    require(events_of(root, "driver.sealed"), "el driver debía sellarse")
    assert outcome == Outcome(30), agent.calls
    reasons = {
        e["scenarioid"]: e["data"]["reason"] for e in events_of(root, "scenario.quarantined")
    }
    assert reasons == {"IVA-001": "driver_sospechoso", "IVA-002": "driver_sospechoso"}
    assert green_ids(root) == ["IVA-003"], "IVA-003 no usa el paso roto"
    finished = events_of(root, "run.finished")[-1]["data"]["reason"]
    assert "Posible driver defectuoso en IVA-001, IVA-002" in finished
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "Posible driver defectuoso, no seguro" in report
