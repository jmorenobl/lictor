"""Warn, without failing, when the CI goes over its time budget.

Two uses:

    ci_budget.py slow-tests <junit.xml>   tests outside `repro` slower than SLOW_TEST_S
    ci_budget.py jobs <jobs.json>         each job, and the critical path, against its budget
    ci_budget.py api-jobs <endpoint>      the same, asking GitHub's API for the jobs

`jobs.json` is what `gh api repos/<repo>/actions/runs/<id>/attempts/<n>/jobs` returns, and
`api-jobs` takes that endpoint. Each finding is a GitHub annotation (`::warning::`), and both job
commands also append a table to `$GITHUB_STEP_SUMMARY`. They exit 0 whatever they find: a budget is
an alarm, not a gate, and a timeout (the cut against a hung job) is set apart and much looser. A
runner can be up to 1.8 times slower than another, so a gate set at the budget would fail work that
didn't grow.

The API fails now and then (a 502 once failed the job): `api-jobs` asks again after a short wait,
and if it never answers it says so with a warning and checks nothing. A failure of the script itself
still fails the job.

The queue is shown apart: `timeout-minutes` doesn't count it, and it depends on GitHub's capacity
at that moment, not on the suite.
"""

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
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
# Seconds to wait before each new question to the API: three questions in all, within a minute.
API_RETRY_WAITS_S = (5.0, 15.0)
API_TIMEOUT_S = 60.0
USAGE = (
    "Uso: ci_budget.py slow-tests <junit.xml> | ci_budget.py jobs <jobs.json> "
    "| ci_budget.py api-jobs <endpoint>"
)


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


@dataclass(frozen=True)
class ApiFailure:
    """GitHub's API didn't give the jobs, after every retry."""

    attempts: int
    reason: str

    @property
    def warning(self) -> str:
        return (
            f"Sin datos de los trabajos: la API de GitHub falló {self.attempts} veces "
            f"({self.reason}). El presupuesto no se comprobó en esta ejecución."
        )


def gh_api(endpoint: str) -> subprocess.CompletedProcess[str]:
    """Ask GitHub's API through `gh`, which takes the token from `GH_TOKEN`."""
    return subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True,
        text=True,
        check=False,
        timeout=API_TIMEOUT_S,
    )


def _one_line(text: str, limit: int = 200) -> str:
    """The last line of an error, short enough for an annotation, which a newline would cut."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    last = lines[-1] if lines else "sin mensaje"
    return last if len(last) <= limit else last[: limit - 1] + "…"


def fetch_jobs(
    endpoint: str,
    ask: Callable[[str], subprocess.CompletedProcess[str]] = gh_api,
    wait: Callable[[float], None] = time.sleep,
) -> dict[str, object] | ApiFailure:
    """The jobs of the run, or why the API didn't give them after asking again.

    A failed call, one that ran out of time and an answer that isn't a JSON object are the API's;
    anything else (no `gh` at all, for one) is the job's own and is left to fail it.
    """
    reason = ""
    for attempt, delay in enumerate((0.0, *API_RETRY_WAITS_S)):
        if attempt:
            wait(delay)
        try:
            done = ask(endpoint)
        except subprocess.TimeoutExpired:
            reason = f"sin respuesta en {API_TIMEOUT_S:.0f} s"
            continue
        if done.returncode != 0:
            reason = _one_line(done.stderr or done.stdout)
            continue
        try:
            payload = json.loads(done.stdout)
        except json.JSONDecodeError:
            reason = "la respuesta no es JSON"
            continue
        if isinstance(payload, dict):
            return payload
        reason = "la respuesta no es un objeto JSON"
    return ApiFailure(attempts=1 + len(API_RETRY_WAITS_S), reason=reason)


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


def _append_summary(text: str) -> None:
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with Path(step_summary).open("a", encoding="utf-8") as out:
            out.write(text)


def _budget_warnings(payload: dict[str, object]) -> list[str]:
    jobs = finished_jobs(payload)
    _append_summary(summary(jobs))
    return job_warnings(jobs)


def main(argv: Sequence[str]) -> int:
    commands = ("slow-tests", "jobs", "api-jobs")
    if len(argv) != len(("command", "argument")) or argv[0] not in commands:
        print(USAGE, file=sys.stderr)
        return 2
    command, argument = argv
    if command == "slow-tests":
        warnings = slow_tests(Path(argument).read_text(encoding="utf-8"))
    elif command == "jobs":
        warnings = _budget_warnings(json.loads(Path(argument).read_text(encoding="utf-8")))
    else:
        fetched = fetch_jobs(argument)
        if isinstance(fetched, ApiFailure):
            _append_summary(f"{fetched.warning}\n")
            warnings = [fetched.warning]
        else:
            warnings = _budget_warnings(fetched)
    for warning in warnings:
        print(f"::warning::{warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
