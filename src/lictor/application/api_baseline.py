"""The public API of a base commit, measured as the candidates are (ADR-0017).

There is one base: the commit a run started from. The state keeps only its SHA; its report is
measured again from git, running the API control's own command in a clean clone of that commit,
and never read back from `runs/`, which no decision reads once a step is over. A report is kept in
memory by commit, so a run measures its base once; a base that couldn't be measured is measured
again next time, and says why meanwhile.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from lictor.application.ports import ReportReader, Vcs
from lictor.application.run_files import fill_report_paths, save_output, verification_tree
from lictor.domain.api import ApiBase
from lictor.domain.controls import CommandControl, Control
from lictor.domain.model import CommandOutput
from lictor.domain.reports import ApiReport, InvalidReport

REPORT_NAME = "api-base.json"
LOG_NAME = "api-base.log"

# Runs a command in a directory with its timeout; says what it changed of the run's state.
type RunIn = Callable[[Sequence[str], Path, int], tuple[CommandOutput, tuple[str, ...]]]


def api_control(controls: Sequence[Control]) -> CommandControl | None:
    """The control that measures the public API, if the configuration has one."""
    for control in controls:
        if isinstance(control, CommandControl) and control.report == "api":
            return control
    return None


@dataclass
class ApiBaseline:
    """How the API of a base is measured: the API control's command and reader, and what the
    person accepts to break (`api.accepted`)."""

    run: str
    timeout_s: int
    reader: ReportReader[ApiReport]
    accepted: tuple[str, ...] = ()
    _measured: dict[str, ApiReport] = field(default_factory=dict)

    def base(
        self, sha: str, vcs: Vcs, project_root: Path, run_in: RunIn, evidence: Path
    ) -> ApiBase:
        """The base at `sha`, measured once; its report and log go to `evidence`."""
        found = self._measured.get(sha)
        if found is None:
            measured = self._measure(sha, vcs, project_root, run_in, evidence)
            if isinstance(measured, str):
                return ApiBase(sha, measured, self.accepted)
            self._measured[sha] = found = measured
        return ApiBase(sha, found, self.accepted)

    def _measure(
        self, sha: str, vcs: Vcs, project_root: Path, run_in: RunIn, evidence: Path
    ) -> ApiReport | str:
        evidence.mkdir(parents=True, exist_ok=True)
        report = evidence / REPORT_NAME
        command = fill_report_paths([(self.run, report)])
        with verification_tree(vcs, sha, project_root) as tree:
            output, changed = run_in(command, tree, self.timeout_s)
        save_output(evidence / LOG_NAME, command, output)
        problem = _ended_badly(output, changed, self.timeout_s)
        if problem is not None:
            return problem
        read = self.reader.read(report)
        if isinstance(read, ApiReport):
            return read
        return (
            f"su informe no es válido: {read.reason}"
            if isinstance(read, InvalidReport)
            else "la medida no dejó informe"
        )


def _ended_badly(output: CommandOutput, changed: Sequence[str], timeout_s: int) -> str | None:
    """Why a measure of the base proves nothing, before its report is even read."""
    problems = (
        (bool(changed), f"su medida cambió el estado de la ejecución ({', '.join(changed)})"),
        (output.timed_out, f"la medida no terminó en {timeout_s} s"),
        (
            output.returncode != 0,
            f"la medida terminó con el código {output.returncode} ({LOG_NAME})",
        ),
    )
    return next((problem for happened, problem in problems if happened), None)
