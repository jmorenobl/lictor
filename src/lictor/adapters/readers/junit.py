"""JUnit XML result reader using defusedxml."""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

import defusedxml.ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from lictor.adapters.readers.files import report_bytes
from lictor.application.ports import ReportReader
from lictor.domain.model import TestCaseResult
from lictor.domain.reports import InvalidReport, JUnitReport, ReportOf


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
        properties=_properties(tc_elem),
    )


def _properties(tc_elem: ET.Element) -> dict[str, str]:
    """Read what the runner attached to the case; a repeated name keeps its last value."""
    return {
        prop.attrib["name"]: prop.attrib.get("value", "")
        for prop in tc_elem.findall("properties/property")
        if "name" in prop.attrib
    }


class JUnitReader(ReportReader[JUnitReport]):
    """Safely parses JUnit XML reports from their bytes, as XML says to decode them.

    A BOM or a declared encoding is honoured; bytes that don't decode as declared, an entity or
    anything that isn't XML make an invalid report. Whether its cases are enough is the domain's.
    """

    def read(self, path: Path) -> ReportOf[JUnitReport]:
        raw = report_bytes(path)
        if not isinstance(raw, bytes):
            return raw
        if not raw.strip():
            return InvalidReport("está vacío")
        try:
            root = DefusedET.fromstring(raw)
        except (DefusedXmlException, ET.ParseError, ValueError) as exc:
            return InvalidReport(str(exc) or type(exc).__name__)
        return JUnitReport(tuple(_parse_test_case(elem) for elem in root.iter("testcase")))
