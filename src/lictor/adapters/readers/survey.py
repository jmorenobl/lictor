"""Survey report reader: what the profile's survey saw of a project, as JSON (ADR-0015)."""

from pathlib import Path

from lictor.adapters.readers.files import json_report
from lictor.application.ports import ReportReader
from lictor.domain.reports import ReportOf
from lictor.domain.survey import SurveyReport, survey_report


class SurveyReader(ReportReader[SurveyReport]):
    """Read the survey's facts; what is a blind spot is the domain's to say."""

    def read(self, path: Path) -> ReportOf[SurveyReport]:
        return json_report(path, survey_report)
