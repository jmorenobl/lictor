"""The repository's scripts, loaded by path because they aren't part of the package."""

import importlib.util
import json
import stat
import subprocess
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
    assert calls == "run\nexport-cicd-stats\nresults\n"
    assert report.read_text(encoding="utf-8").strip() == '{"killed": 3, "total": 4}'
    survivors = report.with_name("mutation-survivors.txt").read_text(encoding="utf-8")
    assert survivors == "    lictor.domain.rules.x_f__mutmut_1: survived\n"


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
    ]
    assert budget.job_warnings(jobs) == [
        "Repro (macos-15, Python 3.14, parte 1 de 3): 11.0 min, presupuesto 10 min "
        "(cola aparte: 10.0 min)",
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
    assert "| Lictor se verifica a sí mismo | 5.0 | 0.1 | 8 |" in text
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
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    names = [line.split("name:", 1)[1].strip() for line in ci.splitlines() if "    name: " in line]
    budgeted = [prefix for prefix, _ in _load("ci_budget").JOB_BUDGETS_MIN]

    assert all(any(name.startswith(prefix) for name in names) for prefix in budgeted), names
