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
from lictor.domain.complexity import OverLimit
from lictor.domain.controls import CommandControl, ReaderControl
from lictor.domain.duplication import Duplication
from lictor.domain.errors import SealTamperedError
from lictor.domain.model import AgentResult, AgentTask, Event, Scenario, ScenarioId, TreeChange
from lictor.domain.refactor import Measure
from lictor.domain.reports import Clone, CloneOccurrence, FunctionComplexity

SCENARIO_TEXT = "@IVA-001\nEscenario: IVA general\n  Entonces el total es 121"


def _execute(
    tmp_path: Path, result: AgentResult, **overrides: Any
) -> tuple[RefactorScenarioResult, MagicMock, MagicMock, MagicMock]:
    executor = MagicMock(spec=Executor)
    executor.run.return_value = result
    if (run := overrides.pop("run", None)) is not None:
        executor.run.side_effect = run
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


_COMPLEXITY = CommandControl(
    id="complexity",
    level="L1",
    run="medir {out}",
    report="complexity",
    mode="measure",
    limit=10,
    agent_run="uv run python {lictor_files}/medir.py src",
)
_RULE = "Bash(uv run python {lictor_files}/medir.py:*)"


def _over(*values: int) -> dict[str, Measure]:
    functions = tuple(
        FunctionComplexity("src/iva.py", f"f{i}", i, v) for i, v in enumerate(values, 1)
    )
    over = OverLimit(10, functions)
    return {
        "complexity": Measure(misses=(("excess", over.excess), ("peak", over.peak)), findings=over)
    }


def test_the_refactor_hears_what_its_green_left_above_the_limit(tmp_path: Path) -> None:
    """ADR-0013, point 6: the functions, wherever they are, and the command to measure them."""
    tool = tmp_path / "perfil" / "medir.py"
    tool.parent.mkdir()
    tool.write_text("print('mide')\n", encoding="utf-8")
    seen: dict[str, Any] = {}

    def run(task: AgentTask) -> AgentResult:
        [rule] = [t for t in task.allowed_tools if "medir.py" in t]
        files = rule.removeprefix("Bash(uv run python ").removesuffix("/medir.py:*)")
        seen["copy"] = (Path(files) / "medir.py").read_text(encoding="utf-8")
        seen["outside"] = not Path(files).is_relative_to(tmp_path)
        return AgentResult(status="ok")

    _, executor, _, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        controls=[_COMPLEXITY],
        baseline=_over(12, 3),
        agent_files=[tool],
        allowed_tools=["Bash(pruebas:*)", _RULE],
        run=run,
    )

    task = _task(executor)
    assert "- src/iva.py:1 `f1`: 12" in task.prompt
    assert "Complejidad, con un límite de 10 por función" in task.prompt
    assert "{lictor_files}" not in task.prompt
    command = next(line for line in task.prompt.splitlines() if line.startswith("Mídela tú"))
    assert "/medir.py src`, sola" in command
    assert "Bash solo admite: pruebas, uv run python " in task.prompt
    assert seen == {"copy": "print('mide')\n", "outside": True}


def test_without_the_profiles_files_the_rule_and_the_command_go(tmp_path: Path) -> None:
    _, executor, _, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        controls=[_COMPLEXITY],
        baseline=_over(),
        allowed_tools=["Bash(pruebas:*)", _RULE],
    )

    task = _task(executor)
    assert list(task.allowed_tools) == ["Bash(pruebas:*)"]
    assert "- Ninguna función supera el límite." in task.prompt
    assert "Mídela tú" not in task.prompt


def test_a_green_without_a_measure_of_complexity_says_nothing_of_it(tmp_path: Path) -> None:
    _, executor, _, _ = _execute(tmp_path, AgentResult(status="ok"), controls=[_COMPLEXITY])

    assert "Complejidad, con un límite" not in _task(executor).prompt


_DUPLICATION = CommandControl(
    id="duplication",
    level="L1",
    run="repetido {out}",
    report="duplication",
    mode="measure",
    agent_run="uv run python {lictor_files}/repetido.py src",
)


def _clone(*files: str, tokens: int = 60) -> Clone:
    return Clone(tuple(CloneOccurrence(f, 1, 9, 0, tokens) for f in files))


def _repeated(*clones: Clone) -> dict[str, Measure]:
    in_clones = sum(o.tokens for c in clones for o in c.occurrences)
    duplication = Duplication(50, in_clones, clones, len(clones))
    return {"duplication": Measure(misses=(("duplicated", in_clones),), findings=duplication)}


def test_the_refactor_hears_both_measures_of_the_code_in_their_order(tmp_path: Path) -> None:
    """ADR-0014, points 6 and 7: one block per control, the clones of its files first."""
    tools = []
    for name in ("medir.py", "repetido.py"):
        tool = tmp_path / "perfil" / name
        tool.parent.mkdir(exist_ok=True)
        tool.write_text("", encoding="utf-8")
        tools.append(tool)

    _, executor, _, _ = _execute(
        tmp_path,
        AgentResult(status="ok"),
        controls=[_COMPLEXITY, _DUPLICATION],
        baseline={
            **_over(12),
            **_repeated(
                _clone("src/otro.py", "src/mas.py", tokens=90), _clone("src/iva.py", "src/b.py")
            ),
        },
        agent_files=tools,
        allowed_tools=[
            "Bash(pruebas:*)",
            _RULE,
            "Bash(uv run python {lictor_files}/repetido.py:*)",
        ],
    )

    prompt = _task(executor).prompt
    assert prompt.index("Complejidad, con un límite") < prompt.index("Código repetido")
    lines = prompt.splitlines()
    first = lines.index(next(line for line in lines if line.startswith("Código repetido"))) + 1
    assert lines[first : first + 2] == [
        "- src/iva.py:1-9 y src/b.py:1-9: 60 tokens",
        "- src/otro.py:1-9 y src/mas.py:1-9: 90 tokens",
    ]
    assert "más tokens en clones que el verde (300)" in prompt
    assert sum(line.startswith("Mídel") for line in lines) == 2
    assert "{medidas_del_codigo}" not in prompt
    assert "/repetido.py src" in prompt.split("Bash solo admite:")[1].splitlines()[0]


def test_a_green_without_a_measure_of_duplication_says_nothing_of_it(tmp_path: Path) -> None:
    _, executor, _, _ = _execute(tmp_path, AgentResult(status="ok"), controls=[_DUPLICATION])

    assert "Código repetido" not in _task(executor).prompt
