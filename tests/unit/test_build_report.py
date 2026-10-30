from pathlib import Path

from lictor.application.ports import EventLog
from lictor.application.usecases.build_report import (
    BuildReport,
    BuildReportRequest,
)
from lictor.domain.model import Event


class InMemoryEventLog(EventLog):
    def __init__(self, events: list[Event]) -> None:
        self._events = events

    def append(self, event: Event) -> None:
        self._events.append(event)

    def events(self, run_id: str | None = None) -> list[Event]:
        if run_id is None:
            return list(self._events)
        return [e for e in self._events if e.runid == run_id]


def test_build_report_generates_markdown_with_metrics(tmp_path: Path) -> None:
    events = [
        Event(
            id="1",
            type="lictor.run.started.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:00Z",
            runid="run-1",
            data={
                "lictor_version": "0.1.0",
                "cli_version": "claude-code 1.0.0",
                "profile": "python-uv@0.1",
                "config_hash": "cfg123",
            },
        ),
        Event(
            id="2",
            type="lictor.spec.loaded.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:01Z",
            runid="run-1",
            data={
                "scenario_count": 2,
                "scenarios": [
                    {
                        "id": "IVA-001",
                        "name": "IVA general",
                        "file": "features/iva.feature",
                        "line": 5,
                    },
                    {
                        "id": "IVA-002",
                        "name": "IVA reducido",
                        "file": "features/iva.feature",
                        "line": 10,
                    },
                ],
            },
        ),
        # An earlier attempt of IVA-001 that failed and got repeated (deduplication check)
        Event(
            id="3",
            type="lictor.agent.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:10Z",
            runid="run-1",
            scenarioid="IVA-001",
            data={
                "role": "implement",
                "attempt": 1,
                "status": "ok",
                "model": "claude-3-5-sonnet",
                "prompt_sha": "sha_imp_1",
                "tokens": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0},
                "cost_usd": 0.001,
                "duration_ms": 1000,
            },
        ),
        Event(
            id="4",
            type="lictor.verify.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:15Z",
            runid="run-1",
            scenarioid="IVA-001",
            data={
                "attempt": 1,
                "ok": True,
                "failed_at": None,
                "control_durations": {"unit": 200},
                "reverted_files": [],
            },
        ),
        Event(
            id="5",
            type="lictor.scenario.green.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:20Z",
            runid="run-1",
            scenarioid="IVA-001",
            data={"commit": "abc1234", "attempt": 2},
        ),
        # IVA-002 failed and quarantined
        Event(
            id="6",
            type="lictor.agent.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:25Z",
            runid="run-1",
            scenarioid="IVA-002",
            data={
                "role": "implement",
                "attempt": 1,
                "status": "ok",
                "model": "claude-3-5-sonnet",
                "prompt_sha": "sha_imp_1",
                "tokens": {"input": 150, "output": 75, "cache_read": 0, "cache_write": 0},
                "cost_usd": 0.0015,
                "duration_ms": 1500,
            },
        ),
        Event(
            id="7",
            type="lictor.verify.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:30Z",
            runid="run-1",
            scenarioid="IVA-002",
            data={
                "attempt": 1,
                "ok": False,
                "failed_at": "L1",
                "control_durations": {"lint": 50},
                "reverted_files": [],
            },
        ),
        Event(
            id="8",
            type="lictor.scenario.quarantined.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:35Z",
            runid="run-1",
            scenarioid="IVA-002",
            data={"reason": "intentos agotados", "details": "SyntaxError in test"},
        ),
        Event(
            id="9",
            type="lictor.run.finished.v1",
            source="lictor/0.1.0",
            time="2026-10-30T10:00:40Z",
            runid="run-1",
            data={
                "exit_code": 30,
                "totals": {
                    "tokens": {"input": 250, "output": 125},
                    "cost_usd": 0.0025,
                    "agent_duration_ms": 2500,
                    "verify_duration_ms": 250,
                },
            },
        ),
    ]

    event_log = InMemoryEventLog(events)
    use_case = BuildReport(event_log=event_log)

    res = use_case.execute(BuildReportRequest(project_root=tmp_path, run_id="run-1"))

    assert res.report_path == tmp_path / ".lictor" / "report.md"
    assert res.report_path.is_file()

    md = res.report_path.read_text(encoding="utf-8")
    assert "IVA-001" in md
    assert "IVA-002" in md
    assert "| 2 |" in md
    assert "30" in md
    assert "abc1234" in md
    assert "intentos agotados" in md
    assert "SyntaxError in test" in md
    assert "Duración total" in md
    assert "claude-code 1.0.0" in md
    assert "sha_imp_1" in md
