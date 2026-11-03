"""Subprocess implementation of the CommandRunner port."""

import contextlib
import os
import selectors
import signal
import subprocess
import time
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, cast

from lictor.application.ports import CommandRunner
from lictor.domain.model import CommandOutput

# What survives of each stream: the first MiB and the last 15. The start says how a command began
# and the end how it finished; a stream of JSON lines needs both, and never half a line.
HEAD_BYTES = 1 << 20
TAIL_BYTES = 15 << 20
# How long the pipes may stay open once the command is gone: whatever still holds them escaped its
# process group, and nothing outside a sandbox can reach it.
DRAIN_GRACE_S = 1.0
_CHUNK = 1 << 16
_POLL_S = 0.05
LEFT_OPEN = "La salida quedó abierta por un proceso que sobrevivió al comando; se dejó de leer."


class _Capture:
    """Keep the head and the tail of a stream, counting what falls in between."""

    def __init__(self) -> None:
        self.head = bytearray()
        self.tail: deque[bytes] = deque()
        self.tail_size = 0
        self.total = 0

    def add(self, data: bytes) -> None:
        self.total += len(data)
        room = HEAD_BYTES - len(self.head)
        if room > 0:
            self.head += data[:room]
            data = data[room:]
        if not data:
            return
        self.tail.append(data)
        self.tail_size += len(data)
        while self.tail_size - len(self.tail[0]) >= TAIL_BYTES:
            self.tail_size -= len(self.tail.popleft())

    def text(self) -> str:
        tail = b"".join(self.tail)[-TAIL_BYTES:]
        head = bytes(self.head)
        if self.total > len(head) + len(tail):
            # Cut on line breaks, so that no line reaches the result in pieces. A piece with no
            # line break stays raw: progress redrawn with `\r` is still the only clue it holds.
            if b"\n" in head:
                head = head[: head.rfind(b"\n") + 1]
            if b"\n" in tail:
                tail = tail[tail.find(b"\n") + 1 :]
            omitted = self.total - len(head) - len(tail)
            if head and not head.endswith(b"\n"):
                head += b"\n"
            head += f"[… {omitted} bytes omitidos …]\n".encode()
        return (head + tail).decode("utf-8", errors="replace")


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


class SubprocessRunner(CommandRunner):
    """Synchronous command runner executing processes directly without shell.

    Every wait is bounded: the command by its timeout, and its output by a short grace once the
    command has ended. Its whole process group is killed when it ends, however it ends.
    """

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
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=None if env is None else dict(env),
                start_new_session=True,
            )
        except OSError as exc:
            return CommandOutput(
                returncode=1,
                stdout="",
                stderr=f"No se pudo iniciar el proceso: {exc}",
                duration_ms=int((time.monotonic() - start_time) * 1000),
                started=False,
            )

        stdout, stderr, timed_out, left_open = self._collect(proc, start_time, timeout_s)
        notes = [stderr] if stderr else []
        if timed_out:
            notes.append(f"Sin tiempo tras {timeout_s} s")
        if left_open:
            notes.append(LEFT_OPEN)
        return CommandOutput(
            returncode=-signal.SIGKILL if timed_out else proc.returncode,
            stdout=stdout,
            stderr="\n".join(notes),
            duration_ms=int((time.monotonic() - start_time) * 1000),
            timed_out=timed_out,
        )

    @staticmethod
    def _collect(
        proc: subprocess.Popen[bytes], start_time: float, timeout_s: int | None
    ) -> tuple[str, str, bool, bool]:
        out, err = cast(IO[bytes], proc.stdout), cast(IO[bytes], proc.stderr)
        out_fd, err_fd = out.fileno(), err.fileno()
        captures = {out_fd: _Capture(), err_fd: _Capture()}
        deadline = None if timeout_s is None else start_time + timeout_s
        ended_at: float | None = None
        timed_out = False
        with selectors.DefaultSelector() as selector:
            for fd in captures:
                selector.register(fd, selectors.EVENT_READ)
            # The command may close both pipes and go on running: the loop waits for both.
            while selector.get_map() or ended_at is None:
                now = time.monotonic()
                expired = deadline is not None and now >= deadline
                if ended_at is None and expired and proc.poll() is None:
                    timed_out = True
                    _kill_group(proc.pid)
                    proc.wait()
                if ended_at is None and proc.poll() is not None:
                    _kill_group(proc.pid)
                    ended_at = now
                if ended_at is not None and now - ended_at >= DRAIN_GRACE_S:
                    break
                if not selector.get_map():
                    time.sleep(_POLL_S)
                    continue
                for key, _ in selector.select(_POLL_S):
                    data = os.read(key.fd, _CHUNK)
                    if data:
                        captures[key.fd].add(data)
                    else:
                        selector.unregister(key.fd)
            left_open = bool(selector.get_map())
        out.close()
        err.close()
        proc.wait()
        return captures[out_fd].text(), captures[err_fd].text(), timed_out, left_open
