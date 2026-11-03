"""JUnit XML result reader using defusedxml."""

import xml.etree.ElementTree as ET
from collections.abc import Collection
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
        properties=_properties(tc_elem),
    )


def _properties(tc_elem: ET.Element) -> dict[str, str]:
    """Read what the runner attached to the case; a repeated name keeps its last value."""
    return {
        prop.attrib["name"]: prop.attrib.get("value", "")
        for prop in tc_elem.findall("properties/property")
        if "name" in prop.attrib
    }


class JUnitReader(ResultReader):
    """Safely parses JUnit XML test reports."""

    def read(
        self,
        output: str,
        exit_code: int,
        duration_ms: int,
        empty_exit_codes: Collection[int] = (),
    ) -> ControlResult:
        """Parse JUnit XML from string or file path."""
        raw_xml = _load_raw_xml(output)

        if not raw_xml.strip():
            return self._handle_empty(exit_code, duration_ms, empty_exit_codes)

        try:
            root = DefusedET.fromstring(raw_xml)
        except (DefusedXmlException, ET.ParseError, ValueError) as exc:
            return ControlResult(
                ok=False,
                duration_ms=duration_ms,
                error=f"No se pudo leer el informe JUnit: {exc}",
                test_cases=(),
            )

        test_cases = tuple(_parse_test_case(elem) for elem in root.iter("testcase"))
        if not test_cases:
            return self._handle_empty(exit_code, duration_ms, empty_exit_codes)

        return self._evaluate_results(test_cases, exit_code, duration_ms)

    @staticmethod
    def _handle_empty(
        exit_code: int, duration_ms: int, empty_exit_codes: Collection[int]
    ) -> ControlResult:
        if exit_code in empty_exit_codes:
            return ControlResult(ok=True, duration_ms=duration_ms, test_cases=())
        return ControlResult(
            ok=False,
            duration_ms=duration_ms,
            error=f"No hay casos de test (código de salida {exit_code})",
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
                error_msg = f"Fallaron {len(failed)} casos de test"
            else:
                error_msg = f"La suite falló con el código {exit_code}"
        return ControlResult(
            ok=ok,
            duration_ms=duration_ms,
            error=error_msg,
            test_cases=test_cases,
        )
