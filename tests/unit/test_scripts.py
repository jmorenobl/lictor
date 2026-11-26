"""The repository's scripts, loaded by path because they aren't part of the package."""

import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_TOOL = """#!/bin/sh
echo "$0 $*" >> "{log}"
case "$1" in
  export)
    while [ "$1" != "--output-file" ]; do shift; done
    echo "paquete==1.0" > "$2"
    ;;
  pip-audit)
    cat "$3" >> "{log}"
    exit {audit_exit}
    ;;
esac
"""


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_tools(tmp_path: Path, audit_exit: int) -> Path:
    log = tmp_path / "llamadas"
    for name in ("uv", "uvx"):
        tool = tmp_path / name
        tool.write_text(FAKE_TOOL.format(log=log, audit_exit=audit_exit), encoding="utf-8")
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return log


@pytest.mark.parametrize("audit_exit", [0, 1])
def test_the_audit_reads_the_locked_requirements_and_reports_their_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audit_exit: int
) -> None:
    log = _fake_tools(tmp_path, audit_exit)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")

    assert _load("audit_dependencies").main() == audit_exit

    calls = log.read_text(encoding="utf-8")
    assert "export --frozen --no-emit-project --format requirements-txt" in calls
    assert "pip-audit -r" in calls
    assert "--disable-pip --require-hashes" in calls
    assert "paquete==1.0" in calls, "pip-audit debía leer lo exportado de uv.lock"


def test_the_audit_stops_if_the_export_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "uv").write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    (tmp_path / "uv").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")

    with pytest.raises(subprocess.CalledProcessError):
        _load("audit_dependencies").main()


FAKE_MUTMUT = """#!/bin/sh
echo "$*" >> llamadas
case "$1" in
  run) exit {run_exit} ;;
  export-cicd-stats)
    mkdir -p mutants
    echo '{{"killed": 3, "total": 4}}' > mutants/mutmut-cicd-stats.json
    ;;
  results) echo "    lictor.domain.rules.x_f__mutmut_1: survived" ;;
esac
"""


@pytest.mark.parametrize("run_exit", [0, 3])
def test_mutation_score_exports_the_counts_and_keeps_the_pass_exit_code(
    tmp_path: Path, run_exit: int
) -> None:
    tool = tmp_path / "mutmut"
    tool.write_text(FAKE_MUTMUT.format(run_exit=run_exit), encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    report = tmp_path / "informe" / "score.json"
    report.parent.mkdir()
    report.write_text("viejo", encoding="utf-8")

    code = _load("mutation_score").main([str(report)], mutmut=[str(tool)], root=tmp_path)

    assert code == run_exit
    calls = (tmp_path / "llamadas").read_text(encoding="utf-8")
    assert calls == "run --max-children 4\nexport-cicd-stats\nresults\n"
    assert report.read_text(encoding="utf-8").strip() == '{"killed": 3, "total": 4}'
    survivors = report.with_name("mutation-survivors.txt").read_text(encoding="utf-8")
    assert survivors == "    lictor.domain.rules.x_f__mutmut_1: survived\n"


def test_the_mutation_pass_is_bounded_in_memory_and_in_workers(tmp_path: Path) -> None:
    """A runaway mutant gets a MemoryError instead of filling the machine (2026-11-16, 13,8 GB)."""
    tool = tmp_path / "mutmut"
    tool.write_text(
        FAKE_MUTMUT.format(run_exit=0).replace(
            'echo "$*" >> llamadas', 'echo "$*" >> llamadas\n[ "$1" = run ] && ulimit -d > limite'
        ),
        encoding="utf-8",
    )
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    script = _load("mutation_score")

    script.main([str(tmp_path / "score.json")], mutmut=[str(tool)], root=tmp_path)

    calls = (tmp_path / "llamadas").read_text(encoding="utf-8").splitlines()
    assert calls[0] == f"run --max-children {script.MAX_CHILDREN}"
    if sys.platform == "linux":
        limit_kb = int((tmp_path / "limite").read_text(encoding="utf-8"))
        assert limit_kb == script.WORKER_MEMORY // 1024
    assert script.MAX_CHILDREN * script.WORKER_MEMORY <= 6 * 1024**3


def test_mutation_score_leaves_no_report_when_nothing_was_exported(tmp_path: Path) -> None:
    report = tmp_path / "score.json"
    report.write_text("viejo", encoding="utf-8")

    code = _load("mutation_score").main([str(report)], mutmut=["false"], root=tmp_path)

    assert code == 1
    assert not report.exists()


FAILED_EXPORT = """#!/bin/sh
case "$1" in
  export-cicd-stats) exit 4 ;;
esac
"""


def test_mutation_score_does_not_pass_off_an_old_score_when_the_export_fails(
    tmp_path: Path,
) -> None:
    tool = tmp_path / "mutmut"
    tool.write_text(FAILED_EXPORT, encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    stats = tmp_path / "mutants" / "mutmut-cicd-stats.json"
    stats.parent.mkdir()
    stats.write_text('{"killed": 3, "total": 3}', encoding="utf-8")
    report = tmp_path / "score.json"

    code = _load("mutation_score").main([str(report)], mutmut=[str(tool)], root=tmp_path)

    assert code == 4
    assert not report.exists()
    assert not stats.exists()


def test_mutation_score_fails_if_the_export_leaves_no_file(tmp_path: Path) -> None:
    report = tmp_path / "score.json"

    code = _load("mutation_score").main([str(report)], mutmut=["true"], root=tmp_path)

    assert code == 1
    assert not report.exists()


def test_mutation_score_needs_the_report_path() -> None:
    assert _load("mutation_score").main([]) == 2


def test_mutation_score_keeps_the_score_when_the_survivors_cant_be_listed(tmp_path: Path) -> None:
    tool = tmp_path / "mutmut"
    script = FAKE_MUTMUT.format(run_exit=0).replace(
        '  results) echo "    lictor.domain.rules.x_f__mutmut_1: survived" ;;',
        "  results) exit 7 ;;",
    )
    tool.write_text(script, encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    report = tmp_path / "score.json"
    report.with_name("mutation-survivors.txt").write_text("de otra pasada", encoding="utf-8")

    code = _load("mutation_score").main([str(report)], mutmut=[str(tool)], root=tmp_path)

    assert code == 0
    assert report.is_file()
    assert not report.with_name("mutation-survivors.txt").exists()


JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.unit.test_rules" name="test_fast" time="0.01"/>
  <testcase classname="tests.integration.test_cli" name="test_slow[a]" time="12.5"/>
  <testcase classname="tests.integration.test_cli" name="test_slower" time="30"/>
  <testcase classname="tests.integration.repro.test_baseline" name="test_c0" time="48.6"/>
  <testcase classname="tests.unit.test_rules" name="test_just_under" time="10"/>
</testsuite></testsuites>
"""


def _job(name: str, created: str, started: str, completed: str | None) -> dict[str, object]:
    return {
        "name": name,
        "created_at": f"2026-11-12T11:{created}Z",
        "started_at": f"2026-11-12T11:{started}Z",
        "completed_at": f"2026-11-12T11:{completed}Z" if completed else None,
    }


JOBS = {
    "jobs": [
        _job("Puertas (ubuntu-24.04, Python 3.12)", "00:00", "00:06", "01:06"),
        _job("Repro (macos-15, Python 3.14, parte 1 de 3)", "00:00", "10:00", "21:00"),
        _job("Lictor se verifica a sí mismo", "00:00", "00:06", "05:06"),
        _job("Mutación", "00:00", "00:06", "10:36"),
        _job("Presupuesto", "00:00", "21:10", None),
    ]
}


def test_slow_tests_names_only_the_cases_outside_repro_over_the_limit() -> None:
    warnings = _load("ci_budget").slow_tests(JUNIT)

    assert warnings == [
        "tests.integration.test_cli::test_slower tarda 30.0 s: más de 10 s sin la marca repro",
        "tests.integration.test_cli::test_slow[a] tarda 12.5 s: más de 10 s sin la marca repro",
    ]


def test_a_job_over_its_budget_warns_and_its_queue_is_shown_apart() -> None:
    budget = _load("ci_budget")
    jobs = budget.finished_jobs(JOBS)

    assert [job.name for job in jobs] == [
        "Puertas (ubuntu-24.04, Python 3.12)",
        "Repro (macos-15, Python 3.14, parte 1 de 3)",
        "Lictor se verifica a sí mismo",
        "Mutación",
    ]
    assert budget.job_warnings(jobs) == [
        "Repro (macos-15, Python 3.14, parte 1 de 3): 11.0 min, presupuesto 10 min "
        "(cola aparte: 10.0 min)",
        "Mutación: 10.5 min, presupuesto 10 min (cola aparte: 0.1 min)",
    ]


def test_the_queue_alone_never_breaks_the_critical_path() -> None:
    budget = _load("ci_budget")
    queued = {"jobs": [_job("Puertas (macos-15, Python 3.14)", "00:00", "30:00", "32:00")]}

    assert budget.job_warnings(budget.finished_jobs(queued)) == []


def test_a_critical_path_over_its_budget_warns() -> None:
    budget = _load("ci_budget")
    slow = {"jobs": [_job("Otro trabajo", "00:00", "00:00", "13:00")]}

    assert budget.job_warnings(budget.finished_jobs(slow)) == [
        "Camino crítico sin cola: 13.0 min, presupuesto 12 min"
    ]


def test_the_summary_shows_every_job_and_the_path_with_and_without_queue() -> None:
    budget = _load("ci_budget")

    text = budget.summary(budget.finished_jobs(JOBS))

    assert "| Repro (macos-15, Python 3.14, parte 1 de 3) | 11.0 | 10.0 | 10 |" in text
    assert "| Lictor se verifica a sí mismo | 5.0 | 0.1 | 6 |" in text
    assert "| Mutación | 10.5 | 0.1 | 10 |" in text
    assert text.endswith("Camino crítico: 11.0 min sin cola (presupuesto 12), 21.0 min con ella.\n")


def test_jobs_warns_writes_the_summary_and_still_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps(JOBS), encoding="utf-8")
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))

    code = _load("ci_budget").main(["jobs", str(jobs)])

    assert code == 0
    assert capsys.readouterr().out.startswith("::warning::Repro (macos-15")
    assert "Camino crítico" in step_summary.read_text(encoding="utf-8")


def test_slow_tests_still_passes_with_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(JUNIT, encoding="utf-8")

    assert _load("ci_budget").main(["slow-tests", str(junit)]) == 0
    assert capsys.readouterr().out.count("::warning::") == 2


def test_ci_budget_needs_a_known_command_and_a_path() -> None:
    budget = _load("ci_budget")

    assert budget.main([]) == 2
    assert budget.main(["otro", "x"]) == 2


def test_the_ci_names_match_the_budgets() -> None:
    """A renamed job would silently lose its budget."""
    workflows = REPO_ROOT / ".github" / "workflows"
    text = "".join(
        (workflows / name).read_text(encoding="utf-8") for name in ("ci.yml", "suite-completa.yml")
    )
    names = [
        line.split("name:", 1)[1].strip() for line in text.splitlines() if "    name: " in line
    ]
    budgeted = [prefix for prefix, _ in _load("ci_budget").JOB_BUDGETS_MIN]

    assert all(any(name.startswith(prefix) for name in names) for prefix in budgeted), names


def _answer(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["gh", "api"], code, out, err)


def test_the_api_is_asked_again_after_a_failure_and_its_answer_used() -> None:
    budget = _load("ci_budget")
    answers = [_answer(1, err="gh: HTTP 502: Bad Gateway"), _answer(0, out=json.dumps(JOBS))]
    asked: list[str] = []
    waits: list[float] = []

    def ask(endpoint: str) -> subprocess.CompletedProcess[str]:
        asked.append(endpoint)
        return answers.pop(0)

    fetched = budget.fetch_jobs("repos/r/x", ask=ask, wait=waits.append)

    assert fetched == JOBS
    assert asked == ["repos/r/x", "repos/r/x"]
    assert waits == [budget.API_RETRY_WAITS_S[0]]


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (_answer(1, err="algo\ngh: HTTP 502: Bad Gateway\n"), "gh: HTTP 502: Bad Gateway"),
        (_answer(0, out="<html>502</html>"), "la respuesta no es JSON"),
        (_answer(0, out="[]"), "la respuesta no es un objeto JSON"),
        (_answer(1), "sin mensaje"),
    ],
)
def test_an_api_that_never_answers_well_gives_a_failure_with_its_last_reason(
    answer: subprocess.CompletedProcess[str], reason: str
) -> None:
    budget = _load("ci_budget")
    waits: list[float] = []

    fetched = budget.fetch_jobs("e", ask=lambda _: answer, wait=waits.append)

    assert fetched == budget.ApiFailure(attempts=3, reason=reason)
    assert waits == list(budget.API_RETRY_WAITS_S)
    assert "\n" not in fetched.warning
    assert "El presupuesto no se comprobó" in fetched.warning


def test_an_api_that_runs_out_of_time_is_asked_again() -> None:
    budget = _load("ci_budget")

    def ask(endpoint: str) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["gh", "api", endpoint], budget.API_TIMEOUT_S)

    fetched = budget.fetch_jobs("e", ask=ask, wait=lambda _: None)

    assert fetched == budget.ApiFailure(attempts=3, reason="sin respuesta en 60 s")


def test_a_long_api_error_is_cut_for_its_annotation() -> None:
    budget = _load("ci_budget")

    fetched = budget.fetch_jobs("e", ask=lambda _: _answer(1, err="x" * 500), wait=lambda _: None)

    assert isinstance(fetched, budget.ApiFailure)
    assert len(fetched.reason) == 200
    assert fetched.reason.endswith("…")


def test_no_gh_at_all_is_the_job_s_own_failure_and_is_not_hidden() -> None:
    budget = _load("ci_budget")

    def ask(endpoint: str) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    with pytest.raises(FileNotFoundError):
        budget.fetch_jobs("e", ask=ask, wait=lambda _: None)


def _fake_gh(tmp_path: Path, script: str) -> None:
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)


def _api_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, Path]:
    budget = _load("ci_budget")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.setattr(budget, "API_RETRY_WAITS_S", (0.0, 0.0))
    step_summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))
    return budget, step_summary


def test_api_jobs_warns_and_passes_when_the_api_keeps_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_gh(tmp_path, 'echo "gh: HTTP 502: Bad Gateway" >&2; exit 1')
    budget, step_summary = _api_jobs(tmp_path, monkeypatch)

    assert budget.main(["api-jobs", "repos/r/actions/runs/1/attempts/1/jobs"]) == 0
    out = capsys.readouterr().out
    assert out == (
        "::warning::Sin datos de los trabajos: la API de GitHub falló 3 veces "
        "(gh: HTTP 502: Bad Gateway). El presupuesto no se comprobó en esta ejecución.\n"
    )
    assert "Sin datos de los trabajos" in step_summary.read_text(encoding="utf-8")


def test_api_jobs_checks_the_budget_with_what_the_api_gives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answer = tmp_path / "jobs.json"
    answer.write_text(json.dumps(JOBS), encoding="utf-8")
    _fake_gh(tmp_path, f'[ "$1 $2" = "api repos/r/jobs" ] && cat "{answer}"')
    budget, step_summary = _api_jobs(tmp_path, monkeypatch)

    assert budget.main(["api-jobs", "repos/r/jobs"]) == 0
    assert capsys.readouterr().out.startswith("::warning::Repro (macos-15")
    assert "Camino crítico" in step_summary.read_text(encoding="utf-8")


def test_the_ci_asks_the_api_through_the_script() -> None:
    """A step of its own around `gh api` failed the job on a 502, before the script ran."""
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "ci_budget.py api-jobs" in ci
    assert "gh api" not in ci


def _long_job(name: str, started: str, completed: str | None) -> dict[str, object]:
    """A job created at 10:00 that may run longer than an hour."""
    return {
        "name": name,
        "created_at": "2026-11-12T10:00:00Z",
        "started_at": f"2026-11-12T{started}:00Z",
        "completed_at": f"2026-11-12T{completed}:00Z" if completed else None,
    }


FULL_SUITE = {
    "jobs": [
        _long_job("Suite completa / Suite completa (macos-15, Python 3.12)", "10:05", "11:35"),
        _long_job("Suite completa / Suite completa (ubuntu-24.04, Python 3.13)", "10:01", "10:41"),
        _long_job("Suite completa / Presupuesto", "11:36", None),
    ]
}


def test_the_full_suite_has_a_budget_per_job_and_no_critical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Its jobs run apart: only the one over 60 min warns, and no path of a pull request."""
    answer = tmp_path / "jobs.json"
    answer.write_text(json.dumps(FULL_SUITE), encoding="utf-8")
    _fake_gh(tmp_path, f'cat "{answer}"')
    budget, step_summary = _api_jobs(tmp_path, monkeypatch)

    assert budget.main(["full-suite-jobs", "repos/r/jobs"]) == 0

    assert capsys.readouterr().out == (
        "::warning::Suite completa / Suite completa (macos-15, Python 3.12): 90.0 min, "
        "presupuesto 60 min (cola aparte: 5.0 min)\n"
    )
    text = step_summary.read_text(encoding="utf-8")
    assert (
        "| Suite completa / Suite completa (ubuntu-24.04, Python 3.13) | 40.0 | 1.0 | 60 |" in text
    )
    assert "Camino crítico" not in text


def test_the_full_suite_steals_work_and_checks_its_budget() -> None:
    """With the default distribution macOS ran every repro case in one worker, over 90 min."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "suite-completa.yml").read_text(
        encoding="utf-8"
    )
    runs = [line for line in workflow.splitlines() if "uv run pytest" in line]

    assert len(runs) == 3
    assert all("-n auto --dist worksteal" in line for line in runs), runs
    assert "ci_budget.py full-suite-jobs" in workflow
