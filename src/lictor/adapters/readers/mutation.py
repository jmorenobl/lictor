"""Mutation report reader: how many mutants each outcome had, as JSON."""

import json
from collections.abc import Collection
from pathlib import Path

from lictor.application.ports import ResultReader
from lictor.domain.model import ControlResult
from lictor.domain.rules import mutation_score

_COUNTS = (
    "killed",
    "survived",
    "timeout",
    "no_tests",
    "suspicious",
    "segfault",
    "skipped",
    "total",
)


def _counts(raw: str) -> dict[str, int] | str:
    """Read the counts, or say why they can't be read."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"no es JSON: {exc}"
    if not isinstance(data, dict):
        return "no es un objeto JSON"
    counts: dict[str, int] = {}
    for name in _COUNTS:
        value = data.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return f"'{name}' no es un número de mutantes: {value!r}"
        counts[name] = value
    return counts


class MutationReader(ResultReader):
    """Score a mutation run from the counts its tool exported to the report path."""

    def read(
        self,
        output: str,
        exit_code: int,
        duration_ms: int,
        empty_exit_codes: Collection[int] = (),
    ) -> ControlResult:
        """`output` is the report's path; the run passes when it ended well and scored something."""
        path = Path(output.strip())
        if not output.strip() or not path.is_file():
            return ControlResult(ok=False, duration_ms=duration_ms, error="Sin informe de mutación")
        counts = _counts(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(counts, str):
            return ControlResult(
                ok=False,
                duration_ms=duration_ms,
                error=f"El informe de mutación no es válido: {counts}",
            )
        score = mutation_score(counts)
        if exit_code != 0:
            error: str | None = f"La pasada de mutación falló con el código {exit_code}"
        elif score.total == 0:
            error = "El informe de mutación no tiene mutantes"
        else:
            error = None
        return ControlResult(ok=error is None, duration_ms=duration_ms, error=error, score=score)
