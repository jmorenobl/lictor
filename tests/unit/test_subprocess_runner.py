"""Unit tests for CommandRunner implementations: SubprocessRunner and FakeRunner."""

import json
import re
import sys
import time
from pathlib import Path

import pytest

from lictor.adapters.runner.fake import FakeRunner
from lictor.adapters.runner.subprocess_runner import (
    HEAD_BYTES,
    LEFT_OPEN,
    TAIL_BYTES,
    SubprocessRunner,
)
from lictor.domain.model import CommandOutput


def test_subprocess_runner_success(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    cmd = [sys.executable, "-c", "import sys; sys.stdout.write('hello'); sys.stderr.write('world')"]
    out = runner.run(cmd, cwd=tmp_path)

    assert out.returncode == 0
    assert out.stdout == "hello"
    assert out.stderr == "world"
    assert out.duration_ms >= 0


def test_subprocess_runner_env_is_the_whole_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEREDADA", "x")
    show = "import os; print(os.environ.get('NUEVA'), os.environ.get('HEREDADA'))"

    out = SubprocessRunner().run(
        [sys.executable, "-B", "-c", show], cwd=tmp_path, env={"NUEVA": "z"}
    )

    assert out.stdout.split() == ["z", "None"]


def test_subprocess_runner_without_env_inherits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEREDADA", "x")
    show = "import os; print(os.environ['HEREDADA'])"

    out = SubprocessRunner().run([sys.executable, "-c", show], cwd=tmp_path)

    assert out.stdout.strip() == "x"


def test_subprocess_runner_reports_a_command_the_exact_env_cannot_find(tmp_path: Path) -> None:
    out = SubprocessRunner().run(["uv-no-existe"], cwd=tmp_path, env={"PATH": str(tmp_path)})

    assert out.returncode != 0
    assert "uv-no-existe" in out.stderr


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
    assert out.timed_out is True
    assert "Sin tiempo tras 1 s" in out.stderr


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


def test_subprocess_runner_replaces_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    code = "import os; os.write(1, b'a\\xffb'); os.write(2, b'c\\xfe')"
    out = SubprocessRunner().run([sys.executable, "-c", code], cwd=tmp_path)

    assert (out.returncode, out.stdout, out.stderr) == (0, "a\ufffdb", "c\ufffd")
    assert out.timed_out is False


ORPHAN = (
    "import subprocess, sys\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(6)'],"
    " start_new_session=True)\n"
    "print('hecho', flush=True)\n"
    "sys.exit(3)\n"
)


def test_subprocess_runner_returns_when_the_command_ends_despite_an_orphan(tmp_path: Path) -> None:
    started = time.monotonic()
    out = SubprocessRunner().run([sys.executable, "-c", ORPHAN], cwd=tmp_path, timeout_s=30)

    assert time.monotonic() - started < 2.5
    assert (out.returncode, out.stdout.strip(), out.timed_out) == (3, "hecho", False)
    assert LEFT_OPEN in out.stderr


def test_subprocess_runner_kills_the_group_when_the_command_ends(tmp_path: Path) -> None:
    out = SubprocessRunner().run(
        ["/bin/sh", "-c", "(sleep 1; touch marca) >/dev/null 2>&1 &"], cwd=tmp_path, timeout_s=10
    )
    time.sleep(2)

    assert out.returncode == 0
    assert not (tmp_path / "marca").exists()


def test_subprocess_runner_gives_the_command_no_input(tmp_path: Path) -> None:
    code = "import sys; print(repr(sys.stdin.read()))"
    started = time.monotonic()
    out = SubprocessRunner().run([sys.executable, "-c", code], cwd=tmp_path, timeout_s=5)

    assert time.monotonic() - started < 2
    assert (out.returncode, out.stdout.strip()) == (0, "''")


def test_subprocess_runner_honours_the_timeout_after_the_pipes_close(tmp_path: Path) -> None:
    code = "import os, time; os.close(1); os.close(2); time.sleep(5)"
    started = time.monotonic()
    out = SubprocessRunner().run([sys.executable, "-c", code], cwd=tmp_path, timeout_s=1)

    assert time.monotonic() - started < 2.5
    assert out.timed_out is True


def test_subprocess_runner_keeps_the_head_and_the_tail_on_whole_lines(tmp_path: Path) -> None:
    code = (
        "import json, sys\n"
        "line = 'x' * 1000\n"
        "for i in range(20 * 1024):\n"
        "    sys.stdout.write(json.dumps({'i': i, 'p': line}) + '\\n')\n"
    )
    out = SubprocessRunner().run([sys.executable, "-c", code], cwd=tmp_path, timeout_s=60)

    lines = out.stdout.splitlines()
    markers = [n for n, line in enumerate(lines) if line.startswith("[… ")]
    assert len(markers) == 1
    marker = lines[markers[0]]
    assert re.fullmatch(r"\[… \d+ bytes omitidos …\]", marker)
    records = [json.loads(line) for line in lines if not line.startswith("[… ")]
    assert records[0]["i"] == 0
    assert records[-1]["i"] == 20 * 1024 - 1
    assert len(out.stdout.encode()) <= HEAD_BYTES + TAIL_BYTES + len(marker.encode()) + 1
    omitted = int(marker.split()[1])
    kept = len(out.stdout.encode()) - len(marker.encode()) - 1
    assert kept + omitted == sum(
        len(json.dumps({"i": i, "p": "x" * 1000})) + 1 for i in range(20 * 1024)
    )


def test_subprocess_runner_keeps_short_output_whole(tmp_path: Path) -> None:
    out = SubprocessRunner().run([sys.executable, "-c", "print('a\\nb')"], cwd=tmp_path)

    assert out.stdout == "a\nb\n"
