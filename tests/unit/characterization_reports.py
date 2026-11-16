"""What reading a report and judging it does, as each site of the code does it (ADR-0009).

The tables in `characterization_reports.jsonl` were measured against master (`50af6a5`), before
ADR-0009 moved every verdict into the domain; `test_characterization_reports.py` checks them. Each
function below does what one site does:

- a control of a run or of `check`: read `{out}` with the reader of its format and judge it, or
  judge the output for `exit-code` (`read_and_judge`);
- the acceptance of a run (`VerifyAttempt._run_acceptance`): the same, with no empty exit codes,
  then `suite_crash_reason` and `describe_crash`;
- the acceptance of `check` (`CheckProject`): the JUnit verdict, with no empty exit codes;
- the red and the impostors (`CheckRed`): the JUnit reader on `{out}`, never the stdout.

A row changes only with a deliberate change of ADR-0009, in the commit that makes it: the diff of
the `.jsonl` is the list of what changed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lictor.adapters.readers.coverage import CoverageReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.application.control_reports import read_and_judge
from lictor.application.ports import ReportReaders
from lictor.application.run_files import describe_crash
from lictor.domain.model import CommandOutput, CoverageScore, MutationScore, Score
from lictor.domain.reports import ReportFormat
from lictor.domain.results import Judged
from lictor.domain.verdicts import (
    Crashed,
    Execution,
    Failed,
    Passed,
    VerdictRule,
    acceptance_check_verdict,
    enforce_min_score,
    junit_verdict,
    report_cases,
    trust,
)

READERS = ReportReaders(junit=JUnitReader(), coverage=CoverageReader(), mutation=MutationReader())

RAISES = "raises"


@dataclass(frozen=True)
class Seen:
    """What a site ends up with: the verdict, the message, the cases and the score."""

    ok: bool | str  # RAISES when the reader raised
    error: str | None
    cases: int = 0
    score: str | None = None


def _score(score: Score | None) -> str | None:
    if isinstance(score, CoverageScore):
        return f"coverage {score.percent!r}"
    if isinstance(score, MutationScore):
        return f"mutation {score.percent!r} ({score.killed} de {score.total})"
    return None


def _seen(call: Callable[[], Judged]) -> Seen:
    try:
        judged = call()
    except Exception as exc:  # a reader that raised was part of what master did
        return Seen(RAISES, type(exc).__name__)
    verdict = judged.verdict
    error = verdict.reason if isinstance(verdict, Failed) else None
    return Seen(isinstance(verdict, Passed), error, len(judged.cases), _score(judged.score))


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
    """A JUnit control of a run or of `check`: read `{out}`, then its verdict."""
    return _seen(lambda: read_and_judge(READERS, _rule("junit", empty), report, _output(code)))


@dataclass(frozen=True)
class SeenAcceptance:
    """What the acceptance of a run ends up with: the reader's view, and the crash it judged."""

    reader: Seen
    crash: str | None = None
    reason: str | None = None  # what describe_crash puts first, before the output


def acceptance_of_a_run(report: Path, code: int) -> SeenAcceptance:
    """`VerifyAttempt._run_acceptance`, with `failure_exit_codes` [1] as the profile declares."""
    junit = READERS.junit.read(report)
    reader = _seen(lambda: Judged(junit, junit_verdict(junit, _ran(code), ())))
    trusted = trust(junit, _ran(code), (1,))
    crash = trusted.reason if isinstance(trusted, Crashed) else None
    reason = describe_crash(crash, "") if crash else None
    return SeenAcceptance(reader, crash, reason)


def acceptance_of_check(report: Path, code: int) -> Seen:
    """`CheckProject`: the acceptance trusts its report as in a run, then judges its cases."""
    junit = READERS.junit.read(report)
    verdict = acceptance_check_verdict(trust(junit, _ran(code), (1,)))
    return _seen(lambda: Judged(junit, verdict))


RED_STDOUTS: dict[str, str] = {
    "vacío": "",
    "texto": "texto corriente",
    "texto con /": "ruta/con/barra",
    "un XML JUnit": PASS1.decode(),
}


@dataclass(frozen=True)
class SeenRed:
    """What the red and the impostors use of a report: its cases, and whether the suite crashed."""

    cases: int
    crash: str | None


def acceptance_of_the_red(report: Path, stdout: str, code: int) -> SeenRed:
    """`CheckRed`, red and impostors: `{out}` read by the JUnit reader; the stdout is never read."""
    del stdout  # ADR-0009, change 3: the stdout is no source of a report
    junit = READERS.junit.read(report)
    trusted = trust(junit, _ran(code), (1,))
    crash = trusted.reason if isinstance(trusted, Crashed) else None
    return SeenRed(len(report_cases(junit)), crash)


TIMEOUT_S = 600


def _rule(report_format: ReportFormat, empty: tuple[int, ...] = ()) -> VerdictRule:
    return VerdictRule(report_format, TIMEOUT_S, empty)


def _ran(code: int) -> Execution:
    return Execution(code, TIMEOUT_S)


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
    """An `exit-code` control: its verdict reads stdout and stderr joined, with no report."""
    output = _output(code, stdout, stderr)
    return _seen(lambda: read_and_judge(READERS, _rule("exit-code", empty), Path("x"), output))


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
        {"covered_lines": 0, "num_statements": 0, "num_branches": 2, "covered_branches": 1}
    ),
    "más líneas cubiertas que sentencias": _coverage({"covered_lines": 9, "num_statements": 2}),
}


def coverage_control(report: Path, code: int, empty: tuple[int, ...]) -> Seen:
    return _seen(lambda: read_and_judge(READERS, _rule("coverage", empty), report, _output(code)))


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
    return _seen(lambda: read_and_judge(READERS, _rule("mutation"), report, _output(code)))


def _coverage_score(percent: float | None) -> CoverageScore | None:
    if percent is None:
        return None
    return CoverageScore(covered_lines=round(percent * 100), num_statements=10_000, percent=percent)


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
    score = _coverage_score(percent)
    verdict = enforce_min_score(Passed() if passed else Failed("falló antes"), score, minimum)
    error = verdict.reason if isinstance(verdict, Failed) else None
    return Seen(isinstance(verdict, Passed), error, 0, _score(score))


# A command cut by its timeout, with a report it managed to leave (ADR-0009, change 10). These rows
# are new: before that change a cut command was judged as its code -9, as in the other tables.
_CUT = CommandOutput(returncode=-9, stdout="algo", stderr="", timed_out=True)
TIMED_OUT_FORMATS: tuple[ReportFormat, ...] = ("exit-code", "junit", "coverage", "mutation")
_LEFT_BEHIND: dict[ReportFormat, bytes] = {
    "junit": PASS1,
    "coverage": _coverage({"covered_lines": 1, "num_statements": 2}),
    "mutation": json.dumps({"killed": 1, "total": 2}).encode(),
}


def timed_out_control(report_format: ReportFormat, tmp: Path) -> Seen:
    """A control cut by its timeout, whatever report it left."""
    report = tmp / "r"
    if report_format in _LEFT_BEHIND:
        report.write_bytes(_LEFT_BEHIND[report_format])
    return _seen(lambda: read_and_judge(READERS, _rule(report_format), report, _CUT))


def timed_out_acceptance(tmp: Path) -> SeenRed:
    """An acceptance cut by its timeout, as the run, `check` and the red trust it."""
    report = tmp / "a.xml"
    report.write_bytes(PASS1)
    trusted = trust(READERS.junit.read(report), Execution.of(_CUT, TIMEOUT_S), (1,))
    return SeenRed(1, trusted.reason if isinstance(trusted, Crashed) else None)
