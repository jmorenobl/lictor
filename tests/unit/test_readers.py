"""Unit tests for ResultReader implementations: ExitCodeReader and JUnitReader."""

from pathlib import Path

from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader


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


def test_junit_reader_allow_empty_with_pytest_code_5() -> None:
    reader = JUnitReader(allow_empty=True)
    res = reader.read(output="", exit_code=5, duration_ms=5)
    assert res.ok is True
    assert res.test_cases == ()


def test_junit_reader_empty_without_allow_empty_fails() -> None:
    reader = JUnitReader(allow_empty=False)
    res = reader.read(output="", exit_code=5, duration_ms=5)
    assert res.ok is False
    assert "No test cases found" in (res.error or "")


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
