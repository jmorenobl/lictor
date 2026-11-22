"""The profile's coverage measure for the agent (F71): Lictor's counts, and where they moved.

The module runs where Lictor isn't installed, so it repeats `score_misses` instead of importing
it: the first table fixes that both give the same counts for the same report.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_coverage
from lictor.domain.refactor import score_misses
from lictor.domain.rules import coverage_score

_TOTALS = [
    {"covered_lines": 0, "num_statements": 0, "covered_branches": 0, "num_branches": 0},
    {"covered_lines": 1813, "num_statements": 2531, "covered_branches": 333, "num_branches": 660},
    {"covered_lines": 10, "num_statements": 10},
    {"covered_lines": 3, "num_statements": 7, "covered_branches": 1, "num_branches": 4},
]


@pytest.mark.parametrize("totals", _TOTALS)
def test_its_counts_are_lictors(totals: dict[str, int]) -> None:
    lictors = dict(score_misses(coverage_score(totals)))
    assert lictor_coverage.misses(totals) == (lictors["lines"], lictors["branches"])


def _function(lines: int, branches: int) -> dict[str, Any]:
    return {"summary": {"num_statements": lines, "covered_lines": 0, "num_branches": branches}}


def _report(functions: dict[str, dict[str, tuple[int, int]]]) -> dict[str, Any]:
    """A report whose functions leave those lines and branches without coverage."""
    files: dict[str, Any] = {}
    lines = branches = 0
    for path, found in functions.items():
        files[path] = {
            "summary": {
                "num_statements": sum(n for n, _ in found.values()),
                "covered_lines": 0,
                "num_branches": sum(b for _, b in found.values()),
            },
            "functions": {name: _function(*counts) for name, counts in found.items()},
        }
        lines += sum(n for n, _ in found.values())
        branches += sum(b for _, b in found.values())
    totals = {"num_statements": lines, "covered_lines": 0, "num_branches": branches}
    return {"files": files, "totals": totals}


# The green and a refactor that moved code without coverage to a new module, as PRE-001 did.
_GREEN = _report({"src/a.py": {"f": (4, 2), "g": (2, 0)}})
_MOVED = _report({"src/a.py": {"g": (2, 0)}, "src/b.py": {"f": (4, 3)}})


def test_code_moved_without_coverage_rises_where_it_goes_and_falls_where_it_was() -> None:
    up, down = lictor_coverage.changes(_MOVED, _GREEN)

    assert up == ["src/b.py:f: +4 líneas, +3 ramas"]
    assert down == ["src/a.py:f: -4 líneas, -2 ramas"]


def test_the_last_line_is_the_balance_against_the_green() -> None:
    found = lictor_coverage.report_text(_MOVED, _GREEN)

    assert found[0] == "Sube lo que queda sin cubrir frente al verde en:"
    assert found[-1] == "6 líneas y 3 ramas sin cubrir; el verde, 6 y 2."


def test_without_a_rise_or_a_green_only_the_counts_are_printed() -> None:
    assert lictor_coverage.report_text(_GREEN, _GREEN) == [
        "6 líneas y 2 ramas sin cubrir; el verde, 6 y 2."
    ]
    assert lictor_coverage.report_text(_GREEN, None) == ["6 líneas y 2 ramas sin cubrir."]


def test_a_report_without_functions_is_compared_by_file() -> None:
    old = {"files": {"src/a.py": {"summary": {"num_statements": 3, "covered_lines": 1}}}}
    new = {"files": {"src/a.py": {"summary": {"num_statements": 4, "covered_lines": 1}}}}

    assert lictor_coverage.changes(new, old) == (["src/a.py: +1 líneas, +0 ramas"], [])


def test_it_runs_the_tests_and_prints_the_counts_last(tmp_path: Path) -> None:
    """A real run on a tiny project, with the green's report next to a copy of the module."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "uno.py").write_text(
        "def f(x):\n    if x:\n        return 1\n    return 2\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_uno.py").write_text(
        "import sys\nsys.path.append('src')\nimport uno\n\n\ndef test_f():\n    assert uno.f(1)\n",
        encoding="utf-8",
    )
    module = tmp_path / "files" / "lictor_coverage.py"
    module.parent.mkdir()
    module.write_bytes(Path(lictor_coverage.__file__).read_bytes())
    green = _report({"src/uno.py": {"f": (0, 0)}})
    (module.parent / lictor_coverage.GREEN).write_text(json.dumps(green), encoding="utf-8")

    done = subprocess.run(
        [sys.executable, "-I", "-B", str(module), "tests", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    lines = done.stdout.strip().splitlines()
    assert done.returncode == 0, done.stdout + done.stderr
    assert lines[-1] == "1 líneas y 1 ramas sin cubrir; el verde, 0 y 0."
    assert "src/uno.py:f: +1 líneas, +1 ramas" in done.stdout
