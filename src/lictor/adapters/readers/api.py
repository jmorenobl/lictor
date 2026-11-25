"""API report reader: every public name the API command found, as JSON (ADR-0017)."""

from pathlib import Path

from lictor.adapters.readers.files import json_report
from lictor.application.ports import ReportReader
from lictor.domain.api import api_report
from lictor.domain.reports import ApiReport, ReportOf


class ApiReader(ReportReader[ApiReport]):
    """Read each public name; what breaks against a base is the domain's to say."""

    def read(self, path: Path) -> ReportOf[ApiReport]:
        return json_report(path, api_report)
