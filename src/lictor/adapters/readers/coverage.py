"""Coverage report reader: the totals coverage.py exports as JSON (`--cov-report=json:{out}`)."""

import json
from collections.abc import Collection
from pathlib import Path

from lictor.application.ports import ResultReader
from lictor.domain.model import ControlResult
from lictor.domain.rules import coverage_score

_TOTALS = ("covered_lines", "num_statements", "covered_branches", "num_branches")


def _totals(raw: str) -> dict[str, int] | str:
    """Read the totals, or say why they can't be read."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"no es JSON: {exc}"
    totals = data.get("totals") if isinstance(data, dict) else None
    if not isinstance(totals, dict):
        return "no tiene 'totals'"
    counts: dict[str, int] = {}
    for name in _TOTALS:
        value = totals.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"'{name}' no es un número: {value!r}"
        counts[name] = value
    return counts


class CoverageReader(ResultReader):
    """Score a coverage run from the totals its tool exported to the report path.

    The score is computed from the counts, not taken from `percent_covered`, so it is the same
    number whichever version wrote the report.
    """

    def read(
        self,
        output: str,
        exit_code: int,
        duration_ms: int,
        empty_exit_codes: Collection[int] = (),
    ) -> ControlResult:
        """`output` is the report's path; a run with no tests to run passes without a score."""
        path = Path(output.strip())
        if not output.strip() or not path.is_file():
            if exit_code in empty_exit_codes:
                return ControlResult(ok=True, duration_ms=duration_ms)
            return ControlResult(
                ok=False, duration_ms=duration_ms, error="Sin informe de cobertura"
            )
        totals = _totals(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(totals, str):
            return ControlResult(
                ok=False,
                duration_ms=duration_ms,
                error=f"El informe de cobertura no es válido: {totals}",
            )
        score = coverage_score(totals)
        failed = exit_code != 0 and exit_code not in empty_exit_codes
        error = f"Los tests de la cobertura fallaron con el código {exit_code}" if failed else None
        return ControlResult(ok=not failed, duration_ms=duration_ms, error=error, score=score)
