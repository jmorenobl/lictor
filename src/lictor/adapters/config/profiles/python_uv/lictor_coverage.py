"""What the tests leave without coverage, counted as Lictor counts it, and where it rose (F71).

Lictor copies this file next to each agent call that measures coverage. It runs the tests with
the arguments it gets, in the interpreter that runs it, with branch coverage of `src` (or of what
a `--cov=` among them names), and prints:

- coverage.py's usual table, with the total percentage the implementer's minimum is checked on;
- if the green's report is next to this file (`coverage-del-verde.json`, which Lictor leaves there
  for a refactor) and something rose against it, the functions, or the files when a report has no
  functions, where the lines or the branches without coverage rose and where they fell;
- last, one line with the two counts the refactor may not raise, and the green's.

The counts are Lictor's (`score_misses`): statements minus covered lines, and branches minus
covered branches, from the report's totals. It imports nothing of Lictor and stays valid back to
Python 3.9, as the project's interpreter may be.

    python -I -B lictor_coverage.py tests/unit -q
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

GREEN = "coverage-del-verde.json"
SHOWN = 10  # places listed of each sign: the last line holds the balance


def misses(summary: Mapping[str, Any]) -> tuple[int, int]:
    """Lines and branches a summary of coverage.py leaves without coverage."""
    lines = int(summary.get("num_statements", 0)) - int(summary.get("covered_lines", 0))
    branches = int(summary.get("num_branches", 0)) - int(summary.get("covered_branches", 0))
    return lines, branches


def _places(report: Mapping[str, Any], by_function: bool) -> dict[str, tuple[int, int]]:
    """What each function (or each file) of a report leaves without coverage."""
    found: dict[str, tuple[int, int]] = {}
    for path, data in report.get("files", {}).items():
        if not by_function:
            found[path] = misses(data.get("summary", {}))
            continue
        for name, function in data.get("functions", {}).items():
            found[f"{path}:{name or '(módulo)'}"] = misses(function.get("summary", {}))
    return found


def _has_functions(report: Mapping[str, Any]) -> bool:
    return any("functions" in data for data in report.get("files", {}).values())


def changes(current: Mapping[str, Any], green: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Where the lines or branches without coverage rose and fell against the green, biggest first.

    Code moved without coverage rises where it goes and falls where it was: both are shown, so the
    agent reads the balance, which is what the last line counts.
    """
    by_function = _has_functions(current) and _has_functions(green)
    now, before = _places(current, by_function), _places(green, by_function)
    up, down = [], []
    for place in sorted(set(now) | set(before)):
        lines, branches = now.get(place, (0, 0))
        old_lines, old_branches = before.get(place, (0, 0))
        more_lines, more_branches = lines - old_lines, branches - old_branches
        row = (abs(more_lines) + abs(more_branches), place, more_lines, more_branches)
        if more_lines > 0 or more_branches > 0:
            up.append(row)
        elif more_lines < 0 or more_branches < 0:
            down.append(row)
    return _rows(up), _rows(down)


def _rows(rows: list[tuple[int, str, int, int]]) -> list[str]:
    rows.sort(key=lambda row: (-row[0], row[1]))
    return [
        f"{place}: {lines:+d} líneas, {branches:+d} ramas" for _, place, lines, branches in rows
    ]


def summary_line(current: Mapping[str, Any], green: Mapping[str, Any] | None) -> str:
    """The last line: the two counts, and the green's when there is one."""
    lines, branches = misses(current.get("totals", {}))
    text = f"{lines} líneas y {branches} ramas sin cubrir"
    if green is None:
        return text + "."
    old_lines, old_branches = misses(green.get("totals", {}))
    return f"{text}; el verde, {old_lines} y {old_branches}."


def report_text(current: Mapping[str, Any], green: Mapping[str, Any] | None) -> list[str]:
    """What is printed after the table: where it rose and fell, at most `SHOWN` places each."""
    found = []
    if green is not None:
        up, down = changes(current, green)
        if up:
            found.append("Sube lo que queda sin cubrir frente al verde en:")
            found.extend(f"  {row}" for row in up[:SHOWN])
            if down:
                found.append("Y baja en:")
                found.extend(f"  {row}" for row in down[:SHOWN])
    found.append(summary_line(current, green))
    return found


def _read(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def main(argv: Sequence[str]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "coverage.json"
        # What to measure is `src` unless the arguments say it, as the control's `adds` may.
        target = [] if any(arg.startswith("--cov=") for arg in argv) else ["--cov=src"]
        command = [
            sys.executable,
            "-I",
            "-B",
            "-m",
            "pytest",
            *argv,
            *target,
            "--cov-branch",
            "--cov-report=term-missing",
            f"--cov-report=json:{report}",
        ]
        code = subprocess.run(command, check=False).returncode
        current = _read(report)
    if current is None:
        print("No se pudo medir la cobertura: los tests no dejaron informe.")
        return code or 2
    green = _read(Path(__file__).with_name(GREEN))
    for line in report_text(current, green):
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
