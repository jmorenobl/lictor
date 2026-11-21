"""Coverage report reader: the totals coverage.py exports as JSON (`--cov-report=json:{out}`)."""

from pathlib import Path

from lictor.adapters.readers.files import json_report
from lictor.application.ports import ReportReader
from lictor.domain.reports import CoverageReport, ReportOf, coverage_report


class CoverageReader(ReportReader[CoverageReport]):
    """Read the totals of a coverage export; whether they are enough is the domain's to say.

    The score is computed from the counts, not taken from `percent_covered`, so it is the same
    number whichever version wrote the report.
    """

    def read(self, path: Path) -> ReportOf[CoverageReport]:
        return json_report(path, coverage_report)
