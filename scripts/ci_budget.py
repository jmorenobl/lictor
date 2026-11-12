"""Warn, without failing, when the CI goes over its time budget.

Two uses:

    ci_budget.py slow-tests <junit.xml>   tests outside `repro` slower than SLOW_TEST_S
    ci_budget.py jobs <jobs.json>         each job, and the critical path, against its budget

`jobs.json` is what `gh api repos/<repo>/actions/runs/<id>/attempts/<n>/jobs` returns. Each finding
is a GitHub annotation (`::warning::`), and `jobs` also appends a table to `$GITHUB_STEP_SUMMARY`.
Both exit 0 whatever they find: a budget is an alarm, not a gate, and a timeout (the cut against a
hung job) is set apart and much looser. A runner can be up to 1.8 times slower than another, so a
gate set at the budget would fail work that didn't grow.

The queue is shown apart: `timeout-minutes` doesn't count it, and it depends on GitHub's capacity
at that moment, not on the suite.
"""

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from defusedxml import ElementTree

SLOW_TEST_S = 10.0
REPRO_CLASSNAME = "tests.integration.repro"
# Minutes each job may take on its runner, by the start of its name, and the critical path of a
# pull request without the queue. Keep them with the table of AGENTS.md («What to run and when»).
JOB_BUDGETS_MIN = (
    ("Puertas", 3.0),
    ("Repro", 10.0),
    ("Lictor se verifica", 8.0),
    ("Dependencias", 3.0),
)
CRITICAL_PATH_MIN = 12.0
USAGE = "Uso: ci_budget.py slow-tests <junit.xml> | ci_budget.py jobs <jobs.json>"


@dataclass(frozen=True)
class Job:
    name: str
    created: datetime
    started: datetime
    completed: datetime

    @property
    def run_min(self) -> float:
        return (self.completed - self.started).total_seconds() / 60

    @property
    def queue_min(self) -> float:
        return (self.started - self.created).total_seconds() / 60


def slow_tests(junit: str, threshold_s: float = SLOW_TEST_S) -> list[str]:
    """The cases outside `repro` slower than the threshold, slowest first."""
    found: list[tuple[float, str]] = []
    for case in ElementTree.fromstring(junit).iter("testcase"):
        classname = case.get("classname", "")
        seconds = float(case.get("time", "0") or 0)
        if seconds > threshold_s and not classname.startswith(REPRO_CLASSNAME):
            found.append((seconds, f"{classname}::{case.get('name', '')}"))
    found.sort(reverse=True)
    return [
        f"{name} tarda {seconds:.1f} s: más de {threshold_s:.0f} s sin la marca repro"
        for seconds, name in found
    ]


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def finished_jobs(payload: dict[str, object]) -> list[Job]:
    """The jobs that ran to the end; the one reading them is still running, and is left out."""
    raw = payload.get("jobs")
    jobs: list[Job] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not item.get("completed_at") or not item.get("started_at"):
            continue
        jobs.append(
            Job(
                name=str(item.get("name", "")),
                created=_time(str(item.get("created_at") or item["started_at"])),
                started=_time(str(item["started_at"])),
                completed=_time(str(item["completed_at"])),
            )
        )
    return jobs


def budget_of(name: str) -> float | None:
    return next((minutes for prefix, minutes in JOB_BUDGETS_MIN if name.startswith(prefix)), None)


def job_warnings(jobs: Sequence[Job]) -> list[str]:
    """Each job over its budget, and the critical path over its own; the queue never counts."""
    warnings = []
    for job in jobs:
        budget = budget_of(job.name)
        if budget is not None and job.run_min > budget:
            warnings.append(
                f"{job.name}: {job.run_min:.1f} min, presupuesto {budget:.0f} min "
                f"(cola aparte: {job.queue_min:.1f} min)"
            )
    critical = max((job.run_min for job in jobs), default=0.0)
    if critical > CRITICAL_PATH_MIN:
        warnings.append(
            f"Camino crítico sin cola: {critical:.1f} min, presupuesto {CRITICAL_PATH_MIN:.0f} min"
        )
    return warnings


def summary(jobs: Sequence[Job]) -> str:
    """A table of every job, with the critical path with and without the queue."""
    lines = [
        "| Trabajo | Ejecución (min) | Cola (min) | Presupuesto (min) |",
        "|:--|--:|--:|--:|",
    ]
    for job in sorted(jobs, key=lambda job: -job.run_min):
        budget = budget_of(job.name)
        shown = "—" if budget is None else f"{budget:.0f}"
        lines.append(f"| {job.name} | {job.run_min:.1f} | {job.queue_min:.1f} | {shown} |")
    if jobs:
        without_queue = max(job.run_min for job in jobs)
        start = min(job.created for job in jobs)
        with_queue = (max(job.completed for job in jobs) - start).total_seconds() / 60
        lines.append("")
        lines.append(
            f"Camino crítico: {without_queue:.1f} min sin cola (presupuesto "
            f"{CRITICAL_PATH_MIN:.0f}), {with_queue:.1f} min con ella."
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str]) -> int:
    if len(argv) != len(("command", "path")) or argv[0] not in ("slow-tests", "jobs"):
        print(USAGE, file=sys.stderr)
        return 2
    text = Path(argv[1]).read_text(encoding="utf-8")
    if argv[0] == "slow-tests":
        warnings = slow_tests(text)
    else:
        jobs = finished_jobs(json.loads(text))
        warnings = job_warnings(jobs)
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            with Path(step_summary).open("a", encoding="utf-8") as out:
                out.write(summary(jobs))
    for warning in warnings:
        print(f"::warning::{warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
