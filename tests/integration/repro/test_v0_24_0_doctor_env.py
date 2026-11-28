"""G5: `doctor` keeps its environment from one run of it to the next, and builds it once.

Fable's B2 of the 0.24.0's PR: the environment was ensured with the kept key and then, after the
controls on HEAD, with `doctor`'s own id, so each invalidated the other and every `doctor` built it
twice. A `prepare` that only writes a line per build counts them; nothing here needs a toolchain.
"""

import json
from pathlib import Path

from typer.testing import CliRunner

from lictor.cli import app
from tests.integration.repro.harness import git, write

FEATURE = """# language: es
Característica: Nada

  @NAD-001
  Escenario: Nada
    Dado nada
    Cuando nada
    Entonces nada
"""


def _project(root: Path, log: Path) -> Path:
    config = {
        "version": "0.1",
        "profile": "generic@0.2",
        "backend": {"kind": "fake"},
        "hints": "",
        "paths": {
            "writable": ["src/"],
            "driver": ["acc/"],
            "protected": ["features/", "acc/", "lictor.json"],
        },
        "code_paths": ["src/"],
        "acceptance": {"run": "sh -c 'exit 0 #{out}'", "failure_exit_codes": [1]},
        "controls": [{"id": "ok", "level": "L1", "run": "true", "report": "exit-code"}],
        "agent_tools": [],
        "verification": {
            "prepare": f"sh -c 'echo construido >> {log}'",
        },
    }
    write(root, "features/nada.feature", FEATURE)
    write(root, "src/nada.txt", "nada\n")
    write(root, "lictor.json", json.dumps(config))
    git(root, "init", "-q", "-b", "master")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: inicio")
    return root


def _doctor(root: Path, *args: str) -> str:
    result = CliRunner().invoke(app, ["doctor", *args, "--cwd", str(root)])
    assert result.exit_code == 0, result.output
    return result.output


def test_doctor_builds_its_environment_once_and_then_reuses_it(tmp_path: Path) -> None:
    log = tmp_path / "construcciones.log"
    root = _project(tmp_path / "nada", log)

    first = _doctor(root)
    again = _doctor(root)

    assert log.read_text(encoding="utf-8").count("construido") == 1
    assert "reutilizado de un doctor anterior" not in first
    assert "✓ Entorno de verificación: reutilizado de un doctor anterior" in again


def test_doctor_rebuilds_it_when_head_changes_or_it_is_told_to(tmp_path: Path) -> None:
    log = tmp_path / "construcciones.log"
    root = _project(tmp_path / "nada", log)
    _doctor(root)

    write(root, "src/nada.txt", "otra cosa\n")
    git(root, "commit", "-qam", "chore: otra cosa")
    _doctor(root)
    _doctor(root, "--fresh")

    assert log.read_text(encoding="utf-8").count("construido") == 3
