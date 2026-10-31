"""In-memory fake implementation of CommandRunner for unit and integration testing."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from lictor.application.ports import CommandRunner
from lictor.domain.model import CommandOutput


class FakeRunner(CommandRunner):
    """Configurable fake command runner."""

    def __init__(
        self,
        default_response: CommandOutput | None = None,
    ) -> None:
        self.default_response = default_response or CommandOutput(
            returncode=0,
            stdout="",
            stderr="",
            duration_ms=0,
        )
        self.calls: list[dict[str, object]] = []
        self._pattern_responses: dict[str, CommandOutput] = {}
        self._queue: list[CommandOutput] = []

    def set_response(self, substring: str, output: CommandOutput) -> None:
        """Register a canned response when any command argument contains `substring`."""
        self._pattern_responses[substring] = output

    def queue_response(self, output: CommandOutput) -> None:
        """Enqueue a response to be popped on consecutive calls."""
        self._queue.append(output)

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: Mapping[str, str | None] | None = None,
    ) -> CommandOutput:
        """Record the call and return a canned or queued response."""
        self.calls.append(
            {
                "cmd": list(cmd),
                "cwd": cwd,
                "timeout_s": timeout_s,
                "env": env,
            }
        )

        if self._queue:
            return self._queue.pop(0)

        cmd_str = " ".join(cmd)
        for pattern, response in self._pattern_responses.items():
            if pattern in cmd_str:
                return response

        return self.default_response
