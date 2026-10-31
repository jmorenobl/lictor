"""Subprocess implementation of the CommandRunner port."""

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from lictor.application.ports import CommandRunner
from lictor.domain.model import CommandOutput


class SubprocessRunner(CommandRunner):
    """Synchronous command runner executing processes directly without shell."""

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutput:
        """Execute a command list synchronously with process group timeout management."""
        start_time = time.monotonic()
        try:
            proc = subprocess.Popen(
                list(cmd),
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=None if env is None else dict(env),
                start_new_session=True,
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return CommandOutput(
                returncode=1,
                stdout="",
                stderr=f"Failed to start process: {exc}",
                duration_ms=duration_ms,
            )

        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return CommandOutput(
                returncode=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            duration_ms = int((time.monotonic() - start_time) * 1000)
            timeout_msg = f"Command timed out after {timeout_s} seconds"
            combined_stderr = f"{stderr}\n{timeout_msg}".strip() if stderr else timeout_msg
            return CommandOutput(
                returncode=-signal.SIGKILL,
                stdout=stdout,
                stderr=combined_stderr,
                duration_ms=duration_ms,
            )
