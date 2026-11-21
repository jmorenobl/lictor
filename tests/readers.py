"""The report readers the composition root wires, for tests that run controls for real."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor.adapters.readers.complexity import ComplexityReader
from lictor.adapters.readers.coverage import CoverageReader
from lictor.adapters.readers.duplication import DuplicationReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.application.ports import ReportReaders
from lictor.domain.model import CommandOutput


def all_readers() -> ReportReaders:
    return ReportReaders(
        junit=JUnitReader(),
        coverage=CoverageReader(),
        mutation=MutationReader(),
        complexity=ComplexityReader(),
        duplication=DuplicationReader(),
    )


def coverage_json(covered: int, statements: int) -> str:
    """The totals coverage.py exports, as a coverage reader reads them."""
    totals = {"covered_lines": covered, "num_statements": statements}
    return json.dumps({"totals": {**totals, "covered_branches": 0, "num_branches": 0}})


@dataclass
class Leaves:
    """What a command leaves: its JUnit and its coverage reports (None leaves none) and its end."""

    junit: str | None = None
    coverage: str | None = None
    returncode: int = 0
    timed_out: bool = False


class FlagRunner(FakeRunner):
    """A fake runner that writes each report where its flag says, as pytest would.

    The JUnit report goes after `--junitxml`, the coverage one after `--cov-report=json:`; what
    each command leaves is chosen by its first token. It tells the paths it got in `reports`.
    """

    JUNIT_FLAG = "--junitxml"
    COVERAGE_FLAG = "--cov-report=json:"

    def __init__(self, leaves: Mapping[str, Leaves]) -> None:
        super().__init__()
        self.leaves = dict(leaves)
        self.reports: list[Path] = []

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutput:
        super().run(cmd, cwd, timeout_s, env)
        leaves = self.leaves.get(cmd[0], Leaves())
        for i, token in enumerate(cmd):
            if token == self.JUNIT_FLAG:
                self._leave(Path(cmd[i + 1]), leaves.junit)
            elif token.startswith(self.COVERAGE_FLAG):
                self._leave(Path(token.removeprefix(self.COVERAGE_FLAG)), leaves.coverage)
        return CommandOutput(
            returncode=leaves.returncode,
            stdout=f"salida de {cmd[0]}",
            stderr="",
            timed_out=leaves.timed_out,
        )

    def _leave(self, path: Path, content: str | None) -> None:
        self.reports.append(path)
        if content is not None:
            path.write_text(content, encoding="utf-8")


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
