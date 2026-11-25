"""The contract of ADR-0009: what reading and judging a report does, row by row.

`characterization_reports.jsonl` was measured against master (`50af6a5`) before ADR-0009, by
calling the readers exactly as each site does (`characterization_reports.py`). Each line is one
case and what that site ends up with: the verdict, the message, the number of cases and the score.

The file is the contract. A deliberate change of ADR-0009 changes its rows by hand, so the diff of
this file is the list of what changed, and every other row must stay as it is.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from tests.unit import characterization_reports as c

_ROWS = [
    json.loads(line)
    for line in (Path(__file__).parent / "characterization_reports.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]


def _rows(table: str, *keys: str) -> Any:
    """The rows of one table, each with an id made of its inputs."""
    return [
        pytest.param(row, id=" | ".join(str(row[key]) for key in keys))
        for row in _ROWS
        if row["table"] == table
    ]


def _check(expected: dict[str, Any], seen: c.Seen | c.SeenAcceptance | c.SeenRed) -> None:
    assert asdict(seen) == expected


def test_every_row_belongs_to_a_known_table() -> None:
    tables = {
        "junit_control",
        "acceptance_run",
        "acceptance_check",
        "red",
        "exit_code",
        "coverage",
        "mutation",
        "complexity",
        "duplication",
        "api",
        "min_score",
        "timed_out",
    }
    assert {row["table"] for row in _ROWS} == tables


@pytest.mark.parametrize("row", _rows("junit_control", "report", "code", "empty"))
def test_junit_as_a_control(row: dict[str, Any], tmp_path: Path) -> None:
    report = c.leave_report(tmp_path / "r.xml", c.JUNIT_REPORTS[row["report"]])
    _check(row["expected"], c.junit_as_control(report, row["code"], tuple(row["empty"])))


@pytest.mark.parametrize("row", _rows("acceptance_run", "report", "code"))
def test_the_acceptance_of_a_run(row: dict[str, Any], tmp_path: Path) -> None:
    report = c.leave_report(tmp_path / "a.xml", c.JUNIT_REPORTS[row["report"]])
    _check(row["expected"], c.acceptance_of_a_run(report, row["code"]))


@pytest.mark.parametrize("row", _rows("acceptance_check", "report", "code"))
def test_the_acceptance_of_check(row: dict[str, Any], tmp_path: Path) -> None:
    report = c.leave_report(tmp_path / "a.xml", c.JUNIT_REPORTS[row["report"]])
    _check(row["expected"], c.acceptance_of_check(report, row["code"]))


@pytest.mark.parametrize("row", _rows("red", "report", "stdout", "code"))
def test_the_red_and_the_impostors(row: dict[str, Any], tmp_path: Path) -> None:
    report = c.leave_report(tmp_path / "r.xml", c.JUNIT_REPORTS[row["report"]])
    stdout = c.RED_STDOUTS[row["stdout"]]
    _check(row["expected"], c.acceptance_of_the_red(report, stdout, row["code"]))


@pytest.mark.parametrize("row", _rows("exit_code", "output", "code", "empty"))
def test_an_exit_code_control(row: dict[str, Any]) -> None:
    stdout, stderr = c.EXIT_OUTPUTS[row["output"]]
    _check(row["expected"], c.exit_code_control(stdout, stderr, row["code"], tuple(row["empty"])))


@pytest.mark.parametrize("row", _rows("coverage", "report", "code", "empty"))
def test_a_coverage_control(row: dict[str, Any], tmp_path: Path) -> None:
    report = c.leave_report(tmp_path / "c.json", c.COVERAGE_REPORTS[row["report"]])
    _check(row["expected"], c.coverage_control(report, row["code"], tuple(row["empty"])))


@pytest.mark.parametrize("row", _rows("mutation", "report", "code"))
def test_a_mutation_control(row: dict[str, Any], tmp_path: Path) -> None:
    report = c.leave_report(tmp_path / "m.json", c.MUTATION_REPORTS[row["report"]])
    _check(row["expected"], c.mutation_control(report, row["code"]))


@pytest.mark.parametrize("row", _rows("complexity", "report", "code"))
def test_a_complexity_control(row: dict[str, Any], tmp_path: Path) -> None:
    report = c.leave_report(tmp_path / "x.json", c.COMPLEXITY_REPORTS[row["report"]])
    _check(row["expected"], c.complexity_control(report, row["code"]))


@pytest.mark.parametrize("row", _rows("api", "report", "code"))
def test_an_api_control(row: dict[str, Any], tmp_path: Path) -> None:
    report = c.leave_report(tmp_path / "a.json", c.API_REPORTS[row["report"]])
    _check(row["expected"], c.api_control(report, row["code"]))


@pytest.mark.parametrize("row", _rows("duplication", "report", "code"))
def test_a_duplication_control(row: dict[str, Any], tmp_path: Path) -> None:
    report = c.leave_report(tmp_path / "d.json", c.DUPLICATION_REPORTS[row["report"]])
    _check(row["expected"], c.duplication_control(report, row["code"]))


@pytest.mark.parametrize("row", _rows("min_score", "case"))
def test_a_minimum_score(row: dict[str, Any]) -> None:
    _check(row["expected"], c.min_score_case(row["case"]))


@pytest.mark.parametrize(
    ("table", "key", "catalogue"),
    [
        ("junit_control", "report", c.JUNIT_REPORTS),
        ("red", "stdout", c.RED_STDOUTS),
        ("exit_code", "output", c.EXIT_OUTPUTS),
        ("coverage", "report", c.COVERAGE_REPORTS),
        ("mutation", "report", c.MUTATION_REPORTS),
        ("complexity", "report", c.COMPLEXITY_REPORTS),
        ("duplication", "report", c.DUPLICATION_REPORTS),
        ("api", "report", c.API_REPORTS),
        ("min_score", "case", c.MIN_SCORE_CASES),
    ],
)
def test_every_case_of_the_catalogue_has_rows(
    table: str, key: str, catalogue: dict[str, object]
) -> None:
    """A case added to the catalogue without measuring it would test nothing."""
    assert {row[key] for row in _ROWS if row["table"] == table} == set(catalogue)


@pytest.mark.parametrize("row", _rows("timed_out", "site"))
def test_a_command_cut_by_its_timeout(row: dict[str, Any], tmp_path: Path) -> None:
    site = row["site"]
    if site == "aceptación":
        _check(row["expected"], c.timed_out_acceptance(tmp_path))
    else:
        _check(row["expected"], c.timed_out_control(site, tmp_path))


def test_every_cut_site_has_a_row() -> None:
    sites = {row["site"] for row in _ROWS if row["table"] == "timed_out"}
    assert sites == {*c.TIMED_OUT_FORMATS, "aceptación"}
