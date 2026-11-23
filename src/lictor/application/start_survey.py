"""Run the profile's survey on HEAD, in the verification environment, and read its report.

The survey reads the files of a clean clone and runs none of them (ADR-0015): it is Lictor's
module, run with `-I -B` like the environment's own questions, so it needs no `IntegritySnapshot`.
What it can't do (no report, one that can't be read) is a reason, never an exception: an analysis
of Lictor mustn't stop a project that is fine.
"""

import json
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor.application.ports import ReportReader, Vcs
from lictor.application.run_files import save_output, verification_tree, write_text
from lictor.application.verification_env import VerificationEnvironment
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


@dataclass(frozen=True)
class StartSurvey:
    """The profile's survey command, what it is asked about and how its report is read."""

    run: str
    commands: Sequence[SurveyedCommandSpec]
    detect: Sequence[str]
    reader: ReportReader[SurveyReport]

    def execute(
        self, environment: VerificationEnvironment, vcs: Vcs, project_root: Path, evidence: Path
    ) -> SurveyReport | str:
        """Survey HEAD and return its facts, or why there are none; evidence goes to `evidence`."""
        evidence.mkdir(parents=True, exist_ok=True)
        given = evidence / INPUT_NAME
        report = evidence / REPORT_NAME
        report.unlink(missing_ok=True)
        payload = {
            "commands": [{"id": c.id, "run": c.run} for c in self.commands],
            "detect": list(self.detect),
        }
        write_text(given, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        tokens = [
            token.replace("{in}", str(given)).replace("{out}", str(report))
            for token in shlex.split(self.run)
        ]
        with verification_tree(vcs, "HEAD", project_root) as tree:
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
        return read
