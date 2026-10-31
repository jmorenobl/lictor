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
