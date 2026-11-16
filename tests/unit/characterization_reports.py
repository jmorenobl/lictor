"""What reading a report and judging it does today, as each site of the code does it (ADR-0009).

The tables in `test_characterization_reports.py` pin, row by row, the behaviour of the readers and
of the acceptance before ADR-0009 moves every verdict into the domain. Each function below calls
the readers exactly as one site does:

- a control of a run or of `check` (`reader_input`: the path of `{out}`, or the output for
  `exit-code`);
- the acceptance of a run (`VerifyAttempt._run_acceptance`: the path, no `empty_exit_codes`, then
  `suite_crash_reason` and `describe_crash`);
- the acceptance of `check` (`CheckProject`: only the reader's `ok`);
- the red and the impostors (`CheckRed`: the file read with `errors="replace"`, or the stdout when
  there is no file).

A row that ADR-0009 changes on purpose keeps today's value here: changing it is the diff that
implements that change.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lictor.adapters.readers.coverage import CoverageReader
from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.application.run_files import describe_crash, format_output, reader_input
from lictor.domain.model import CommandOutput, ControlResult, CoverageScore, MutationScore
from lictor.domain.rules import enforce_min_score, suite_crash_reason

RAISES = "raises"


@dataclass(frozen=True)
class Seen:
    """What a site ends up with: the verdict, the message, the cases and the score."""

    ok: bool | str  # RAISES when the reader raised
    error: str | None
    cases: int = 0
    score: str | None = None


def _score(result: ControlResult) -> str | None:
    score = result.score
    if isinstance(score, CoverageScore):
        return f"coverage {score.percent!r}"
    if isinstance(score, MutationScore):
        return f"mutation {score.percent!r} ({score.killed} de {score.total})"
    return None


def _seen(call: Callable[[], ControlResult]) -> Seen:
    try:
        result = call()
    except Exception as exc:  # a reader that raises is part of what today does
        return Seen(RAISES, type(exc).__name__)
    return Seen(result.ok, result.error, len(result.test_cases), _score(result))


PASS1 = b'<testsuite><testcase name="a" classname="c"/></testsuite>'
_FAIL1 = (
    b'<testsuite><testcase name="a" classname="c"><failure message="m">x</failure>'
    b"</testcase></testsuite>"
)
_ERR1 = (
    b'<testsuite><testcase name="a" classname="c"><error message="m">x</error>'
    b"</testcase></testsuite>"
)
_SKIP1 = b'<testsuite><testcase name="a" classname="c"><skipped/></testcase></testsuite>'
_FAIL_AND_SKIP = (
    b'<testsuite><testcase name="a" classname="c"><skipped/><failure message="m"/>'
    b"</testcase></testsuite>"
)
_LATIN1 = (
    b'<?xml version="1.0" encoding="iso-8859-1"?>'
    b'<testsuite><testcase name="caf\xe9" classname="c"/></testsuite>'
)
_ENTITY = (
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]>'
    b'<testsuite><testcase name="&e;"/></testsuite>'
)

# How a report can be left at `{out}`: bytes, or one of the markers below.
ABSENT = "absent"
DIRECTORY = "directory"
BROKEN_LINK = "broken-link"
OTHER_REPORT_PATH = "other-report-path"  # the content is the path of another JUnit that exists

JUNIT_REPORTS: dict[str, bytes | str] = {
    "ausente": ABSENT,
    "directorio": DIRECTORY,
    "enlace roto": BROKEN_LINK,
    "vacío (0 bytes)": b"",
    "solo espacios": b"   \n",
    "no es XML": b"esto no es xml",
    "texto antes del XML": b"aviso: x\n" + PASS1,
    "saltos de línea antes del XML": b"\n\n" + PASS1,
    "bytes no UTF-8": b'<testsuite><testcase name="\xff\xfe" classname="c"/></testsuite>',
    "UTF-8 con BOM": b"\xef\xbb\xbf" + PASS1,
    "declara iso-8859-1": _LATIN1,
    "UTF-16 con declaración": ('<?xml version="1.0" encoding="utf-16"?>' + PASS1.decode()).encode(
        "utf-16"
    ),
    "XML sin testcase": b"<testsuites><testsuite/></testsuites>",
    "raíz testcase": b'<testcase name="a"/>',
    "1 caso que pasa": PASS1,
    "1 caso failure": _FAIL1,
    "1 caso error": _ERR1,
    "1 caso skipped": _SKIP1,
    "1 caso skipped y failure": _FAIL_AND_SKIP,
    "con entidad (DTD)": _ENTITY,
    "basura tras el XML": PASS1 + b"zzz",
    "el contenido es la ruta de otro JUnit": OTHER_REPORT_PATH,
}


def leave_report(where: Path, how: bytes | str) -> Path:
    """Leave at `where` what a command could have left at its `{out}`, and return `where`."""
    if how == ABSENT:
        return where
    if how == DIRECTORY:
        where.mkdir()
        return where
    if how == BROKEN_LINK:
        where.symlink_to(where.parent / "no-existe")
        return where
    if how == OTHER_REPORT_PATH:
        other = where.parent / "otro.xml"
        other.write_bytes(PASS1)
        where.write_text(str(other), encoding="utf-8")
        return where
    assert isinstance(how, bytes)
    where.write_bytes(how)
    return where


def junit_as_control(report: Path, code: int, empty: tuple[int, ...]) -> Seen:
    """A JUnit control of a run or of `check`: the reader gets the path of `{out}`."""
    given = reader_input("junit", "cmd --junitxml {out}", report, _output(code))
    return _seen(lambda: JUnitReader().read(given, code, 0, empty))


@dataclass(frozen=True)
class SeenAcceptance:
    """What the acceptance of a run ends up with: the reader's view, and the crash it judged."""

    reader: Seen
    crash: str | None = None
    reason: str | None = None  # what describe_crash puts first, before the output


def acceptance_of_a_run(report: Path, code: int) -> SeenAcceptance:
    """`VerifyAttempt._run_acceptance`, with `failure_exit_codes` [1] as the profile declares."""
    try:
        result = JUnitReader().read(str(report), code, 0)
    except Exception as exc:
        return SeenAcceptance(Seen(RAISES, type(exc).__name__))
    crash = suite_crash_reason(code, (1,), result.test_cases)
    reason = describe_crash(crash, result, "").strip() if crash else None
    return SeenAcceptance(Seen(result.ok, result.error, len(result.test_cases)), crash, reason)


def acceptance_of_check(report: Path, code: int) -> Seen:
    """`CheckProject`: the acceptance is judged by the reader's `ok`, with no empty exit codes."""
    return _seen(lambda: JUnitReader().read(str(report), code, 0, ()))


RED_STDOUTS: dict[str, str] = {
    "vacío": "",
    "texto": "texto corriente",
    "texto con /": "ruta/con/barra",
    "un XML JUnit": PASS1.decode(),
}


def acceptance_of_the_red(report: Path, stdout: str, code: int) -> SeenAcceptance:
    """`CheckRed`, red and impostors: the file read with replace, or the stdout if there is none."""
    content = report.read_text(encoding="utf-8", errors="replace") if report.is_file() else ""
    try:
        result = JUnitReader().read(content or stdout, code, 0)
    except Exception as exc:
        return SeenAcceptance(Seen(RAISES, type(exc).__name__))
    crash = suite_crash_reason(code, (1,), result.test_cases)
    return SeenAcceptance(Seen(result.ok, result.error, len(result.test_cases)), crash)


def _output(code: int, stdout: str = "", stderr: str = "") -> CommandOutput:
    return CommandOutput(returncode=code, stdout=stdout, stderr=stderr, duration_ms=0)


EXIT_OUTPUTS: dict[str, tuple[str, str]] = {
    "sin salida": ("", ""),
    "stdout en blanco": ("\n\n  \n", ""),
    "stderr en blanco": ("", "  \n\n"),
    "una línea en stdout": ("fallo X", ""),
    "una línea en stderr": ("", "fallo Y"),
    "stdout y stderr": ("fallo X", "fallo Y"),
    "60 líneas": ("\n".join(f"línea {i}" for i in range(60)), ""),
    "texto y 60 líneas en blanco": ("error real" + "\n" * 60, ""),
}


def exit_code_control(stdout: str, stderr: str, code: int, empty: tuple[int, ...]) -> Seen:
    """An `exit-code` control: the reader gets stdout and stderr joined, as `reader_input` does."""
    output = _output(code, stdout, stderr)
    given = reader_input("exit-code", "cmd", Path("no-se-usa"), output)
    assert given == format_output(output)
    return _seen(lambda: ExitCodeReader().read(given, code, 0, empty))


def _coverage(totals: dict[str, object]) -> bytes:
    return json.dumps({"totals": totals}).encode()


COVERAGE_REPORTS: dict[str, bytes | str] = {
    "ausente": ABSENT,
    "directorio": DIRECTORY,
    "enlace roto": BROKEN_LINK,
    "vacío": b"",
    "no es JSON": b"{x",
    "JSON sin totals": b"{}",
    "totals vacío": b'{"totals": {}}',
    "totals con valor no numérico": _coverage({"covered_lines": "a", "num_statements": 1}),
    "totals con un float": _coverage({"covered_lines": 1.0, "num_statements": 2}),
    "totals con NaN": b'{"totals": {"covered_lines": NaN, "num_statements": 2}}',
    "UTF-8 con BOM": b"\xef\xbb\xbf" + _coverage({"covered_lines": 1, "num_statements": 2}),
    "UTF-16": json.dumps({"totals": {"covered_lines": 1, "num_statements": 2}}).encode("utf-16"),
    "bytes no UTF-8": b'{"totals": {"covered_lines": 1, "num_statements": 2}, "x": "\xff"}',
    "3 de 4 líneas y 1 de 2 ramas": _coverage(
        {"covered_lines": 3, "num_statements": 4, "covered_branches": 1, "num_branches": 2}
    ),
    "0 de 2 líneas": _coverage({"covered_lines": 0, "num_statements": 2}),
    "0 sentencias y 0 ramas": _coverage({"covered_lines": 0, "num_statements": 0}),
    "0 sentencias y 1 de 2 ramas": _coverage(
        {"num_statements": 0, "num_branches": 2, "covered_branches": 1}
    ),
    "más líneas cubiertas que sentencias": _coverage({"covered_lines": 9, "num_statements": 2}),
}


def coverage_control(report: Path, code: int, empty: tuple[int, ...]) -> Seen:
    given = reader_input("coverage", "cmd --cov-report=json:{out}", report, _output(code))
    return _seen(lambda: CoverageReader().read(given, code, 0, empty))


MUTATION_REPORTS: dict[str, bytes | str] = {
    "ausente": ABSENT,
    "directorio": DIRECTORY,
    "vacío": b"",
    "no es JSON": b"{x",
    "lista JSON": b"[]",
    "objeto vacío": b"{}",
    "valor negativo": json.dumps({"killed": -1}).encode(),
    "valor float": json.dumps({"killed": 1.0, "total": 1}).encode(),
    "UTF-8 con BOM": b'\xef\xbb\xbf{"killed": 1, "total": 1}',
    "UTF-16": '{"killed": 1, "total": 1}'.encode("utf-16"),
    "bytes no UTF-8": b'{"killed": 1, "total": 1, "x": "\xff"}',
    "9 de 10": json.dumps({"killed": 9, "survived": 1, "total": 10}).encode(),
    "sin mutantes": json.dumps({"total": 0}).encode(),
    "todos omitidos": json.dumps({"total": 5, "skipped": 5}).encode(),
    "más muertos que el total": json.dumps({"killed": 9, "total": 3}).encode(),
    "más omitidos que el total": json.dumps({"total": 1, "skipped": 4}).encode(),
}


def mutation_control(report: Path, code: int) -> Seen:
    given = reader_input("mutation", "cmd {out}", report, _output(code))
    return _seen(lambda: MutationReader().read(given, code, 0, ()))


def _coverage_result(ok: bool, percent: float | None) -> ControlResult:
    if percent is None:
        return ControlResult(ok=ok, error=None if ok else "falló antes")
    score = CoverageScore(
        covered_lines=round(percent * 100), num_statements=10_000, percent=percent
    )
    return ControlResult(ok=ok, error=None if ok else "falló antes", score=score)


# What `enforce_min_score` gets, and the minimum: (passed, percent or None, minimum).
MIN_SCORE_CASES: dict[str, tuple[bool, float | None, float | None]] = {
    "pasó sin puntuación, sin mínimo": (True, None, None),
    "pasó sin puntuación, mínimo 90": (True, None, 90),
    "pasó con 50, mínimo 90": (True, 50.0, 90),
    "pasó con 90, mínimo 90": (True, 90.0, 90),
    "pasó con 89,96, mínimo 90": (True, 89.96, 90),
    "pasó con 95, mínimo 90": (True, 95.0, 90),
    "pasó con 0, mínimo 0": (True, 0.0, 0),
    "falló con 50, mínimo 90": (False, 50.0, 90),
    "falló sin puntuación, mínimo 90": (False, None, 90),
}


def min_score_case(label: str) -> Seen:
    passed, percent, minimum = MIN_SCORE_CASES[label]
    return _seen(lambda: enforce_min_score(_coverage_result(passed, percent), minimum))
