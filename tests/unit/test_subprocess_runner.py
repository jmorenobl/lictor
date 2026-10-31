"""Unit tests for CommandRunner implementations: SubprocessRunner and FakeRunner."""

import sys
import time
from pathlib import Path

import pytest

from lictor.adapters.runner.fake import FakeRunner
from lictor.adapters.runner.subprocess_runner import SubprocessRunner
from lictor.domain.model import CommandOutput


def test_subprocess_runner_success(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    cmd = [sys.executable, "-c", "import sys; sys.stdout.write('hello'); sys.stderr.write('world')"]
    out = runner.run(cmd, cwd=tmp_path)

    assert out.returncode == 0
    assert out.stdout == "hello"
    assert out.stderr == "world"
    assert out.duration_ms >= 0


def test_subprocess_runner_applies_env_over_what_it_inherits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUITADA", "x")
    monkeypatch.setenv("CAMBIADA", "antes")
    monkeypatch.setenv("INTACTA", "y")
    monkeypatch.delenv("AUSENTE", raising=False)
    show = "import os; print(sorted((k, v) for k, v in os.environ.items() if k in {})); "
    names = "('QUITADA', 'CAMBIADA', 'INTACTA', 'NUEVA', 'AUSENTE')"
    env = {"QUITADA": None, "AUSENTE": None, "CAMBIADA": "despues", "NUEVA": "z"}

    out = SubprocessRunner().run([sys.executable, "-c", show.format(names)], cwd=tmp_path, env=env)

    assert out.stdout.strip() == str([("CAMBIADA", "despues"), ("INTACTA", "y"), ("NUEVA", "z")])


def test_subprocess_runner_nonzero_exit(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    cmd = [sys.executable, "-c", "import sys; sys.stderr.write('failed'); sys.exit(42)"]
    out = runner.run(cmd, cwd=tmp_path)

    assert out.returncode == 42
    assert out.stderr == "failed"


def test_subprocess_runner_timeout(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    # Sleep longer than timeout
    cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
    start = time.monotonic()
    out = runner.run(cmd, cwd=tmp_path, timeout_s=1)
    duration = time.monotonic() - start

    assert duration < 3.0  # Killed promptly
    assert out.returncode != 0
    assert "timed out" in out.stderr.lower()


def test_fake_runner_default_and_custom_response(tmp_path: Path) -> None:
    runner = FakeRunner()
    out_default = runner.run(["echo", "1"], cwd=tmp_path)
    assert out_default.returncode == 0
    assert out_default.stdout == ""

    runner.set_response(
        "pytest",
        CommandOutput(returncode=1, stdout="1 failed", stderr="error", duration_ms=120),
    )
    out_pytest = runner.run(["uv", "run", "pytest"], cwd=tmp_path)
    assert out_pytest.returncode == 1
    assert out_pytest.stdout == "1 failed"
    assert len(runner.calls) == 2


def test_fake_runner_queue(tmp_path: Path) -> None:
    runner = FakeRunner()
    resp1 = CommandOutput(returncode=0, stdout="first", stderr="")
    resp2 = CommandOutput(returncode=0, stdout="second", stderr="")
    runner.queue_response(resp1)
    runner.queue_response(resp2)

    assert runner.run(["cmd1"], cwd=tmp_path).stdout == "first"
    assert runner.run(["cmd2"], cwd=tmp_path).stdout == "second"
