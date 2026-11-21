"""The report readers read facts and decide nothing (ADR-0009); verdicts are in test_verdicts."""

import json
from pathlib import Path

import pytest

from lictor.adapters.readers.complexity import ComplexityReader
from lictor.adapters.readers.coverage import CoverageReader
from lictor.adapters.readers.duplication import DuplicationReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.domain.model import CoverageScore, MutationScore
from lictor.domain.reports import (
    CoverageReport,
    InvalidReport,
    JUnitReport,
    MissingReport,
    MutationReport,
)


def _write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _junit(tmp_path: Path, xml: str | bytes) -> JUnitReport:
    report = JUnitReader().read(_write(tmp_path, "report.xml", xml))
    assert isinstance(report, JUnitReport)
    return report


def test_junit_reads_every_case_with_its_status(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <testsuite name="unit">
      <testcase name="test_one" classname="pkg.test">
        <failure message="Assert failed">Details trace</failure>
      </testcase>
      <testcase name="test_two" classname="pkg.test">
        <error message="Crash">Stack trace</error>
      </testcase>
      <testcase name="test_three" classname="pkg.test"><skipped message="Not ready" /></testcase>
      <testcase name="test_four" classname="pkg.test" />
    </testsuite>
    """
    one, two, three, four = _junit(tmp_path, xml).cases

    assert (one.name, one.classname, one.status) == ("test_one", "pkg.test", "failure")
    assert (one.message, one.details) == ("Assert failed", "Details trace")
    assert (two.status, two.message, two.details) == ("error", "Crash", "Stack trace")
    assert (three.status, three.message) == ("skipped", "Not ready")
    assert (four.status, four.message, four.details) == ("passed", None, None)


def test_junit_reads_the_cases_of_every_suite(tmp_path: Path) -> None:
    xml = (
        "<testsuites><testsuite><testcase name='a'/></testsuite>"
        "<testsuite><testcase name='b'/></testsuite></testsuites>"
    )
    assert [case.name for case in _junit(tmp_path, xml).cases] == ["a", "b"]


def test_junit_with_no_case_is_a_run_with_no_tests(tmp_path: Path) -> None:
    """What pytest writes with 0 tests: a report, not a missing one."""
    assert _junit(tmp_path, "<testsuites><testsuite tests='0'/></testsuites>") == JUnitReport()


def test_junit_reads_the_properties_of_each_case(tmp_path: Path) -> None:
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

    first, second = _junit(tmp_path, xml).cases

    assert first.properties == {
        "lictor.failure": "assertion",
        "lictor.origin": "src/x.py",
        "vacía": "",
    }
    assert second.properties == {}


_UTF16 = "<?xml version='1.0' encoding='utf-16'?><testsuite><testcase name='café'/></testsuite>"


@pytest.mark.parametrize(
    "raw",
    [
        b"\xef\xbb\xbf<testsuite><testcase name='caf\xc3\xa9'/></testsuite>",
        b"<?xml version='1.0' encoding='iso-8859-1'?><testsuite><testcase name='caf\xe9'/>"
        b"</testsuite>",
        _UTF16.encode("utf-16"),
    ],
    ids=["BOM", "iso-8859-1 declarado", "UTF-16"],
)
def test_junit_decodes_as_xml_says(tmp_path: Path, raw: bytes) -> None:
    assert [case.name for case in _junit(tmp_path, raw).cases] == ["café"]


def test_junit_reads_only_its_path_never_a_path_written_in_it(tmp_path: Path) -> None:
    other = _write(tmp_path, "other.xml", "<testsuite><testcase name='a'/></testsuite>")
    report = JUnitReader().read(_write(tmp_path, "report.xml", str(other)))
    assert isinstance(report, InvalidReport)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"", "está vacío"),
        (b"  \n", "está vacío"),
        (b"<testsuite>", "no element found"),
        (b"<testsuite><testcase name='\xff'/></testsuite>", "not well-formed"),
        (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><testsuite name="&e;"/>',
            "EntitiesForbidden",
        ),
    ],
    ids=["vacío", "espacios", "sin cerrar", "no UTF-8", "entidad"],
)
def test_junit_that_cannot_be_read_is_invalid(tmp_path: Path, raw: bytes, reason: str) -> None:
    report = JUnitReader().read(_write(tmp_path, "report.xml", raw))
    assert isinstance(report, InvalidReport)
    assert reason in report.reason


_READERS = [
    JUnitReader(),
    CoverageReader(),
    MutationReader(),
    ComplexityReader(),
    DuplicationReader(),
]
type _Reader = JUnitReader | CoverageReader | MutationReader | ComplexityReader | DuplicationReader


@pytest.mark.parametrize("reader", _READERS)
def test_nothing_at_the_path_is_a_missing_report(tmp_path: Path, reader: _Reader) -> None:
    assert reader.read(tmp_path / "nada") == MissingReport()


@pytest.mark.parametrize("reader", _READERS)
def test_what_is_there_and_isnt_a_file_is_invalid(tmp_path: Path, reader: _Reader) -> None:
    directory = tmp_path / "dir"
    directory.mkdir()
    broken = tmp_path / "broken"
    broken.symlink_to(tmp_path / "no-existe")

    assert reader.read(directory) == InvalidReport("es un directorio")
    assert reader.read(broken) == InvalidReport("es un enlace que no lleva a ningún fichero")


def test_a_report_that_cannot_be_opened_is_invalid(tmp_path: Path) -> None:
    report = _write(tmp_path, "report.xml", "<testsuite/>")
    report.chmod(0)
    try:
        result = JUnitReader().read(report)
    finally:
        report.chmod(0o600)
    if result == JUnitReport():
        pytest.skip("quien ejecuta puede leer un fichero sin permisos (root)")
    assert isinstance(result, InvalidReport)
    assert result.reason.startswith("no se puede abrir: ")


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


def test_coverage_reads_the_totals_of_coverage_py(tmp_path: Path) -> None:
    report = CoverageReader().read(_write(tmp_path, "coverage.json", _COVERAGE_JSON))

    assert report == CoverageReport(9, 10, 1, 2)
    assert isinstance(report, CoverageReport)
    assert report.score == CoverageScore(
        covered_lines=9,
        num_statements=10,
        covered_branches=1,
        num_branches=2,
        percent=100 * 10 / 12,
    )


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"{", "no es JSON"),
        (b"\xef\xbb\xbf{}", "no es JSON: Unexpected UTF-8 BOM"),
        ('{"totals": {}}'.encode("utf-16"), "no es UTF-8"),
        (b'{"totals": {"covered_lines": 1, "num_statements": 2}, "x": "\xff"}', "no es UTF-8"),
    ],
    ids=["sin cerrar", "BOM", "UTF-16", "no UTF-8"],
)
def test_coverage_that_cannot_be_read_is_invalid(tmp_path: Path, raw: bytes, reason: str) -> None:
    report = CoverageReader().read(_write(tmp_path, "coverage.json", raw))
    assert isinstance(report, InvalidReport)
    assert report.reason.startswith(reason)


def test_mutation_reads_the_exported_counts(tmp_path: Path) -> None:
    counts = '{"killed": 8, "survived": 1, "timeout": 1, "skipped": 2, "total": 12, "x": "y"}'

    report = MutationReader().read(_write(tmp_path, "score.json", counts))

    assert isinstance(report, MutationReport)
    assert report.score == MutationScore(
        killed=8, survived=1, timeout=1, skipped=2, total=12, percent=80.0
    )


@pytest.mark.parametrize(
    ("raw", "reason"),
    [(b"no", "no es JSON"), (b'{"total": 1, "x": "\xff"}', "no es UTF-8")],
    ids=["no JSON", "no UTF-8"],
)
def test_mutation_that_cannot_be_read_is_invalid(tmp_path: Path, raw: bytes, reason: str) -> None:
    report = MutationReader().read(_write(tmp_path, "score.json", raw))
    assert isinstance(report, InvalidReport)
    assert report.reason.startswith(reason)
