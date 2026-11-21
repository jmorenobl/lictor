"""Complexity report reader: every function the complexity command measured, as JSON."""

import json
from pathlib import Path

from lictor.adapters.readers.files import json_text, report_bytes
from lictor.application.ports import ReportReader
from lictor.domain.reports import ComplexityReport, InvalidReport, ReportOf, complexity_report


class ComplexityReader(ReportReader[ComplexityReport]):
    """Read each function and its value; which are above the limit is the domain's to say."""

    def read(self, path: Path) -> ReportOf[ComplexityReport]:
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
        return complexity_report(data)
