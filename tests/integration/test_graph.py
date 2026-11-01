import dataclasses
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langgraph.types import Command

from lictor.adapters.agents.fake import FakeExecutor
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.adapters.spec.gherkin_source import GherkinSource
from lictor.adapters.store.clock import FixedClock
from lictor.adapters.store.jsonl_eventlog import JsonlEventLog
from lictor.adapters.vcs.git_cli import GitCli
from lictor.application.ports import AuditSink, SpecSource
from lictor.application.usecases.check_red import CheckRed
from lictor.application.usecases.commit_green import CommitGreen
from lictor.application.usecases.implement_scenario import ImplementScenario
from lictor.application.usecases.prepare_driver import PrepareDriver
from lictor.application.usecases.quarantine import Quarantine
from lictor.application.usecases.verify_attempt import VerificationControl, VerifyAttempt
from lictor.application.verification_env import VerificationEnvironment, VerificationEnvSpec
from lictor.domain.model import CommandOutput, Scenario, VerifyResult
from lictor.orchestration.graph import (
    GraphContext,
    GraphState,
    _GraphNodes,
    create_graph,
    get_sqlite_saver,
)

DRIVER_TEST_CODE = "def test_iva_001(): pass\ndef test_iva_002(): pass\n"


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Lictor Test"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@lictor.local"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )


class DynamicTestRunner(FakeRunner):
    """Fake runner that inspects working tree to produce realistic test outcomes."""

    def __init__(self, repo_dir: Path) -> None:
        super().__init__()
        self.repo_dir = repo_dir

    def run(
        self,
        cmd: Any,
        cwd: Path,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandOutput:
        cmd_str = " ".join(str(c) for c in cmd)
        out_path: Path | None = None
        for arg in cmd:
            if str(arg).endswith(".xml"):
                out_path = Path(arg)
                break

        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if "unit-runner" in cmd_str:
                out_path.write_text(
                    '<testsuites><testsuite name="unit" /></testsuites>',
                    encoding="utf-8",
                )
                return CommandOutput(returncode=0, stdout="OK", stderr="", duration_ms=10)

            # Check implementation in src/
            iva_code = cwd / "src" / "iva.py"
            code_content = iva_code.read_text(encoding="utf-8") if iva_code.is_file() else ""

            # Check if IVA-001 or IVA-002 are implemented
            iva_001_passed = "IVA-001: OK" in code_content
            iva_002_passed = "IVA-002: OK" in code_content

            xml_cases = []
            if iva_001_passed:
                xml_cases.append('<testcase name="test_iva_001" classname="test_iva" />')
            else:
                xml_cases.append(
                    '<testcase name="test_iva_001" classname="test_iva">'
                    '<failure message="Not implemented">TODO</failure></testcase>'
                )

            if iva_002_passed:
                xml_cases.append('<testcase name="test_iva_002" classname="test_iva" />')
            else:
                xml_cases.append(
                    '<testcase name="test_iva_002" classname="test_iva">'
                    '<failure message="Not implemented">TODO</failure></testcase>'
                )

            xml_content = (
                f'<testsuites><testsuite name="suite">{"".join(xml_cases)}</testsuite></testsuites>'
            )
            out_path.write_text(xml_content, encoding="utf-8")

        return CommandOutput(returncode=0, stdout="OK", stderr="", duration_ms=10)


def _setup_test_environment(tmp_path: Path, executor: FakeExecutor) -> GraphContext:
    _init_git_repo(tmp_path)

    # Create initial features file and .gitignore
    (tmp_path / ".gitignore").write_text(".lictor/\n", encoding="utf-8")
    features_dir = tmp_path / "features"
    features_dir.mkdir(parents=True)
    (features_dir / "iva.feature").write_text(
        """# language: es
Característica: IVA de facturas

  @IVA-001
  Escenario: IVA general
    Dado una factura de 100
    Cuando aplico IVA general
    Entonces el total es 121

  @IVA-002
  Escenario: IVA reducido
    Dado una factura de 100
    Cuando aplico IVA reducido
    Entonces el total es 110
""",
        encoding="utf-8",
    )

    # Initial commit with features
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: initial spec"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    vcs = GitCli(tmp_path)
    event_log = JsonlEventLog(tmp_path / ".lictor" / "journal.jsonl")
    clock = FixedClock(datetime(2026, 10, 30, 15, 0, 0, tzinfo=UTC))
    spec_source = GherkinSource()
    runner = DynamicTestRunner(tmp_path)
    reader = JUnitReader()

    prepare_driver = PrepareDriver(
        vcs=vcs,
        executor=executor,
        event_log=event_log,
        clock=clock,
    )
    check_red = CheckRed(
        environment=VerificationEnvironment(
            vcs, runner, AuditSink(event_log, clock), tmp_path, VerificationEnvSpec()
        ),
        reader=reader,
        vcs=vcs,
        spec_source=spec_source,
        audit=AuditSink(event_log=event_log, clock=clock),
    )
    implement_scenario = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )
    verify_attempt = VerifyAttempt(
        vcs=vcs,
        runner=runner,
        readers={"junit": reader},
        project_root=tmp_path,
    )
    commit_green = CommitGreen(vcs=vcs, event_log=event_log, clock=clock)
    quarantine = Quarantine(event_log=event_log, clock=clock)

    return GraphContext(
        project_root=tmp_path,
        paths_writable=["src/"],
        paths_protected=["features/", "tests/acceptance/"],
        paths_driver=["tests/acceptance/"],
        suppressions=["# noqa", "# type: ignore"],
        driver_hints="Escribe tests en tests/acceptance/",
        hints="Python 3.12",
        check_commands=["check-command"],
        model="fake-agent",
        allowed_tools=["Bash(*)"],
        max_turns=40,
        timeout_s=600,
        max_attempts=2,
        max_driver_attempts=2,
        error_max_chars=4000,
        acceptance_run="acceptance-runner --junitxml {out}",
        acceptance_report="junit",
        acceptance_timeout_s=600,
        controls=[
            VerificationControl(
                id="unit",
                level="L2",
                run="unit-runner {out}",
                report="junit",
                allow_empty=True,
            )
        ],
        prepare_driver=prepare_driver,
        check_red=check_red,
        implement_scenario=implement_scenario,
        verify_attempt=verify_attempt,
        commit_green=commit_green,
        quarantine=quarantine,
        spec_source=spec_source,
        vcs=vcs,
        executor=executor,
        event_log=event_log,
        clock=clock,
    )


def test_graph_mode_good(tmp_path: Path) -> None:
    executor = FakeExecutor(
        mode="good",
        files_to_write={
            "driver": {
                "tests/acceptance/test_iva.py": DRIVER_TEST_CODE,
            },
            "IVA-001": {
                "src/iva.py": "# IVA-001: OK\n",
            },
            "IVA-002": {
                "src/iva.py": "# IVA-001: OK\n# IVA-002: OK\n",
            },
        },
    )

    context = _setup_test_environment(tmp_path, executor)
    saver = get_sqlite_saver(tmp_path / ".lictor" / "checkpoints.sqlite")
    graph = create_graph(context, checkpointer=saver)

    initial_state = {
        "run_id": "20261030-150000-0001",
        "config_hash": "dummy_config_hash",
        "driver_fresh": True,
        "driver_attempts": 0,
        "attempt": 0,
        "status": {},
        "reasons": {},
        "totals": {},
    }

    config = {"configurable": {"thread_id": "20261030-150000-0001"}}
    final_state = graph.invoke(initial_state, config=config)

    assert final_state.get("exit_code") == 0
    assert final_state.get("status") == {"IVA-001": "green", "IVA-002": "green"}
    assert (tmp_path / ".lictor" / "seal.json").is_file()

    # Check that commits were created
    trailers = context.vcs.get_trailers("Scenario")
    assert "IVA-001" in trailers
    assert "IVA-002" in trailers


def test_graph_mode_fail(tmp_path: Path) -> None:
    # Driver succeeds, but implement fails to satisfy tests
    executor = FakeExecutor(
        mode="fail",
        files_to_write={
            "driver": {
                "tests/acceptance/test_iva.py": DRIVER_TEST_CODE,
            },
            "fail": {
                "src/iva.py": "# Unfinished implementation\n",
            },
        },
    )

    context = _setup_test_environment(tmp_path, executor)
    saver = get_sqlite_saver(tmp_path / ".lictor" / "checkpoints.sqlite")
    graph = create_graph(context, checkpointer=saver)

    initial_state = {
        "run_id": "20261030-150000-0002",
        "config_hash": "dummy_config_hash",
        "driver_fresh": True,
        "driver_attempts": 0,
        "attempt": 0,
        "status": {},
        "reasons": {},
        "totals": {},
    }

    config = {"configurable": {"thread_id": "20261030-150000-0002"}}
    final_state = graph.invoke(initial_state, config=config)

    assert final_state.get("exit_code") == 30
    assert final_state.get("status") == {"IVA-001": "quarantined", "IVA-002": "quarantined"}
    assert final_state.get("reasons") == {
        "IVA-001": "intentos agotados",
        "IVA-002": "intentos agotados",
    }


def test_graph_mode_cheat(tmp_path: Path) -> None:
    # Agent tries to cheat by adding suppressions
    executor = FakeExecutor(
        mode="cheat",
        files_to_write={
            "driver": {
                "tests/acceptance/test_iva.py": DRIVER_TEST_CODE,
            },
            "cheat:implement": {
                "src/iva.py": f"# IVA-001: OK\n{'#'} noqa\n",
            },
        },
    )

    context = _setup_test_environment(tmp_path, executor)
    saver = get_sqlite_saver(tmp_path / ".lictor" / "checkpoints.sqlite")
    graph = create_graph(context, checkpointer=saver)

    initial_state = {
        "run_id": "20261030-150000-0003",
        "config_hash": "dummy_config_hash",
        "driver_fresh": True,
        "driver_attempts": 0,
        "attempt": 0,
        "status": {},
        "reasons": {},
        "totals": {},
    }

    config = {"configurable": {"thread_id": "20261030-150000-0003"}}
    final_state = graph.invoke(initial_state, config=config)

    assert final_state.get("exit_code") == 30
    assert final_state.get("status", {}).get("IVA-001") == "quarantined"


def test_graph_mode_conflict(tmp_path: Path) -> None:
    # First write driver, then report conflict on implement
    executor = FakeExecutor(
        mode="conflict",
        files_to_write={
            "driver": {
                "tests/acceptance/test_iva.py": DRIVER_TEST_CODE,
            },
        },
    )

    context = _setup_test_environment(tmp_path, executor)
    saver = get_sqlite_saver(tmp_path / ".lictor" / "checkpoints.sqlite")
    graph = create_graph(context, checkpointer=saver)

    initial_state = {
        "run_id": "20261030-150000-0004",
        "config_hash": "dummy_config_hash",
        "driver_fresh": True,
        "driver_attempts": 0,
        "attempt": 0,
        "status": {},
        "reasons": {},
        "totals": {},
    }

    config = {"configurable": {"thread_id": "20261030-150000-0004"}}
    final_state = graph.invoke(initial_state, config=config)

    assert final_state.get("exit_code") == 11
    assert final_state.get("status", {}).get("IVA-001") == "quarantined"
    assert final_state.get("reasons", {}).get("IVA-001") == "spec_conflict"


def test_graph_mode_rate_limit_and_resume(tmp_path: Path) -> None:
    executor = FakeExecutor(
        mode="rate_limit",
        files_to_write={
            "driver": {
                "tests/acceptance/test_iva.py": DRIVER_TEST_CODE,
            },
        },
    )

    context = _setup_test_environment(tmp_path, executor)
    saver = get_sqlite_saver(tmp_path / ".lictor" / "checkpoints.sqlite")
    graph = create_graph(context, checkpointer=saver)

    initial_state = {
        "run_id": "20261030-150000-0005",
        "config_hash": "dummy_config_hash",
        "driver_fresh": True,
        "driver_attempts": 0,
        "attempt": 0,
        "status": {},
        "reasons": {},
        "totals": {},
    }

    config = {"configurable": {"thread_id": "20261030-150000-0005"}}
    # First invocation hits rate_limit in build_driver and triggers interrupt in pause node
    graph.invoke(initial_state, config=config)

    # Check thread state shows pause interrupt
    state_snap = graph.get_state(config)
    assert len(state_snap.tasks) > 0
    assert len(state_snap.tasks[0].interrupts) > 0
    assert state_snap.tasks[0].interrupts[0].value["reason"] == "rate_limited"

    # Now simulate quota window reset: change executor mode to good
    executor.default_mode = "good"
    executor.files_to_write = {
        "driver": {
            "tests/acceptance/test_iva.py": DRIVER_TEST_CODE,
        },
        "IVA-001": {
            "src/iva.py": "# IVA-001: OK\n",
        },
        "IVA-002": {
            "src/iva.py": "# IVA-001: OK\n# IVA-002: OK\n",
        },
    }

    # Resume the interrupted execution
    resumed_state = graph.invoke(Command(resume=True), config=config)
    assert resumed_state.get("exit_code") == 0
    assert resumed_state.get("status") == {"IVA-001": "green", "IVA-002": "green"}
    assert resumed_state.get("driver_attempts") == 1


def test_graph_mode_crash_and_checkpoint_resumption(tmp_path: Path) -> None:
    # Simulate a crash during implement of IVA-001
    executor = FakeExecutor(
        mode="good",
        crash_on_scenario="IVA-001",
        files_to_write={
            "driver": {
                "tests/acceptance/test_iva.py": DRIVER_TEST_CODE,
            },
        },
    )

    context = _setup_test_environment(tmp_path, executor)
    saver = get_sqlite_saver(tmp_path / ".lictor" / "checkpoints.sqlite")
    graph = create_graph(context, checkpointer=saver)

    initial_state = {
        "run_id": "20261030-150000-0006",
        "config_hash": "dummy_config_hash",
        "driver_fresh": True,
        "driver_attempts": 0,
        "attempt": 0,
        "status": {},
        "reasons": {},
        "totals": {},
    }

    config = {"configurable": {"thread_id": "20261030-150000-0006"}}

    with pytest.raises(RuntimeError, match="Simulated FakeExecutor crash on scenario IVA-001"):
        graph.invoke(initial_state, config=config)

    # Working tree may be dirty from interrupted run; fix executor and resume from checkpoint!
    executor.crash_on_scenario = None
    executor.default_mode = "good"
    executor.files_to_write = {
        "driver": {
            "tests/acceptance/test_iva.py": DRIVER_TEST_CODE,
        },
        "IVA-001": {
            "src/iva.py": "# IVA-001: OK\n",
        },
        "IVA-002": {
            "src/iva.py": "# IVA-001: OK\n# IVA-002: OK\n",
        },
    }

    # Resume with invoke(None)
    resumed_state = graph.invoke(None, config=config)
    assert resumed_state.get("exit_code") == 0
    assert resumed_state.get("status") == {"IVA-001": "green", "IVA-002": "green"}


def test_graph_seal_tampered_in_implement_terminates_with_exit_code_10(tmp_path: Path) -> None:
    executor = FakeExecutor(
        mode="good",
        files_to_write={
            "driver": {
                "tests/acceptance/test_iva.py": DRIVER_TEST_CODE,
            },
            "IVA-001": {
                "src/iva.py": "# IVA-001: OK\n",
            },
        },
    )

    context = _setup_test_environment(tmp_path, executor)
    saver = get_sqlite_saver(tmp_path / ".lictor" / "checkpoints.sqlite")
    graph = create_graph(context, checkpointer=saver)

    initial_state = {
        "run_id": "20261030-150000-0007",
        "config_hash": "dummy_config_hash",
        "driver_fresh": True,
        "driver_attempts": 0,
        "attempt": 0,
        "status": {},
        "reasons": {},
        "totals": {},
    }

    config = {"configurable": {"thread_id": "20261030-150000-0007"}}

    # Tamper with spec right before implement: use a wrapper on spec_source
    class _TamperSpecSource(SpecSource):
        def __init__(self, inner: SpecSource) -> None:
            self._inner = inner
            self._calls = 0

        def load(self, features_dir: Path) -> list[Scenario]:
            return self._inner.load(features_dir)

        def verify_seal(self, project_root: Path, driver_paths: Sequence[str]) -> bool:
            self._calls += 1
            if self._calls > 1:
                return False
            return self._inner.verify_seal(project_root, driver_paths)

        def write_seal(
            self,
            project_root: Path,
            run_id: str,
            driver_paths: Sequence[str],
        ) -> dict[str, Any]:
            return self._inner.write_seal(project_root, run_id, driver_paths)

    context = dataclasses.replace(context, spec_source=_TamperSpecSource(context.spec_source))
    graph = create_graph(context, checkpointer=saver)

    final_state = graph.invoke(initial_state, config=config)
    assert final_state.get("exit_code") == 10
    # verify must NOT have run or set last_verify
    assert final_state.get("last_verify") == {"ok": True}


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"attempt": 2, "attempt_slot": 3}, 3),  # a failed call didn't use up the attempt
        ({"attempt": 2}, 2),  # checkpoint from before attempt_slot existed
        ({"attempt": 0}, 1),
        ({}, 1),
    ],
)
def test_verify_files_its_evidence_under_the_attempt_of_the_call(
    state: dict[str, Any], expected: int
) -> None:
    verify_attempt = MagicMock()
    verify_attempt.execute.return_value = VerifyResult(ok=True)
    context = MagicMock(acceptance_failure_exit_codes=(1, 2), controls=())
    context.verify_attempt = verify_attempt
    context.clock.now_iso.return_value = "2026-10-31T10:00:00Z"

    _GraphNodes(context).verify(cast(GraphState, {"run_id": "R", "current": "IVA-001", **state}))

    request = verify_attempt.execute.call_args.args[0]
    assert request.attempt == expected
    assert request.acceptance_failure_exit_codes == (1, 2)


def test_build_driver_hands_the_forbidden_paths_to_the_use_case() -> None:
    context = MagicMock(forbidden_paths=("*.pth",))
    context.prepare_driver.execute.return_value = MagicMock(
        agent_result=MagicMock(
            text="", duration_ms=0, cost_usd=0.0, tokens=MagicMock(input=0, output=0)
        ),
        driver_attempts=1,
        agent_signal="ok",
        agent_failures=0,
    )

    _GraphNodes(context).build_driver(cast(GraphState, {"run_id": "R"}))

    assert context.prepare_driver.execute.call_args.args[0].forbidden_paths == ("*.pth",)
