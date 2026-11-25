"""Run the profile's survey on HEAD, in the verification environment, and read its report.

The survey reads the files of a clean clone and runs none of them (ADR-0015): it is Lictor's
module, run with `-I -B` like the environment's own questions, so it needs no `IntegritySnapshot`.
What it can't do (no report, one that can't be read) is a reason, never an exception: an analysis
of Lictor mustn't stop a project that is fine.
"""

import dataclasses
import json
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor.application.ports import CiReader, ReportReader, Vcs
from lictor.application.run_files import save_output, verification_tree, write_text
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.ci import CiStep, CiSteps
from lictor.domain.reports import InvalidReport, MissingReport
from lictor.domain.survey import SurveyReport

SURVEY_TIMEOUT_S = 120
INPUT_NAME = "survey-in.json"
REPORT_NAME = "survey.json"
LOG_NAME = "survey.log"


@dataclass(frozen=True)
class SurveyedCommandSpec:
    """A command the survey is asked about: a control's, or the acceptance's."""

    id: str
    run: str


def _step_payload(step: CiStep) -> dict[str, object]:
    return {
        "file": step.file,
        "line": step.line,
        "run": step.run,
        "origin": step.origin,
        "pass_filenames": step.pass_filenames,
        "files": list(step.files),
        "exclude": list(step.exclude),
    }


@dataclass(frozen=True)
class StartSurvey:
    """The profile's survey command, what it is asked about and how its report is read.

    With `ci`, the steps of the project's CI are read from the same clone and handed to the
    survey too, which recognises them with the rules it uses for the controls (F76).
    """

    run: str
    commands: Sequence[SurveyedCommandSpec]
    detect: Sequence[str]
    reader: ReportReader[SurveyReport]
    ci: CiReader | None = None

    def execute(
        self, environment: VerificationEnvironment, vcs: Vcs, project_root: Path, evidence: Path
    ) -> SurveyReport | str:
        """Survey HEAD and return its facts, or why there are none; evidence goes to `evidence`."""
        evidence.mkdir(parents=True, exist_ok=True)
        given = evidence / INPUT_NAME
        report = evidence / REPORT_NAME
        report.unlink(missing_ok=True)
        tokens = [
            token.replace("{in}", str(given)).replace("{out}", str(report))
            for token in shlex.split(self.run)
        ]
        with verification_tree(vcs, "HEAD", project_root) as tree:
            ci = self.ci.read(tree) if self.ci is not None else CiSteps()
            payload: dict[str, object] = {
                "commands": [{"id": c.id, "run": c.run} for c in self.commands],
                "detect": list(self.detect),
            }
            if self.ci is not None:
                payload["ci"] = [_step_payload(step) for step in ci.steps]
            write_text(given, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            output = environment.run(tokens, cwd=tree, timeout_s=SURVEY_TIMEOUT_S)
        save_output(evidence / LOG_NAME, tokens, output)
        if output.timed_out:
            return f"el análisis no terminó en {SURVEY_TIMEOUT_S} s"
        if output.returncode != 0:
            return f"el análisis terminó con el código {output.returncode} ({evidence / LOG_NAME})"
        read = self.reader.read(report)
        if isinstance(read, MissingReport):
            return "el análisis no dejó informe"
        if isinstance(read, InvalidReport):
            return f"el informe del análisis no es válido: {read.reason}"
        return dataclasses.replace(read, ci_unreadable=ci.unreadable)
