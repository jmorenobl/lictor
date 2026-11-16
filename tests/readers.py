"""The report readers the composition root wires, for tests that run controls for real."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from lictor.adapters.readers.coverage import CoverageReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.application.ports import ReportReaders
from lictor.domain.model import CommandOutput


def all_readers() -> ReportReaders:
    return ReportReaders(junit=JUnitReader(), coverage=CoverageReader(), mutation=MutationReader())


class ReportingRunner(FakeRunner):
    """A fake runner that also leaves a report at `{out}`, the command's last argument."""

    def __init__(self) -> None:
        super().__init__()
        self._reports: dict[str, tuple[CommandOutput, str]] = {}

    def set_report(self, substring: str, output: CommandOutput, report: str) -> None:
        """For a command with `substring`: `report` at its `{out}`, and `output` as its answer."""
        self._reports[substring] = (output, report)

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutput:
        joined = " ".join(cmd)
        for substring, (output, report) in self._reports.items():
            if substring in joined:
                super().run(cmd, cwd, timeout_s, env)
                Path(cmd[-1]).write_text(report, encoding="utf-8")
                return output
        return super().run(cmd, cwd, timeout_s, env)
