"""Go and Node with the `generic` profile, at the basic level of proof (ADR-0020, §9.1).

The agent is scripted. Each case configures its toy project as `generic.skill.md` says, and an
oracle that doesn't use Lictor checks every green on its own toolchain.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lictor.cli import app
from lictor.domain.model import AgentTask
from tests.integration.repro.harness import (
    VERIFY_ENV,
    Agent,
    Outcome,
    events_of,
    green_ids,
    known_limitation,
    require,
    run_lictor,
    write,
)
from tests.integration.repro.toolchains import (
    GO_DRIVER_PATH,
    GO_IMPL,
    GO_LENIENT,
    GO_REFACTORED,
    GO_STUB,
    GO_UNIT_PATH,
    NODE_DRIVER,
    NODE_DRIVER_PATH,
    NODE_IMPL,
    NODE_STUB,
    go_driver,
    go_false_greens,
    go_project,
    node_false_greens,
    node_project,
)

pytestmark = pytest.mark.toolchain


def _go_driver(source: str) -> object:
    def driver(task: AgentTask, _attempt: int = 1) -> None:
        write(task.cwd, "nif/nif.go", GO_STUB)
        write(task.cwd, GO_DRIVER_PATH, source)

    return driver


def _go_impl(source: str = GO_IMPL) -> object:
    def impl(task: AgentTask, _scenario_id: str, _attempt: int = 1) -> None:
        write(task.cwd, "nif/nif.go", source)

    return impl


def _go_refactor(task: AgentTask, _scenario_id: str, _attempt: int = 1) -> None:
    """Improves the scenario's file and adds a case to the unit test beside the code."""
    write(task.cwd, "nif/nif.go", GO_REFACTORED)
    unit = (task.cwd / GO_UNIT_PATH).read_text(encoding="utf-8")
    write(
        task.cwd,
        GO_UNIT_PATH,
        unit
        + '\nfunc TestValidoCorto(t *testing.T) {\n\tif Valido("1") {\n\t\tt.Fatal()\n\t}\n}\n',
    )


def _cli(*args: str) -> str:
    result = CliRunner().invoke(app, list(args))
    return result.output


def test_go_reaches_green_with_its_refactor_and_every_report_says_the_basic_level(
    tmp_path: Path,
) -> None:
    root = go_project(tmp_path / "nif", refactor={"enabled": True})
    agent = Agent(driver=_go_driver(go_driver()), impl=_go_impl(), refactor=_go_refactor)

    doctor = _cli("doctor", "--cwd", str(root))
    outcome = run_lictor(root, agent)

    assert "✓ Configuración válida: perfil 'generic@0.1' (alpha)" in doctor
    assert "⚠ Nivel de prueba: básico" in doctor
    assert "⚠ Órdenes del agente" not in doctor
    assert outcome == Outcome(0)
    assert agent.calls == ["driver", "NIF-001", "refactor:NIF-001"]
    assert green_ids(root) == ["NIF-001", "NIF-002"]
    assert go_false_greens(root) == []
    refactored = events_of(root, "scenario.refactored")
    assert [e["data"]["outcome"] for e in refactored] == ["applied"]
    started = events_of(root, "run.started")[0]["data"]
    assert started["proof"]["level"] == "basico"
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "- **Nivel de prueba**: básico" in report
    assert "## Lo que este nivel no comprueba" in report
    assert "No se puede saber: el perfil no declara cómo leer" in report
    assert "Nivel de prueba: básico (" in _cli("status", "--cwd", str(root))
    checked = _cli("check", "--cwd", str(root), "--level", "L1,L2")
    assert "✓ vet" in checked
    assert "✓ unit" in checked


def test_go_without_the_recover_in_its_first_test_is_rejected_saying_why(
    tmp_path: Path,
) -> None:
    root = go_project(tmp_path / "nif")
    agent = Agent(driver=_go_driver(go_driver(first_defer=False)))

    outcome = run_lictor(root, agent)

    assert outcome.exit_code not in (0, None)
    assert green_ids(root) == []
    rejected = events_of(root, "driver.rejected")
    assert rejected[0]["data"]["reason"] == "mapeo"
    assert "panic: no implementado" in agent.prompts["driver"][1]


def test_go_driver_that_doesnt_compile_hears_the_compiler_on_its_retry(tmp_path: Path) -> None:
    root = go_project(tmp_path / "nif")
    broken = go_driver(extra="\nvar _ = nif.NoExiste\n")

    def driver(task: AgentTask, attempt: int = 1) -> None:
        _go_driver(broken if attempt == 1 else go_driver())(task, attempt)  # type: ignore[operator]

    agent = Agent(driver=driver, impl=_go_impl())

    assert run_lictor(root, agent) == Outcome(0)
    assert "undefined: nif.NoExiste" in agent.prompts["driver"][1]
    assert green_ids(root) == ["NIF-001", "NIF-002"]
    assert go_false_greens(root) == []


@known_limitation("un test que no compara nada pasa con cualquier código", until="0.24.0")
def test_go_a_vacuous_test_is_no_green(tmp_path: Path) -> None:
    """The basic level's gap: NIF-002 asserts nothing, and code that ignores the letter passes."""
    root = go_project(tmp_path / "nif")
    agent = Agent(driver=_go_driver(go_driver(second="vacuo")), impl=_go_impl(GO_LENIENT))

    require(run_lictor(root, agent) == Outcome(0), "el run debía llegar a verde")
    require(green_ids(root) == ["NIF-001", "NIF-002"], "los dos escenarios debían quedar en verde")

    assert go_false_greens(root) == []


@known_limitation(
    "un test que no compara con el escenario pasa con cualquier código", until="0.24.0"
)
def test_go_a_weak_assertion_is_no_green(tmp_path: Path) -> None:
    """The other half of the gap: NIF-002 fails at the red by the stub, never by its comparison."""
    root = go_project(tmp_path / "nif")
    agent = Agent(driver=_go_driver(go_driver(second="debil")), impl=_go_impl(GO_LENIENT))

    require(run_lictor(root, agent) == Outcome(0), "el run debía llegar a verde")
    require(green_ids(root) == ["NIF-001", "NIF-002"], "los dos escenarios debían quedar en verde")

    assert go_false_greens(root) == []


def _node_driver(task: AgentTask, _attempt: int = 1) -> None:
    write(task.cwd, "iva/index.js", NODE_STUB)
    write(task.cwd, NODE_DRIVER_PATH, NODE_DRIVER)


def test_node_builds_its_dependencies_into_a_hashed_environment_and_reaches_green(
    tmp_path: Path,
) -> None:
    root = node_project(tmp_path / "iva")

    def impl(task: AgentTask, _scenario_id: str, _attempt: int = 1) -> None:
        write(task.cwd, "iva/index.js", NODE_IMPL)

    agent = Agent(driver=_node_driver, impl=impl)

    assert run_lictor(root, agent) == Outcome(0)
    assert green_ids(root) == ["IVA-001"]
    assert node_false_greens(root) == []
    assert (root / VERIFY_ENV / "node_modules" / "redondeo" / "index.js").is_file()
    assert [e["data"]["reason"] for e in events_of(root, "env.built")] == ["primera verificación"]
    started = events_of(root, "run.started")[0]["data"]
    assert "entorno_sin_hash" not in json.dumps(started["proof"])


def test_node_a_dependency_changed_in_the_agents_turn_fails_the_environment(
    tmp_path: Path,
) -> None:
    root = node_project(tmp_path / "iva")

    def impl(task: AgentTask, _scenario_id: str, attempt: int = 1) -> None:
        write(task.cwd, "iva/index.js", NODE_IMPL)
        if attempt == 1:
            planted = root / VERIFY_ENV / "node_modules" / "redondeo" / "index.js"
            planted.write_text("export const centimos = () => 121;\n", encoding="utf-8")

    agent = Agent(driver=_node_driver, impl=impl)

    assert run_lictor(root, agent) == Outcome(0)
    verified = events_of(root, "verify.finished")
    implemented = [e for e in verified if e.get("scenarioid") == "IVA-001"]
    assert implemented[0]["data"]["failed_at"] == "L0-entorno"
    assert events_of(root, "env.tampered")
    assert node_false_greens(root) == []
