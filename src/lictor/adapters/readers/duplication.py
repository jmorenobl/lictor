"""Duplication report reader: every clone the duplication command found, as JSON (ADR-0014)."""

from pathlib import Path

from lictor.adapters.readers.files import json_report
from lictor.application.ports import ReportReader
from lictor.domain.reports import DuplicationReport, ReportOf, duplication_report


class DuplicationReader(ReportReader[DuplicationReport]):
    """Read each file and each clone; what counts against the refactor is the domain's to say."""

    def read(self, path: Path) -> ReportOf[DuplicationReport]:
        return json_report(path, duplication_report)
