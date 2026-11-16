"""A verification environment for tests that verify without building one."""

from collections.abc import Sequence
from pathlib import Path

from lictor.application.ports import CommandRunner
from lictor.domain.model import CommandOutput


class PlainEnvironment:
    """Runs each command with the runner as it is: nothing to build, and nothing ever changes.

    `installed_plugins` is what the commands would load; none unless a test says otherwise.
    """

    def __init__(self, runner: CommandRunner, installed_plugins: Sequence[str] = ()) -> None:
        self.runner = runner
        self._installed_plugins = tuple(installed_plugins)

    @property
    def installed_plugins(self) -> tuple[str, ...]:
        return self._installed_plugins

    def ensure(self, run_id: str) -> str | None:
        return None

    def run(self, cmd: Sequence[str], cwd: Path, timeout_s: int | None) -> CommandOutput:
        return self.runner.run(cmd, cwd=cwd, timeout_s=timeout_s)
