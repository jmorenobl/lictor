"""Phase 2 (A5, M4, M1): the reason for a failure reaches the agent and the user."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.runner.subprocess_runner import SubprocessRunner
from lictor.adapters.vcs.git_cli import GitCli
from lictor.application.usecases.verify_attempt import VerifyAttempt, VerifyAttemptRequest
from lictor.cli import app
from lictor.domain.model import AgentTask, TestCaseResult
from lictor.domain.rules import condense_error
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    Agent,
    Outcome,
    error_block,
    git,
    good_driver,
    pending,
    require,
    run_lictor,
    write,
)

MEASURE_CONTROL = {
    "id": "mutacion",
    "level": "L5",
    "run": "false",
    "report": "exit-code",
    "mode": "measure",
}


@pending(2, "A5")
def test_d11_rejected_driver_is_told_which_test_is_missing(make_project: ProjectFactory) -> None:
    root = make_project()

    def driver(task: AgentTask, attempt: int) -> None:
        good_driver(task)
        if attempt == 1:
            write(task.cwd, DRIVER_PATH, DRIVER.replace("test_iva_003", "test_otro"))

    agent = Agent(driver=driver)
    run_lictor(root, agent)

    require(len(agent.prompts["driver"]) > 1, "el driver debía reintentarse")
    assert "No se encontró" in error_block(agent.prompts["driver"][1])


@pending(2, "A5")
def test_e11_red_rule_names_the_offender_and_only_it(make_project: ProjectFactory) -> None:
    root = make_project()

    def driver(task: AgentTask, attempt: int) -> None:
        good_driver(task)
        if attempt == 1:
            passing = DRIVER.replace("assert iva.negativo(-5) is True", "assert True")
            write(task.cwd, DRIVER_PATH, passing)

    agent = Agent(driver=driver)
    run_lictor(root, agent)

    require(len(agent.prompts["driver"]) > 1, "el driver debía reintentarse")
    block = error_block(agent.prompts["driver"][1])
    assert "test_iva_003" in block
    assert "test_iva_001" not in block, "lista un rojo legítimo"


@pending(2, "A5")
def test_e12_current_scenario_survives_many_pending() -> None:
    long_trace = "\n".join(f"traza {line}" for line in range(40))
    cases = [
        TestCaseResult(
            f"test_iva_{index:03d}",
            status="failure",
            message="NotImplementedError",
            details=long_trace,
        )
        for index in range(1, 30)
    ]
    cases.append(
        TestCaseResult("test_iva_030", status="failure", message="assert 3 == 4 CLAVE", details="x")
    )

    condensed = condense_error("acceptance", "Escenario IVA-030 no pasó", cases)

    assert "no pasó" in condensed
    assert "CLAVE" in condensed


@pending(2, "A5")
def test_e12_regression_reason_is_kept() -> None:
    cases = [TestCaseResult("test_iva_001", status="failure", message="m")]
    condensed = condense_error(
        "acceptance", "Regresión detectada en escenario verde previo: IVA-001", cases
    )
    assert "Regresión" in condensed


@pending(2, "A5")
def test_e12_assertion_line_survives_long_details() -> None:
    details = "\n".join(f"    línea {line}" for line in range(30)) + "\nE   assert 110.1 == 110"
    cases = [TestCaseResult("t", status="error", details=details)]
    assert "E   assert" in condense_error("x", "", cases)


@pending(2, "M1")
def test_d24_stale_report_does_not_hide_a_crashed_suite(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(sync=False)
    run_dir = root / ".lictor" / "runs" / "R"
    write(
        run_dir,
        "acceptance.xml",
        '<testsuite><testcase name="test_iva_001" classname="t"/></testsuite>',
    )
    readers = {"junit": JUnitReader(), "exit-code": ExitCodeReader()}
    verify = VerifyAttempt(GitCli(root), SubprocessRunner(), readers, root)
    monkeypatch.setattr(verify, "_check_seal", lambda _req: SimpleNamespace(ok=True))

    result = verify.execute(
        VerifyAttemptRequest(
            scenario_id="IVA-001",
            run_id="R",
            suppressions=(),
            acceptance_run=f"{sys.executable} -c 'import sys;sys.exit(3)' --junitxml {{out}}",
            all_scenario_ids=["IVA-001"],
        )
    )

    assert result.ok is False


@pending(2, "M4")
def test_d25_dirty_tree_exit_explains_itself(make_project: ProjectFactory) -> None:
    root = make_project(sync=False)
    write(root, "basura.txt", "x")

    result = CliRunner().invoke(app, ["run", "--cwd", str(root)])

    require(result.exit_code == 10, f"se esperaba exit 10: {result.exit_code}")
    assert result.output.strip(), "exit 10 sin mensaje"


@pending(2, "M4")
def test_d25_invalid_spec_exit_names_the_file(make_project: ProjectFactory) -> None:
    root = make_project(sync=False)
    feature = root / "features" / "iva.feature"
    feature.write_text(
        feature.read_text(encoding="utf-8").replace("@IVA-002", ""), encoding="utf-8"
    )
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "spec sin etiqueta")

    result = CliRunner().invoke(app, ["run", "--cwd", str(root)])

    require(result.exit_code == 10, f"se esperaba exit 10: {result.exit_code}")
    assert "iva.feature" in result.output


@pending(2, "M4")
def test_d30_failed_measure_control_is_recorded(make_project: ProjectFactory) -> None:
    root: Path = make_project(extra_controls=[MEASURE_CONTROL])

    outcome = run_lictor(root, Agent())

    require(outcome == Outcome(0), f"measure no debe bloquear: {outcome}")
    assert "mutacion" in (root / ".lictor" / "report.md").read_text(encoding="utf-8")
