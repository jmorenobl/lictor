"""Complexity report reader: every function the complexity command measured, as JSON."""

from pathlib import Path

from lictor.adapters.readers.files import json_report
from lictor.application.ports import ReportReader
from lictor.domain.reports import ComplexityReport, ReportOf, complexity_report


class ComplexityReader(ReportReader[ComplexityReport]):
    """Read each function and its value; which are above the limit is the domain's to say."""

    def read(self, path: Path) -> ReportOf[ComplexityReport]:
        return json_report(path, complexity_report)
