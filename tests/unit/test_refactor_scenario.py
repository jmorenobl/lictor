"""RefactorScenario: the call after each implemented green (ADR-0010), and what it left."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lictor.application.ports import Clock, EventLog, Executor, SpecSource, Vcs
from lictor.application.usecases.refactor_scenario import (
    RefactorScenario,
    RefactorScenarioRequest,
    RefactorScenarioResult,
)
from lictor.domain.controls import ReaderControl
from lictor.domain.errors import SealTamperedError
from lictor.domain.model import AgentResult, AgentTask, Event, Scenario, ScenarioId, TreeChange

SCENARIO_TEXT = "@IVA-001\nEscenario: IVA general\n  Entonces el total es 121"


def _execute(
    tmp_path: Path, result: AgentResult, **overrides: Any
) -> tuple[RefactorScenarioResult, MagicMock, MagicMock, MagicMock]:
    executor = MagicMock(spec=Executor)
    executor.run.return_value = result
    spec_source = MagicMock(spec=SpecSource)
    spec_source.scenario_text.return_value = SCENARIO_TEXT
    spec_source.verify_seal.return_value = overrides.pop("sealed", True)
    vcs = MagicMock(spec=Vcs)
    vcs.capture.return_value = "arbol"
    vcs.changes.return_value = overrides.pop("changes", [TreeChange("src/iva.py", "100644")])
    vcs.parent_of.return_value = "padre"
    vcs.changed_paths.return_value = ["src/iva.py", "tests/unit/test_iva.py"]
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-11-18T10:00:00Z"
    fields: dict[str, Any] = {
        "project_root": tmp_path,
        "run_id": "run",
        "scenario": Scenario(
            id=ScenarioId("IVA-001"), name="N", file="features/iva.feature", line=1
        ),
        "attempt_slot": 2,
        "refactor_of": "verde",
        "model": "m",
        "allowed_tools": [],
        "max_turns": 1,
        "timeout_s": 1,
        "paths_writable": ["src/", "tests/unit/"],
        "paths_protected": ["pyproject.toml"],
        "paths_driver": ["tests/acceptance/"],
        "suppressions": ["# noqa"],
        "green_ids": ["IVA-001"],
        "check_commands": ["pruebas"],
        "hints": "Contexto.",
        "refactor_hints": "- Funciones cortas.",
    }
    use_case = RefactorScenario(vcs, executor, spec_source, event_log, clock)
    res = use_case.execute(RefactorScenarioRequest(**{**fields, **overrides}))
    return res, executor, event_log, vcs


def _task(executor: MagicMock) -> AgentTask:
    task = executor.run.call_args.args[0]
    assert isinstance(task, AgentTask)
    return task


def _events(event_log: MagicMock) -> list[Event]:
    return [call.args[0] for call in event_log.append.call_args_list]


def test_the_prompt_names_the_scenario_its_commit_and_the_profiles_goals(tmp_path: Path) -> None:
    _, executor, _, vcs = _execute(tmp_path, AgentResult(status="ok"))

    task = _task(executor)
    assert task.role == "refactor"
    assert SCENARIO_TEXT in task.prompt
    assert "El commit de ese escenario cambió: src/iva.py, tests/unit/test_iva.py." in task.prompt
    assert "- Funciones cortas." in task.prompt
    assert "deben seguir funcionando: IVA-001" in task.prompt
    assert "{" not in task.prompt.replace("{out}", "")
    vcs.changed_paths.assert_called_once_with("padre", "verde")


def test_its_evidence_goes_beside_the_attempt_that_turned_it_green(tmp_path: Path) -> None:
    _, executor, event_log, _ = _execute(tmp_path, AgentResult(status="ok"))

    assert _task(executor).raw_path == (
        tmp_path / ".lictor" / "runs" / "run" / "IVA-001" / "002-refactor.json"
    )
    started, finished = _events(event_log)
    assert (started.data["role"], started.data["attempt"], started.data["max_attempts"]) == (
        "refactor",
        2,
        1,
    )
    assert (finished.data["role"], finished.data["attempt"]) == ("refactor", 2)


def test_every_measure_it_can_take_is_offered_with_its_command(tmp_path: Path) -> None:
    coverage = ReaderControl(
        id="coverage", level="L2", reads="unit", adds="--cov", agent_run="pruebas --cov"
    )

    _, executor, _, _ = _execute(tmp_path, AgentResult(status="ok"), controls=[coverage])

    prompt = _task(executor).prompt
    assert "  - coverage: `pruebas --cov`." in prompt
    assert "Bash solo admite: pruebas, pruebas --cov." in prompt


def test_a_call_that_changes_nothing_says_so(tmp_path: Path) -> None:
    res, _, _, _ = _execute(tmp_path, AgentResult(status="ok"), changes=[])

    assert (res.agent_signal, res.tree, res.changed) == ("ok", "arbol", False)


def test_a_call_with_changes_leaves_its_tree(tmp_path: Path) -> None:
    res, _, _, _ = _execute(tmp_path, AgentResult(status="ok"))

    assert (res.tree, res.changed, res.keeps) == ("arbol", True, False)


def test_a_failed_backend_keeps_the_attempt_and_counts_the_failure(tmp_path: Path) -> None:
    res, _, _, _ = _execute(tmp_path, AgentResult(status="error"))

    assert (res.agent_signal, res.keeps, res.agent_failures) == ("error", True, 1)


def test_a_tampered_seal_stops_before_the_agent(tmp_path: Path) -> None:
    with pytest.raises(SealTamperedError):
        _execute(tmp_path, AgentResult(status="ok"), sealed=False)
