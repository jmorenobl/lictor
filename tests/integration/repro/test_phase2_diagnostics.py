"""Phase 2 (A5, M4, M1): the reason for a failure reaches the agent and the user."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    GOOD_BY_SCENARIO,
    MODULE_PATH,
    STUB,
    Agent,
    Outcome,
    error_block,
    events_of,
    git,
    good_driver,
    good_impl,
    require,
    run_lictor,
    write,
)

MEASURE_CONTROL = {
    "id": "mutacion",
    "level": "L5",
    "run": "sh -c 'echo salida-de-la-medida; exit 7'",
    "report": "exit-code",
    "mode": "measure",
}


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


def test_e12_regression_reason_is_kept() -> None:
    cases = [TestCaseResult("test_iva_001", status="failure", message="m")]
    condensed = condense_error(
        "acceptance", "Regresión detectada en escenario verde previo: IVA-001", cases
    )
    assert "Regresión" in condensed


def test_e12_assertion_line_survives_long_details() -> None:
    details = "\n".join(f"    línea {line}" for line in range(30)) + "\nE   assert 110.1 == 110"
    cases = [TestCaseResult("t", status="error", details=details)]
    assert "E   assert" in condense_error("x", "", cases)


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
            tree=GitCli(root).get_tree("HEAD"),
        )
    )

    assert result.ok is False


def test_d25_dirty_tree_exit_explains_itself(make_project: ProjectFactory) -> None:
    root = make_project(sync=False)
    write(root, "basura.txt", "x")

    result = CliRunner().invoke(app, ["run", "--cwd", str(root)])

    require(result.exit_code == 10, f"se esperaba exit 10: {result.exit_code}")
    assert result.output.strip(), "exit 10 sin mensaje"


def test_m4_rejected_driver_leaves_an_event_and_the_full_error(
    make_project: ProjectFactory,
) -> None:
    root = make_project()

    def driver(task: AgentTask, attempt: int) -> None:
        good_driver(task)
        if attempt == 1:
            write(task.cwd, DRIVER_PATH, DRIVER.replace("test_iva_003", "test_otro"))

    run_lictor(root, Agent(driver=driver))

    rejected = events_of(root, "driver.rejected")
    require(len(rejected) == 1, f"se esperaba un rechazo: {rejected}")
    data = rejected[0]["data"]
    assert data["attempt"] == 1
    assert "No se encontró" in data["error"]
    assert "No se encontró" in (root / data["error_path"]).read_text(encoding="utf-8")


def test_m4_every_attempt_keeps_its_own_report(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        if attempt > 1:
            write(task.cwd, MODULE_PATH, GOOD_BY_SCENARIO[scenario_id])
        else:
            write(task.cwd, MODULE_PATH, STUB.replace("raise NotImplementedError", "return 1", 1))

    outcome = run_lictor(root, Agent(impl=impl))

    require(outcome == Outcome(0), f"el agente honesto debía terminar: {outcome}")
    reports = sorted((root / ".lictor" / "runs").rglob("acceptance.xml"))
    assert len(reports) >= 2 + 2 + 2, f"informes: {reports}"
    failed = [e for e in events_of(root, "verify.finished") if not e["data"]["ok"]]
    require(failed, "el primer intento debía fallar")
    assert "assert 1 == 121" in failed[0]["data"]["error"]
    assert "assert 1 == 121" in (root / failed[0]["data"]["error_path"]).read_text(encoding="utf-8")


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


def test_d30_failed_measure_control_is_recorded(make_project: ProjectFactory) -> None:
    root: Path = make_project(extra_controls=[MEASURE_CONTROL])

    outcome = run_lictor(root, Agent())

    require(outcome == Outcome(0), f"measure no debe bloquear: {outcome}")
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "mutacion" in report
    assert "fallo" in report
    assert "código 7" in report

    finished = events_of(root, "verify.finished")
    require(len(finished) == 3, f"se esperaban 3 verificaciones: {len(finished)}")
    for event in finished:
        record = event["data"]["controls"]["mutacion"]
        assert record["ok"] is False
        assert record["exit_code"] == 7
        assert record["mode"] == "measure"
        output = (root / record["output"]).read_text(encoding="utf-8")
        assert "salida-de-la-medida" in output
    outputs = {event["data"]["controls"]["mutacion"]["output"] for event in finished}
    assert len(outputs) == 3, "una verificación machacó la salida de otra"


def test_d30_passing_measure_control_is_recorded_too(make_project: ProjectFactory) -> None:
    passing = {**MEASURE_CONTROL, "run": "sh -c 'echo todo-bien'"}
    root: Path = make_project(extra_controls=[passing])

    outcome = run_lictor(root, Agent())

    require(outcome == Outcome(0), f"measure no debe bloquear: {outcome}")
    record = events_of(root, "verify.finished")[0]["data"]["controls"]["mutacion"]
    assert record["ok"] is True
    assert "todo-bien" in (root / record["output"]).read_text(encoding="utf-8")
    assert "mutacion" in (root / ".lictor" / "report.md").read_text(encoding="utf-8")


def _stop_reason(root: Path, name: str = "run.finished") -> dict[str, Any]:
    events = events_of(root, name)
    require(events, f"la ejecución debía dejar {name}")
    return events[-1]["data"]


def test_m4_exhausted_driver_explains_the_exit(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project()

    def driver(task: AgentTask, _attempt: int) -> None:
        good_driver(task)
        write(task.cwd, DRIVER_PATH, DRIVER.replace("test_iva_003", "test_otro"))

    outcome = run_lictor(root, Agent(driver=driver))

    require(outcome == Outcome(10), f"se esperaba exit 10: {outcome}")
    data = _stop_reason(root)
    assert data["exit_code"] == 10
    assert "No se encontró" in data["reason"]
    assert data["reason"] in capsys.readouterr().err
    assert data["reason"] in (root / ".lictor" / "report.md").read_text(encoding="utf-8")


def test_m4_quarantine_exit_names_the_scenario(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id != "IVA-001":
            good_impl(task, scenario_id)

    outcome = run_lictor(root, Agent(impl=impl))

    require(outcome == Outcome(30), f"se esperaba exit 30: {outcome}")
    assert "IVA-001" in capsys.readouterr().err
    assert "IVA-001" in _stop_reason(root)["reason"]


def test_m4_spec_conflict_keeps_the_agents_reason(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project()
    agent = Agent()
    agent.executor.scenario_modes["IVA-001"] = "conflict"

    outcome = run_lictor(root, agent)

    require(outcome == Outcome(11), f"se esperaba exit 11: {outcome}")
    assert "IVA-001" in capsys.readouterr().err
    event = events_of(root, "scenario.quarantined")[0]
    assert "contradictorio" in event["data"]["last_error"]
    assert "contradictorio" in (root / ".lictor" / "report.md").read_text(encoding="utf-8")


def test_m4_green_run_says_so(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project()

    outcome = run_lictor(root, Agent())

    require(outcome == Outcome(0), f"se esperaba exit 0: {outcome}")
    assert "3 escenarios en verde" in capsys.readouterr().out


def test_m4_rate_limit_stop_is_recorded(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project()

    outcome = run_lictor(root, Agent(impl=lambda _t, _s, _a: "rate_limited"))

    require(outcome == Outcome(50), f"se esperaba exit 50: {outcome}")
    data = _stop_reason(root, "run.stopped")
    assert data["exit_code"] == 50
    assert "cuota" in data["reason"]
    assert "lictor run" in capsys.readouterr().err


def test_m4_refused_resume_is_recorded(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_project()
    run_lictor(root, Agent(impl=lambda _t, _s, _a: "rate_limited"))
    capsys.readouterr()
    config = json.loads((root / "lictor.json").read_text(encoding="utf-8"))
    config["limits"]["max_attempts"] += 1
    write(root, "lictor.json", json.dumps(config))

    outcome = run_lictor(root, Agent())

    require(outcome == Outcome(10), f"se esperaba exit 10: {outcome}")
    data = _stop_reason(root, "run.stopped")
    assert data["exit_code"] == 10
    assert "lictor.json" in data["reason"]
    assert "lictor.json" in capsys.readouterr().err


def test_m4_seal_broken_mid_run_explains_itself(
    make_project: ProjectFactory, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A change after L0-rutas can't be the agent's, so it stops the run instead of reverting."""
    feature = tmp_path / "demo-iva" / "features" / "iva.feature"
    edit = {
        "id": "edicion",
        "level": "L2",
        "run": f"sh -c 'echo \"# cambiado\" >> {feature}'",
        "report": "exit-code",
    }
    root = make_project(extra_controls=[edit])

    outcome = run_lictor(root, Agent())

    require(outcome == Outcome(10), f"se esperaba exit 10: {outcome}")
    assert "cambiaron" in _stop_reason(root)["reason"]
    assert "cambiaron" in capsys.readouterr().err


def test_m4_each_agent_call_keeps_its_own_raw_output(make_project: ProjectFactory) -> None:
    root = make_project()

    run_lictor(root, Agent())

    raws = [e["data"]["raw"] for e in events_of(root, "agent.finished")]
    assert len(set(raws)) == len(raws), f"un intento pisó el crudo de otro: {raws}"
    assert all((root / raw).is_file() for raw in raws)
