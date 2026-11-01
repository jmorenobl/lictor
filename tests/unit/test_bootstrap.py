import json
from pathlib import Path

from lictor.adapters.agents.fake import FakeExecutor
from lictor.adapters.store.clock import FixedClock
from lictor.adapters.store.jsonl_eventlog import JsonlEventLog
from lictor.bootstrap import bootstrap_init, bootstrap_runtime


def test_bootstrap_init(tmp_path: Path) -> None:
    init_uc = bootstrap_init(tmp_path)
    assert init_uc is not None


def test_bootstrap_runtime_with_existing_config(tmp_path: Path) -> None:
    config_file = tmp_path / "lictor.json"
    config_file.write_text(
        """{
  "profile": "python-uv@0.1",
  "backend": {
    "kind": "claude-cli",
    "model": "claude-3-5-sonnet",
    "auth": "subscription"
  },
  "limits": {
    "max_attempts": 3,
    "max_driver_attempts": 2,
    "error_max_chars": 4000
  },
  "paths": {
    "writable": ["src/"],
    "driver": ["tests/acceptance/"],
    "protected": ["features/", "lictor.json"]
  },
  "acceptance": {
    "run": "echo test {out}",
    "report": "junit",
    "driver_hints": "hints"
  },
  "controls": [
    {"id": "c1", "level": "L1", "run": "echo c1", "report": "exit-code"}
  ],
  "agent_tools": ["Bash(test:*)"],
  "hints": "project hints",
  "gitignore": [".venv/"]
}""",
        encoding="utf-8",
    )

    fake_exec = FakeExecutor(mode="good")
    clock = FixedClock("2026-10-30T10:00:00Z")
    event_log = JsonlEventLog(tmp_path / ".lictor" / "journal.jsonl")

    res = bootstrap_runtime(
        project_root=tmp_path,
        executor=fake_exec,
        clock=clock,
        event_log=event_log,
    )

    assert res.config.profile == "python-uv@0.1"
    assert res.executor is fake_exec
    assert res.graph_context.project_root == tmp_path
    assert res.graph_context.model == "claude-3-5-sonnet"
    assert len(res.graph_context.controls) == 1


def test_bootstrap_hands_the_acceptance_failure_codes_to_the_graph(tmp_path: Path) -> None:
    config = {
        "profile": "python-uv@0.1",
        "backend": {"kind": "fake", "model": "claude-sonnet-4-5-20250929"},
        "paths": {"writable": ["src/"], "driver": ["tests/"], "protected": ["lictor.json"]},
        "acceptance": {"run": "suite {out}", "report": "junit", "failure_exit_codes": [1, 2]},
        "controls": [],
        "agent_tools": [],
        "hints": "",
        "gitignore": [],
    }
    (tmp_path / "lictor.json").write_text(json.dumps(config), encoding="utf-8")

    runtime = bootstrap_runtime(tmp_path)

    assert runtime.graph_context.acceptance_failure_exit_codes == [1, 2]
