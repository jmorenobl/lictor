"""How `lictor check` shows a reader: «lee unit», its score, the mark and its control's log."""

from lictor.cli import _check_lines
from lictor.domain.controls import CommandControl, ReaderControl
from lictor.domain.reports import CoverageReport, JUnitReport, MissingReport
from lictor.domain.results import (
    CommandEntry,
    ControlEntry,
    Executed,
    Judged,
    Ran,
    Read,
    ReaderEntry,
)
from lictor.domain.verdicts import Execution, Failed, Passed

_UNIT = CommandControl("unit", "L2", "pytest {out}", "junit", mode="measure")
_READER = ReaderControl("coverage", "L2", "unit", "--cov {out}", mode="measure")
_RAN = Ran(("pytest",), Execution(1, 60, duration_ms=7), ".lictor/check/unit.log")


def _entries(unit_passed: bool, read: Read) -> list[ControlEntry]:
    verdict = Passed() if unit_passed else Failed("Fallaron 1 casos de test")
    unit = CommandEntry(_UNIT, Executed(_RAN, Judged(JUnitReport(()), verdict)))
    return [unit, ReaderEntry(_READER, read)]


def test_a_reader_reads_its_control_with_its_score() -> None:
    entries = _entries(True, Read("unit", CoverageReport(3, 4), Passed()))

    assert _check_lines("coverage", "", entries[1], entries) == [
        "✓ coverage lee unit: 75,0 % (3 de 4 líneas)"
    ]


def test_a_score_read_from_a_failed_unit_is_marked() -> None:
    entries = _entries(False, Read("unit", CoverageReport(3, 4), Passed()))

    assert _check_lines("coverage", "", entries[1], entries) == [
        "✓ coverage lee unit: 75,0 % (3 de 4 líneas) (de un unit fallido)"
    ]


def test_a_failed_reader_points_to_the_log_of_the_run_it_read() -> None:
    entries = _entries(True, Read("unit", MissingReport(), Failed("Sin informe de cobertura")))

    assert _check_lines("coverage", " (solo en check)", entries[1], entries) == [
        "✗ coverage (solo en check) lee unit: Sin informe de cobertura",
        "  salida: .lictor/check/unit.log",
    ]
