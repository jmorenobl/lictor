from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.usecases.implement_scenario import (
    ImplementScenario,
    ImplementScenarioRequest,
)
from lictor.domain.errors import SealTamperedError
from lictor.domain.model import AgentResult, Event, Scenario, ScenarioId, Tokens


def test_implement_scenario_seal_tampered_raises_without_reset(tmp_path: Path) -> None:
    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = False  # Human edited files!

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)

    use_case = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = ImplementScenarioRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        scenario=Scenario(
            id=ScenarioId("IVA-001"), name="IVA general", file="features/iva.feature", line=5
        ),
        attempt=0,
        model="claude-3-7-sonnet",
        allowed_tools=["Bash(*)"],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_protected=["features/", "tests/acceptance/"],
        paths_driver=["tests/acceptance/"],
        suppressions=["# noqa"],
        green_ids=[],
        check_commands=["check-command"],
        hints="Python 3.12",
    )

    with pytest.raises(SealTamperedError) as exc_info:
        use_case.execute(req)
    assert exc_info.value.exit_code == 10
    vcs.reset_to_head.assert_not_called()
    executor.run.assert_not_called()


def test_implement_scenario_success(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text(
        """Feature: IVA
  @IVA-001
  Scenario: IVA general
    Given total 100
""",
        encoding="utf-8",
    )

    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    agent_result = AgentResult(
        status="ok",
        num_turns=2,
        duration_ms=800,
        tokens=Tokens(input=50, output=20),
        cost_usd=0.005,
    )
    executor.run.return_value = agent_result

    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = True

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:35:00Z"

    use_case = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = ImplementScenarioRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        scenario=Scenario(
            id=ScenarioId("IVA-001"), name="IVA general", file=str(feature_file), line=3
        ),
        attempt=0,
        model="claude-3-7-sonnet",
        allowed_tools=["Bash(*)"],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_protected=["features/", "tests/acceptance/"],
        paths_driver=["tests/acceptance/"],
        suppressions=["# noqa"],
        green_ids=[],
        check_commands=["check-command"],
        hints="Python 3.12",
    )

    result = use_case.execute(req)

    vcs.reset_to_head.assert_called_once()
    assert result.attempt == 1
    assert result.agent_signal == "ok"
    assert result.agent_result.status == "ok"

    event_log.append.assert_called_once()
    event = event_log.append.call_args[0][0]
    assert isinstance(event, Event)
    assert event.type == "lictor.agent.finished.v1"
    assert event.scenarioid == "IVA-001"
    assert event.data["role"] == "implement"
    assert event.data["attempt"] == 1


def test_implement_scenario_spec_conflict(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text("Feature: IVA\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(
        status="ok",
        spec_conflict="Contradicción con IVA-002",
    )

    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = True

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:35:00Z"

    use_case = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = ImplementScenarioRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        scenario=Scenario(
            id=ScenarioId("IVA-001"), name="IVA general", file=str(feature_file), line=1
        ),
        attempt=1,
        model="claude-3-7-sonnet",
        allowed_tools=[],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_protected=["features/"],
        paths_driver=["tests/acceptance/"],
        suppressions=[],
        green_ids=[],
        check_commands=[],
        hints="",
        last_error="Assertion failed",
        failed_at="L2 (unit)",
    )

    result = use_case.execute(req)
    assert result.attempt == 2
    assert result.agent_signal == "spec_conflict"


def test_implement_scenario_rate_limited_does_not_increment_attempt(tmp_path: Path) -> None:
    feature_file = tmp_path / "features" / "iva.feature"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text("Feature: IVA\n", encoding="utf-8")

    vcs = MagicMock(spec=Vcs)
    executor = MagicMock(spec=Executor)
    executor.run.return_value = AgentResult(status="rate_limited")

    spec_source = MagicMock(spec=SpecSource)
    spec_source.verify_seal.return_value = True

    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-30T14:35:00Z"

    use_case = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=event_log,
        clock=clock,
    )

    req = ImplementScenarioRequest(
        project_root=tmp_path,
        run_id="20261030-140000-abcd",
        scenario=Scenario(
            id=ScenarioId("IVA-001"), name="IVA general", file=str(feature_file), line=1
        ),
        attempt=1,
        model="claude-3-7-sonnet",
        allowed_tools=[],
        max_turns=40,
        timeout_s=600,
        paths_writable=["src/"],
        paths_protected=["features/"],
        paths_driver=["tests/acceptance/"],
        suppressions=[],
        green_ids=[],
        check_commands=[],
        hints="",
    )

    result = use_case.execute(req)
    assert result.attempt == 1  # Not incremented on rate_limited!
    assert result.agent_signal == "rate_limited"
