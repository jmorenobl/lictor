"""What every test shares: independence from a verification that may be running this suite."""

import contextlib
import os
import signal
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Far beyond any wait under test and below the suite's `timeout`: a call that waited for one of
# these processes would take this long, and no load makes a correct call take it.
DETACHED_LIFE_S = 300

# Lictor's own verification runs this suite with these set. Left in place, a nested `uv sync` would
# rewrite the verification environment and a nested pytest would load its plugins. They are cleared
# per test, not at startup, because xdist workers still have to inherit them.
VERIFICATION_VARIABLES = (
    "UV_PROJECT_ENVIRONMENT",
    "UV_NO_SYNC",
    "UV_FROZEN",
    "UV_LINK_MODE",
    "UV_NO_ENV_FILE",
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
)


@pytest.fixture(autouse=True)
def _outside_any_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in VERIFICATION_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _lictor_home_outside_the_users(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test may write the real `~/.lictor`, where `lictor run` creates its worktrees.

    Lictor refuses a home under the temporary directory, and every directory pytest hands out is
    under it, so each test also gets a temporary directory of its own beside the home.
    """
    monkeypatch.setenv("TMPDIR", str(tmp_path_factory.mktemp("tmp")))
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.setenv("LICTOR_HOME", str(tmp_path_factory.mktemp("lictor-home")))


class Detached:
    """Processes a test leaves running on purpose, to prove a call returns without waiting for them.

    A call is judged by what it waited for, not by a wall-clock limit that a loaded machine can
    exceed (F22, F39): if it returned while the process is still alive, it didn't wait for it.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def code(self, name: str) -> str:
        """Python lines that start a sleeper in a session of its own, holding the caller's pipes."""
        pid_file = self.directory / f"{name}.pid"
        return (
            "import subprocess, sys\n"
            "_detached = subprocess.Popen([sys.executable, '-c', "
            f"'import time; time.sleep({DETACHED_LIFE_S})'], start_new_session=True)\n"
            f"open({str(pid_file)!r}, 'w').write(str(_detached.pid))\n"
        )

    def alive(self, name: str) -> bool:
        pid = int((self.directory / f"{name}.pid").read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def kill_all(self) -> None:
        for pid_file in self.directory.glob("*.pid"):
            with contextlib.suppress(ProcessLookupError, ValueError):
                os.kill(int(pid_file.read_text()), signal.SIGKILL)


@pytest.fixture
def detached(tmp_path: Path) -> Iterator[Detached]:
    directory = tmp_path / "procesos"
    directory.mkdir()
    processes = Detached(directory)
    try:
        yield processes
    finally:
        processes.kill_all()
