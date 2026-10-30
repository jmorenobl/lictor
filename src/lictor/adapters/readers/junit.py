"""JUnit XML result reader using defusedxml."""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from lictor.application.ports import ResultReader
from lictor.domain.model import ControlResult, TestCaseResult


def _load_raw_xml(output: str) -> str:
    cleaned = output.strip()
    if cleaned and not cleaned.startswith("<"):
        try:
            path = Path(cleaned)
            if path.is_file():
                return path.read_text(encoding="utf-8")
            if path.suffix == ".xml" or "/" in cleaned or "\\" in cleaned:
                return ""
        except OSError:
            pass
    return output


def _parse_test_case(tc_elem: ET.Element) -> TestCaseResult:
    name = tc_elem.attrib.get("name", "")
    classname = tc_elem.attrib.get("classname", "")

    failure = tc_elem.find("failure")
    error = tc_elem.find("error")
    skipped = tc_elem.find("skipped")

    status: Literal["passed", "failure", "error", "skipped"] = "passed"
    message: str | None = None
    details: str | None = None

    if failure is not None:
        status = "failure"
        message = failure.attrib.get("message")
        details = failure.text
    elif error is not None:
        status = "error"
        message = error.attrib.get("message")
        details = error.text
    elif skipped is not None:
        status = "skipped"
        message = skipped.attrib.get("message")
        details = skipped.text

    return TestCaseResult(
        name=name,
        classname=classname,
        status=status,
        message=message,
        details=details,
    )


class JUnitReader(ResultReader):
    """Safely parses JUnit XML test reports."""

    def __init__(self, allow_empty: bool = False) -> None:
        self.allow_empty = allow_empty

    def read(
        self,
        output: str,
        exit_code: int,
        duration_ms: int,
        allow_empty: bool | None = None,
    ) -> ControlResult:
        """Parse JUnit XML from string or file path."""
        effective_allow_empty = self.allow_empty if allow_empty is None else allow_empty
        raw_xml = _load_raw_xml(output)

        if not raw_xml.strip():
            return self._handle_empty(exit_code, duration_ms, effective_allow_empty)

        try:
            root = DefusedET.fromstring(raw_xml)
        except (DefusedXmlException, ET.ParseError, ValueError) as exc:
            return ControlResult(
                ok=False,
                duration_ms=duration_ms,
                error=f"Failed to parse JUnit XML: {exc}",
                test_cases=(),
            )

        test_cases = tuple(_parse_test_case(elem) for elem in root.iter("testcase"))
        if not test_cases:
            return self._handle_empty(exit_code, duration_ms, effective_allow_empty)

        return self._evaluate_results(test_cases, exit_code, duration_ms)

    @staticmethod
    def _handle_empty(exit_code: int, duration_ms: int, allow_empty: bool) -> ControlResult:
        if allow_empty and exit_code in (0, 5):
            return ControlResult(ok=True, duration_ms=duration_ms, test_cases=())
        return ControlResult(
            ok=False,
            duration_ms=duration_ms,
            error=f"No test cases found (exit code {exit_code})",
            test_cases=(),
        )

    @staticmethod
    def _evaluate_results(
        test_cases: tuple[TestCaseResult, ...],
        exit_code: int,
        duration_ms: int,
    ) -> ControlResult:
        failed = [tc for tc in test_cases if tc.status in ("failure", "error")]
        ok = len(failed) == 0 and exit_code == 0
        error_msg = None
        if not ok:
            if failed:
                error_msg = f"{len(failed)} test case(s) failed"
            else:
                error_msg = f"Suite execution failed with exit code {exit_code}"
        return ControlResult(
            ok=ok,
            duration_ms=duration_ms,
            error=error_msg,
            test_cases=test_cases,
        )
