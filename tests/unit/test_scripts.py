"""The repository's scripts, loaded by path because they aren't part of the package."""

import importlib.util
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
