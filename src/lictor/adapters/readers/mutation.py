"""Mutation report reader: how many mutants each outcome had, as JSON."""

from pathlib import Path

from lictor.adapters.readers.files import json_report
from lictor.application.ports import ReportReader
from lictor.domain.reports import MutationReport, ReportOf, mutation_report


class MutationReader(ReportReader[MutationReport]):
    """Read the counts a mutation tool exported; whether they are enough is the domain's to say."""

    def read(self, path: Path) -> ReportOf[MutationReport]:
        return json_report(path, mutation_report)
