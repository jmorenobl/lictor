"""Unit tests for domain models (Tramo 2)."""

from pathlib import Path

import pytest

from lictor.domain.errors import ExitCode, PreconditionError, SpecError
from lictor.domain.model import (
    AgentResult,
    AgentTask,
    Event,
    Health,
    Scenario,
    ScenarioId,
    Tokens,
    Totals,
)


def _set_attr(obj: object, name: str, val: object) -> None:
    setattr(obj, name, val)


class TestScenarioId:
    def test_valid_scenario_ids(self) -> None:
        sid1 = ScenarioId("IVA-001")
        assert sid1.value == "IVA-001"
        assert sid1.tag == "@IVA-001"
        assert sid1.normalized == "iva_001"
        assert str(sid1) == "IVA-001"

        sid2 = ScenarioId.from_tag("@AUTH-42")
        assert sid2.value == "AUTH-42"
        assert sid2.tag == "@AUTH-42"
        assert sid2.normalized == "auth_42"

        sid3 = ScenarioId("@CHECKOUT2-999")
        assert sid3.value == "CHECKOUT2-999"
        assert sid3.normalized == "checkout2_999"

    def test_invalid_scenario_ids_raise_value_error(self) -> None:
        invalid_ids = [
            "",
            "iva-001",
            "@iva-001",
            "123-1",
            "@123-1",
            "IVA_001",
            "@IVA_001",
            "@IVA",
            "IVA-",
            "@-001",
            "iva_001",
            "@IVA-001-extra",
        ]
        for item in invalid_ids:
            with pytest.raises(SpecError) as exc_info:
                ScenarioId(item)
            assert exc_info.value.exit_code == ExitCode.PRECONDITION
            assert isinstance(exc_info.value, (PreconditionError, ValueError))

            with pytest.raises(SpecError):
                ScenarioId.from_tag(item)

        with pytest.raises(SpecError, match="must be a string"):
            ScenarioId(123)  # type check violation in runtime

    def test_scenario_id_immutability_and_equality(self) -> None:
        sid1 = ScenarioId("IVA-001")
        sid2 = ScenarioId("IVA-001")
        sid3 = ScenarioId("IVA-002")
        assert sid1 == sid2
        assert sid1 != sid3
        assert hash(sid1) == hash(sid2)

        with pytest.raises((AttributeError, TypeError)):
            _set_attr(sid1, "value", "IVA-002")


class TestScenario:
    def test_scenario_creation_and_fields(self) -> None:
        sid = ScenarioId("IVA-001")
        sc = Scenario(id=sid, name="IVA general", file="features/iva.feature", line=3)
        assert sc.id == sid
        assert sc.name == "IVA general"
        assert sc.file == "features/iva.feature"
        assert sc.line == 3

    def test_scenario_with_string_id(self) -> None:
        sc = Scenario(id="IVA-001", name="IVA general", file="features/iva.feature", line=3)
        assert isinstance(sc.id, ScenarioId)
        assert sc.id.value == "IVA-001"

    def test_scenario_immutability(self) -> None:
        sc = Scenario(
            id=ScenarioId("IVA-001"),
            name="IVA general",
            file="features/iva.feature",
            line=3,
        )
        with pytest.raises((AttributeError, TypeError)):
            _set_attr(sc, "name", "Other")


class TestTokens:
    def test_tokens_defaults_and_addition(self) -> None:
        t0 = Tokens()
        assert t0.input == 0
        assert t0.output == 0
        assert t0.cache_read == 0
        assert t0.cache_write == 0

        t1 = Tokens(input=10, output=20, cache_read=30, cache_write=40)
        t2 = Tokens(input=1, output=2, cache_read=3, cache_write=4)
        t3 = t1 + t2

        assert t3.input == 11
        assert t3.output == 22
        assert t3.cache_read == 33
        assert t3.cache_write == 44

    def test_tokens_immutability(self) -> None:
        t = Tokens(input=5)
        with pytest.raises((AttributeError, TypeError)):
            _set_attr(t, "input", 10)


class TestTotals:
    def test_totals_accumulation(self) -> None:
        totals = Totals()
        assert totals.tokens == Tokens(0, 0, 0, 0)
        assert totals.cost_usd == 0.0
        assert totals.agent_duration_ms == 0
        assert totals.verify_duration_ms == 0

        totals = totals.add_agent(
            tokens=Tokens(input=100, output=50, cache_read=200, cache_write=10),
            cost_usd=0.05,
            duration_ms=1200,
        )
        assert totals.tokens.input == 100
        assert totals.tokens.output == 50
        assert totals.tokens.cache_read == 200
        assert totals.tokens.cache_write == 10
        assert totals.cost_usd == 0.05
        assert totals.agent_duration_ms == 1200
        assert totals.verify_duration_ms == 0

        totals = totals.add_verify(duration_ms=300)
        assert totals.verify_duration_ms == 300

        t_other = Totals(
            tokens=Tokens(input=50, output=25, cache_read=100, cache_write=5),
            cost_usd=0.02,
            agent_duration_ms=500,
            verify_duration_ms=100,
        )
        combined = totals + t_other
        assert combined.tokens.input == 150
        assert combined.tokens.output == 75
        assert round(combined.cost_usd, 4) == 0.07
        assert combined.agent_duration_ms == 1700
        assert combined.verify_duration_ms == 400


class TestAgentTaskAndResult:
    def test_agent_task_creation(self, tmp_path: Path) -> None:
        raw_path = tmp_path / "raw.json"
        task = AgentTask(
            role="implement",
            prompt="Implement IVA calculator",
            prompt_sha="sha256:abcdef",
            cwd=tmp_path,
            model="claude-3-5-sonnet",
            allowed_tools=["Read", "Edit", "Write"],
            max_turns=40,
            timeout_s=1800,
            raw_path=raw_path,
        )
        assert task.role == "implement"
        assert task.allowed_tools == ("Read", "Edit", "Write")

    def test_agent_result_creation_and_defaults(self, tmp_path: Path) -> None:
        raw_path = tmp_path / "raw.json"
        res = AgentResult(
            status="ok",
            spec_conflict=None,
            text="Completed successfully",
            session_id="sess-123",
            num_turns=2,
            duration_ms=1500,
            tokens=Tokens(input=100, output=50),
            cost_usd=0.015,
            cost_kind="notional_subscription",
            raw_path=raw_path,
        )
        assert res.status == "ok"
        assert res.spec_conflict is None
        assert res.cost_kind == "notional_subscription"
        assert res.tokens.input == 100

    def test_agent_result_conflict(self) -> None:
        res = AgentResult(
            status="ok",
            spec_conflict="Contradiction in tax brackets",
            text="LICTOR: SPEC_CONFLICT Contradiction in tax brackets",
        )
        assert res.spec_conflict == "Contradiction in tax brackets"


class TestHealth:
    def test_health_model(self) -> None:
        h1 = Health(ok=True, cli_version="2.1.312", auth_status="subscription")
        assert h1.ok is True
        assert h1.cli_version == "2.1.312"
        assert h1.auth_status == "subscription"
        assert h1.message is None

        h2 = Health(ok=False, cli_version="", auth_status="none", message="CLI not found")
        assert h2.ok is False
        assert h2.message == "CLI not found"


class TestEvent:
    def test_cloudevents_serialization(self) -> None:
        evt = Event(
            id="evt-1",
            type="lictor.agent.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T12:00:00Z",
            runid="20261030-120000-a1b2",
            scenarioid="IVA-001",
            data={"status": "ok", "role": "implement"},
        )
        d = evt.to_dict()
        assert d["specversion"] == "1.0"
        assert d["id"] == "evt-1"
        assert d["type"] == "lictor.agent.finished.v1"
        assert d["source"] == "lictor/0.1.0"
        assert d["time"] == "2026-10-30T12:00:00Z"
        assert d["runid"] == "20261030-120000-a1b2"
        assert d["scenarioid"] == "IVA-001"
        assert d["data"] == {"status": "ok", "role": "implement"}

        restored = Event.from_dict(d)
        assert restored == evt
