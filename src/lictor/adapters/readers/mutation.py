"""Mutation report reader: how many mutants each outcome had, as JSON."""

import json
from pathlib import Path

from lictor.adapters.readers.files import json_text, report_bytes
from lictor.application.ports import ReportReader
from lictor.domain.reports import InvalidReport, MutationReport, ReportOf, mutation_report


class MutationReader(ReportReader[MutationReport]):
    """Read the counts a mutation tool exported; whether they are enough is the domain's to say."""

    def read(self, path: Path) -> ReportOf[MutationReport]:
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
        return mutation_report(data)
