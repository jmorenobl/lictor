"""Unit tests for FakeExecutor agent adapter."""

from dataclasses import replace
from pathlib import Path

import pytest

from lictor.adapters.agents.fake import FakeExecutor
from lictor.application.ports import Executor
from lictor.domain.model import AgentTask


def _sample_task(
    cwd: Path,
    raw_path: Path,
    prompt: str = "Objetivo: que pase el escenario IVA-001",
) -> AgentTask:
    return AgentTask(
        role="implement",
        prompt=prompt,
        prompt_sha="sha256:abc",
        cwd=cwd,
        model="fake-model",
        allowed_tools=["Read", "Edit", "Write"],
        max_turns=10,
        timeout_s=60,
        raw_path=raw_path,
    )


class TestFakeExecutor:
    def test_implements_executor_protocol(self) -> None:
        fake = FakeExecutor()
        assert isinstance(fake, Executor)

    def test_health(self) -> None:
        fake = FakeExecutor()
        health = fake.health()
        assert health.ok is True
        assert "fake" in health.cli_version
        assert health.auth_status == "subscription"

    def test_mode_good(self, tmp_path: Path) -> None:
        target_file = tmp_path / "src" / "calc.py"
        fake = FakeExecutor(
            mode="good",
            files_to_write={"good": {"src/calc.py": "def add(a, b): return a + b\n"}},
        )
        task = _sample_task(tmp_path, tmp_path / ".lictor" / "raw.json")
        res = fake.run(task)

        assert res.status == "ok"
        assert res.spec_conflict is None
        assert target_file.exists()
        assert target_file.read_text() == "def add(a, b): return a + b\n"
        assert res.tokens.input > 0
        assert res.cost_usd is not None
        assert res.cost_usd > 0
        assert res.raw_path == task.raw_path
        assert task.raw_path.exists()

    def test_mode_conflict(self, tmp_path: Path) -> None:
        fake = FakeExecutor(mode="conflict")
        task = _sample_task(tmp_path, tmp_path / ".lictor" / "raw.json")
        res = fake.run(task)

        assert res.status == "ok"
        assert res.spec_conflict is not None
        assert "contradice" in res.spec_conflict.lower() or "imposible" in res.spec_conflict.lower()
        assert "LICTOR: SPEC_CONFLICT" in res.text

    def test_mode_rate_limit(self, tmp_path: Path) -> None:
        fake = FakeExecutor(mode="rate_limit")
        task = _sample_task(tmp_path, tmp_path / ".lictor" / "raw.json")
        res = fake.run(task)

        assert res.status == "rate_limited"

    def test_mode_fail(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "src" / "fail.py"
        fake = FakeExecutor(
            mode="fail",
            files_to_write={"fail": {"src/fail.py": "syntax error: !!!"}},
        )
        task = _sample_task(tmp_path, tmp_path / ".lictor" / "raw.json")
        res = fake.run(task)

        assert res.status == "ok"
        assert bad_file.exists()

    def test_mode_cheat(self, tmp_path: Path) -> None:
        protected_file = tmp_path / "tests" / "acceptance" / "test_iva.py"
        fake = FakeExecutor(
            mode="cheat",
            files_to_write={"cheat": {"tests/acceptance/test_iva.py": "# hacked"}},
        )
        task = _sample_task(tmp_path, tmp_path / ".lictor" / "raw.json")
        res = fake.run(task)

        assert res.status == "ok"
        assert protected_file.exists()
        assert protected_file.read_text() == "# hacked"

    def test_mode_crash(self, tmp_path: Path) -> None:
        fake = FakeExecutor(mode="crash")
        task = _sample_task(tmp_path, tmp_path / ".lictor" / "raw.json")
        with pytest.raises(RuntimeError, match="Simulated FakeExecutor crash"):
            fake.run(task)

    def test_scenario_specific_modes(self, tmp_path: Path) -> None:
        fake = FakeExecutor(
            mode="good",
            scenario_modes={
                "IVA-002": "conflict",
                "IVA-003": "rate_limit",
            },
        )
        task1 = _sample_task(
            tmp_path, tmp_path / "raw1.json", prompt="Objetivo: que pase el escenario IVA-001"
        )
        task2 = _sample_task(
            tmp_path, tmp_path / "raw2.json", prompt="Objetivo: que pase el escenario IVA-002"
        )
        task3 = _sample_task(
            tmp_path, tmp_path / "raw3.json", prompt="Objetivo: que pase el escenario IVA-003"
        )

        assert fake.run(task1).status == "ok"
        assert fake.run(task2).spec_conflict is not None
        assert fake.run(task3).status == "rate_limited"

    def test_fixtures_dir_copy(self, tmp_path: Path) -> None:
        fixtures_dir = tmp_path / "fixtures"
        good_dir = fixtures_dir / "good" / "src"
        good_dir.mkdir(parents=True, exist_ok=True)
        (good_dir / "calc.py").write_text("def mul(a, b): return a * b\n", encoding="utf-8")

        project_dir = tmp_path / "project"
        project_dir.mkdir(parents=True, exist_ok=True)

        fake = FakeExecutor(mode="good", fixtures_dir=fixtures_dir)
        task = _sample_task(project_dir, project_dir / "raw.json")
        res = fake.run(task)

        assert res.status == "ok"
        copied_file = project_dir / "src" / "calc.py"
        assert copied_file.is_file()
        assert copied_file.read_text(encoding="utf-8") == "def mul(a, b): return a * b\n"

    def test_crash_on_role(self, tmp_path: Path) -> None:
        fake = FakeExecutor(mode="good", crash_on_role="driver")
        task_driver = AgentTask(
            role="driver",
            prompt="Genera driver para IVA-001",
            prompt_sha="sha256:abc",
            cwd=tmp_path,
            model="fake",
            allowed_tools=["Read", "Write"],
            max_turns=5,
            timeout_s=30,
            raw_path=tmp_path / "raw_driver.json",
        )
        with pytest.raises(RuntimeError, match="crash on role driver"):
            fake.run(task_driver)

        task_impl = _sample_task(tmp_path, tmp_path / "raw_impl.json")
        res = fake.run(task_impl)
        assert res.status == "ok"

    def test_crash_on_scenario(self, tmp_path: Path) -> None:
        fake = FakeExecutor(mode="good", crash_on_scenario="IVA-002")
        task_iva1 = _sample_task(
            tmp_path, tmp_path / "raw1.json", prompt="Objetivo: que pase IVA-001"
        )
        task_iva2 = _sample_task(
            tmp_path, tmp_path / "raw2.json", prompt="Objetivo: que pase IVA-002"
        )

        res1 = fake.run(task_iva1)
        assert res1.status == "ok"

        with pytest.raises(RuntimeError, match="crash on scenario IVA-002"):
            fake.run(task_iva2)

    def test_effect_runs_arbitrary_side_effects_with_attempt_number(self, tmp_path: Path) -> None:
        seen: list[tuple[str | None, int]] = []

        def effect(task: AgentTask, scenario_id: str | None, attempt: int) -> None:
            seen.append((scenario_id, attempt))
            (task.cwd / "binario.pyc").write_bytes(b"\xff\xfe")

        fake = FakeExecutor(effect=effect)
        fake.run(_sample_task(tmp_path, tmp_path / "raw1.json"))
        fake.run(_sample_task(tmp_path, tmp_path / "raw2.json"))

        assert seen == [("IVA-001", 1), ("IVA-001", 2)]
        assert fake.calls == ["IVA-001", "IVA-001"]
        assert (tmp_path / "binario.pyc").read_bytes() == b"\xff\xfe"

    def test_effect_can_override_status(self, tmp_path: Path) -> None:
        fake = FakeExecutor(effect=lambda _task, _scenario, _attempt: "rate_limited")
        result = fake.run(_sample_task(tmp_path, tmp_path / "raw.json"))
        assert result.status == "rate_limited"
        assert result.tokens.input == 0

    def test_effect_keys_driver_calls_by_role(self, tmp_path: Path) -> None:
        attempts: list[int] = []

        def effect(_task: AgentTask, scenario_id: str | None, attempt: int) -> None:
            assert scenario_id is None
            attempts.append(attempt)

        task = replace(_sample_task(tmp_path, tmp_path / "raw.json"), role="driver")
        fake = FakeExecutor(effect=effect)
        fake.run(task)
        fake.run(task)

        assert attempts == [1, 2]
        assert fake.calls == ["driver", "driver"]
