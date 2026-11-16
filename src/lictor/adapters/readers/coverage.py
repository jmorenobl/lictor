"""Coverage report reader: the totals coverage.py exports as JSON (`--cov-report=json:{out}`)."""

import json
from pathlib import Path

from lictor.adapters.readers.files import json_text, report_bytes
from lictor.application.ports import ReportReader
from lictor.domain.reports import CoverageReport, InvalidReport, ReportOf, coverage_report


class CoverageReader(ReportReader[CoverageReport]):
    """Read the totals of a coverage export; whether they are enough is the domain's to say.

    The score is computed from the counts, not taken from `percent_covered`, so it is the same
    number whichever version wrote the report.
    """

    def read(self, path: Path) -> ReportOf[CoverageReport]:
        raw = report_bytes(path)
        if not isinstance(raw, bytes):
            return raw
        text = json_text(raw)
        if isinstance(text, InvalidReport):
            return text
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return InvalidReport(f"no es JSON: {exc}")
        return coverage_report(data)
