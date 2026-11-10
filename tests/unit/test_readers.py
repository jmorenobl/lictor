"""Unit tests for ResultReader implementations: ExitCodeReader and JUnitReader."""

import json
from pathlib import Path

import pytest

from lictor.adapters.readers.coverage import CoverageReader
from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.domain.model import ControlResult, CoverageScore, MutationScore


def test_exit_code_reader_success() -> None:
    reader = ExitCodeReader()
    res = reader.read(output="all good", exit_code=0, duration_ms=10)
    assert res.ok is True
    assert res.error is None
    assert res.duration_ms == 10
    assert res.test_cases == ()


def test_exit_code_reader_failure_extracts_last_40_lines() -> None:
    reader = ExitCodeReader()
    lines = [f"line {i}" for i in range(100)]
    output = "\n".join(lines)
    res = reader.read(output=output, exit_code=1, duration_ms=25)

    assert res.ok is False
    assert res.error is not None
    assert "line 99" in res.error
    assert "line 60" in res.error
    assert "line 59" not in res.error


def test_junit_reader_passed_cases() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <testsuites>
      <testsuite name="acceptance" tests="2" failures="0" errors="0">
        <testcase name="test_iva_001" classname="tests.acceptance.test_iva" />
        <testcase name="test_iva_002" classname="tests.acceptance.test_iva" />
      </testsuite>
    </testsuites>
    """
    reader = JUnitReader()
    res = reader.read(output=xml, exit_code=0, duration_ms=50)

    assert res.ok is True
    assert len(res.test_cases) == 2
    assert res.test_cases[0].name == "test_iva_001"
    assert res.test_cases[0].status == "passed"
    assert res.test_cases[1].status == "passed"


def test_junit_reader_failure_and_error_cases() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <testsuite name="unit">
      <testcase name="test_one" classname="pkg.test">
        <failure message="Assert failed">Details trace</failure>
      </testcase>
      <testcase name="test_two" classname="pkg.test">
        <error message="Crash">Stack trace</error>
      </testcase>
      <testcase name="test_three" classname="pkg.test">
        <skipped message="Not ready" />
      </testcase>
    </testsuite>
    """
    reader = JUnitReader()
    res = reader.read(output=xml, exit_code=1, duration_ms=30)

    assert res.ok is False
    assert len(res.test_cases) == 3
    assert res.test_cases[0].status == "failure"
    assert res.test_cases[0].message == "Assert failed"
    assert res.test_cases[0].details == "Details trace"

    assert res.test_cases[1].status == "error"
    assert res.test_cases[1].message == "Crash"
    assert res.test_cases[1].details == "Stack trace"

    assert res.test_cases[2].status == "skipped"


def test_junit_reader_from_file_path(tmp_path: Path) -> None:
    xml_file = tmp_path / "report.xml"
    xml_file.write_text(
        """<testsuite><testcase name="test_file" status="passed" /></testsuite>""",
        encoding="utf-8",
    )
    reader = JUnitReader()
    res = reader.read(output=str(xml_file), exit_code=0, duration_ms=15)
    assert res.ok is True
    assert len(res.test_cases) == 1
    assert res.test_cases[0].name == "test_file"


def test_junit_reader_empty_passes_with_a_declared_empty_code() -> None:
    res = JUnitReader().read(output="", exit_code=5, duration_ms=5, empty_exit_codes=[5])
    assert res.ok is True
    assert res.test_cases == ()


def test_junit_reader_empty_without_empty_codes_fails() -> None:
    res = JUnitReader().read(output="", exit_code=5, duration_ms=5)
    assert res.ok is False
    assert res.error == "No hay casos de test (código de salida 5)"


@pytest.mark.parametrize(("codes", "ok"), [([5], False), ([0, 5], True), ([], False)])
def test_junit_reader_empty_with_exit_0_needs_0_declared(codes: list[int], ok: bool) -> None:
    report = "<testsuite></testsuite>"
    res = JUnitReader().read(output=report, exit_code=0, duration_ms=5, empty_exit_codes=codes)
    assert res.ok is ok


@pytest.mark.parametrize(("code", "ok"), [(0, True), (5, True), (4, False), (1, False)])
def test_exit_code_reader_passes_a_declared_empty_code(code: int, ok: bool) -> None:
    res = ExitCodeReader().read(output="", exit_code=code, duration_ms=1, empty_exit_codes=(5,))
    assert res.ok is ok


def test_exit_code_reader_without_output_names_the_code() -> None:
    res = ExitCodeReader().read(output="", exit_code=3, duration_ms=1)
    assert res.error == "El comando falló con el código 3"


def test_junit_reader_messages_are_in_spanish() -> None:
    failed = '<testsuite><testcase name="t"><failure message="m"/></testcase></testsuite>'
    assert JUnitReader().read(failed, 1, 1).error == "Fallaron 1 casos de test"
    passed = '<testsuite><testcase name="t"/></testsuite>'
    assert JUnitReader().read(passed, 2, 1).error == "La suite falló con el código 2"
    broken = JUnitReader().read("<testsuite>", 1, 1).error or ""
    assert broken.startswith("No se pudo leer el informe JUnit: ")


def test_junit_reader_rejects_malicious_xml() -> None:
    # Entity expansion (billion laughs / quadratic blowup)
    malicious_xml = """<?xml version="1.0"?>
    <!DOCTYPE lolz [
      <!ENTITY lol "lol">
      <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
    ]>
    <testsuite name="&lol2;"></testsuite>
    """
    reader = JUnitReader()
    res = reader.read(output=malicious_xml, exit_code=0, duration_ms=10)
    assert res.ok is False
    assert res.error is not None


def test_junit_reads_the_properties_of_each_case() -> None:
    xml = (
        "<testsuite>"
        '<testcase name="a"><properties>'
        '<property name="lictor.failure" value="assertion"/>'
        '<property name="lictor.origin" value="src/x.py"/>'
        '<property value="sin nombre"/><property name="vacía"/>'
        '</properties><failure message="m"/></testcase>'
        '<testcase name="b"/>'
        "</testsuite>"
    )

    first, second = JUnitReader().read(xml, exit_code=1, duration_ms=0).test_cases

    assert first.properties == {
        "lictor.failure": "assertion",
        "lictor.origin": "src/x.py",
        "vacía": "",
    }
    assert second.properties == {}


def _mutation(tmp_path: Path, content: str | None, exit_code: int = 0) -> ControlResult:
    report = tmp_path / "score.json"
    if content is not None:
        report.write_text(content, encoding="utf-8")
    return MutationReader().read(output=str(report), exit_code=exit_code, duration_ms=3)


def test_mutation_reader_scores_the_exported_counts(tmp_path: Path) -> None:
    counts = '{"killed": 8, "survived": 1, "timeout": 1, "skipped": 2, "total": 12, "x": "y"}'

    res = _mutation(tmp_path, counts)

    assert (res.ok, res.error, res.duration_ms) == (True, None, 3)
    assert res.score == MutationScore(
        killed=8, survived=1, timeout=1, skipped=2, total=12, percent=80.0
    )


def test_mutation_reader_keeps_the_score_of_a_failed_pass(tmp_path: Path) -> None:
    res = _mutation(tmp_path, '{"killed": 1, "total": 2}', exit_code=4)

    assert res.ok is False
    assert res.error == "La pasada de mutación falló con el código 4"
    assert res.score is not None
    assert res.score.percent == 50.0


def test_mutation_reader_without_a_report_fails(tmp_path: Path) -> None:
    res = _mutation(tmp_path, None)
    assert (res.ok, res.error, res.score) == (False, "Sin informe de mutación", None)
    empty = MutationReader().read(output="", exit_code=0, duration_ms=1)
    assert (empty.ok, empty.error) == (False, "Sin informe de mutación")


def test_mutation_reader_with_no_mutants_fails(tmp_path: Path) -> None:
    res = _mutation(tmp_path, '{"total": 0}')
    assert (res.ok, res.error) == (False, "El informe de mutación no tiene mutantes")


@pytest.mark.parametrize(
    ("content", "why"),
    [
        ("no", "no es JSON"),
        ("[1]", "no es un objeto JSON"),
        ('{"killed": "3"}', "'killed' no es un número de mutantes: '3'"),
        ('{"total": -1}', "'total' no es un número de mutantes: -1"),
        ('{"survived": true}', "'survived' no es un número de mutantes: True"),
    ],
)
def test_mutation_reader_refuses_a_report_it_cannot_trust(
    tmp_path: Path, content: str, why: str
) -> None:
    res = _mutation(tmp_path, content)

    assert res.ok is False
    assert res.error is not None
    assert res.error.startswith(f"El informe de mutación no es válido: {why}")


def _coverage(
    tmp_path: Path, content: str | None, exit_code: int = 0, empty: tuple[int, ...] = ()
) -> ControlResult:
    report = tmp_path / "coverage.json"
    if content is not None:
        report.write_text(content, encoding="utf-8")
    return CoverageReader().read(
        output=str(report), exit_code=exit_code, duration_ms=3, empty_exit_codes=empty
    )


_COVERAGE_JSON = json.dumps(
    {
        "meta": {"format": 3},
        "files": {},
        "totals": {
            "covered_lines": 9,
            "num_statements": 10,
            "covered_branches": 1,
            "num_branches": 2,
            "percent_covered": 83.33,
        },
    }
)


def test_coverage_reader_scores_the_totals_of_coverage_py(tmp_path: Path) -> None:
    res = _coverage(tmp_path, _COVERAGE_JSON)

    assert (res.ok, res.error, res.duration_ms) == (True, None, 3)
    assert res.score == CoverageScore(
        covered_lines=9,
        num_statements=10,
        covered_branches=1,
        num_branches=2,
        percent=100 * 10 / 12,
    )


def test_coverage_reader_keeps_the_score_when_the_tests_fail(tmp_path: Path) -> None:
    res = _coverage(tmp_path, _COVERAGE_JSON, exit_code=1)

    assert res.ok is False
    assert res.error == "Los tests de la cobertura fallaron con el código 1"
    assert res.score is not None


def test_coverage_reader_without_tests_to_run_passes_with_no_score(tmp_path: Path) -> None:
    res = _coverage(tmp_path, None, exit_code=5, empty=(5,))

    assert (res.ok, res.error, res.score) == (True, None, None)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "Sin informe de cobertura"),
        ("{", "no es JSON"),
        ("[]", "no tiene 'totals'"),
        ('{"totals": {"covered_lines": "9", "num_statements": 10}}', "'covered_lines'"),
        ('{"totals": {"covered_lines": -1, "num_statements": 10}}', "'covered_lines'"),
        ('{"totals": {"covered_lines": true, "num_statements": 10}}', "'covered_lines'"),
    ],
)
def test_coverage_reader_without_a_valid_report_fails(
    tmp_path: Path, content: str | None, reason: str
) -> None:
    res = _coverage(tmp_path, content)

    assert res.ok is False
    assert res.error is not None
    assert reason in res.error
    assert res.score is None
