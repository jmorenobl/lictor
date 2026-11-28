"""Results as variants, what a verification does with each, and the journal's codec (ADR-0009)."""

import json
from dataclasses import replace

import pytest

from lictor.domain.controls import CommandControl, ControlMode, ReaderControl
from lictor.domain.model import TestCaseResult
from lictor.domain.reports import (
    CoverageReport,
    InvalidReport,
    JUnitReport,
    MissingReport,
    Report,
)
from lictor.domain.results import (
    OLDER_EVENTS,
    VERIFY_FINISHED,
    AcceptanceRun,
    Block,
    CommandEntry,
    CommandOutcome,
    ControlEntry,
    Executed,
    Judged,
    NotExecuted,
    Outcome,
    Proceed,
    Ran,
    Read,
    ReaderEntry,
    ReaderOutcome,
    ReadFact,
    ReadSource,
    RecordedAcceptance,
    RecordedExecuted,
    RecordedJudged,
    RecordedNotExecuted,
    RecordedRead,
    RecordedRun,
    RecordedSkipped,
    RecordedUnjudged,
    Skipped,
    StateTampered,
    Unjudged,
    VerifyResult,
    acceptance_cases,
    acceptance_from_record,
    acceptance_record,
    consequence,
    control_from_record,
    control_record,
    failure_of,
    from_failed_run,
    nothing_to_measure,
    passed,
    reader_outcome,
    recorded_controls,
    recorded_duration,
    recorded_from_failed_run,
    verification_duration,
)
from lictor.domain.verdicts import NOTHING_MEASURED, Crashed, Execution, Failed, Passed, Reported

_FAILURE = TestCaseResult(name="f", status="failure")
_PASS = TestCaseResult(name="p")
_RAN = Ran(("cmd", "{out}"), Execution(1, 60, duration_ms=30), "r/control_x.log")


def _entry(outcome: CommandOutcome, mode: ControlMode = "block") -> ControlEntry:
    return CommandEntry(CommandControl("x", "L1", "cmd", "junit", mode=mode), outcome)


def _judged(report: Report | None, verdict: Passed | Failed) -> Executed:
    return Executed(_RAN, Judged(report, verdict))


_TAMPERED = Executed(_RAN, Unjudged(("seal.json",)))
_FAILED = _judged(JUnitReport((_FAILURE,)), Failed("Fallaron 1 casos de test"))


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (Skipped("no toca"), True),
        (NotExecuted("borrado"), False),
        (_TAMPERED, False),
        (_judged(None, Passed()), True),
        (_FAILED, False),
    ],
)
def test_only_a_skip_or_a_judged_pass_passes(outcome: Outcome, expected: bool) -> None:
    assert passed(outcome) is expected


def test_a_judged_result_gives_the_cases_and_score_of_its_report() -> None:
    coverage = CoverageReport(1, 2)
    assert Judged(JUnitReport((_PASS,)), Passed()).cases == (_PASS,)
    assert Judged(coverage, Passed()).score == coverage.score
    assert Judged(None, Passed()).cases == ()
    assert Judged(None, Passed()).score is None


def test_a_failure_carries_the_reason_and_the_cases() -> None:
    assert failure_of(_FAILED, "salida") == Block("Fallaron 1 casos de test", (_FAILURE,))


@pytest.mark.parametrize(
    "report",
    [MissingReport(), InvalidReport("roto"), JUnitReport()],
    ids=["missing", "invalid", "sin casos"],
)
def test_only_the_output_explains_a_failure_with_nothing_to_read(report: Report) -> None:
    block = failure_of(_judged(report, Failed("motivo")), "la salida")
    assert block == Block("motivo\nla salida")


def test_with_no_output_the_reason_stays_alone() -> None:
    assert failure_of(_judged(MissingReport(), Failed("motivo")), "") == Block("motivo")


def test_an_exit_code_failure_is_its_own_output() -> None:
    assert failure_of(_judged(None, Failed("fin de la salida")), "toda la salida") == Block(
        "fin de la salida"
    )


@pytest.mark.parametrize(
    "outcome", [Skipped("x"), _judged(None, Passed()), _TAMPERED], ids=["skip", "pass", "unjudged"]
)
def test_there_is_no_failure_to_read(outcome: Outcome) -> None:
    assert failure_of(outcome) is None


def test_a_control_that_could_not_run_fails_with_its_reason() -> None:
    assert failure_of(NotExecuted("borró tests/unit")) == Block("borró tests/unit")


@pytest.mark.parametrize("mode", ["block", "measure"])
def test_a_control_that_tampered_ends_the_verification_whatever_its_mode(mode: ControlMode) -> None:
    assert consequence(_entry(_TAMPERED, mode)) == StateTampered(("seal.json",))


@pytest.mark.parametrize(
    ("outcome", "mode", "expected"),
    [
        (Skipped("x"), "block", Proceed()),
        (_judged(None, Passed()), "block", Proceed()),
        (_FAILED, "measure", Proceed()),
        (NotExecuted("borrado"), "measure", Proceed()),
        (_FAILED, "block", Block("Fallaron 1 casos de test", (_FAILURE,))),
        (NotExecuted("borrado"), "block", Block("borrado")),
    ],
)
def test_what_a_verification_does_with_a_result(
    outcome: CommandOutcome, mode: ControlMode, expected: object
) -> None:
    assert consequence(_entry(outcome, mode)) == expected


def test_a_blocking_failure_with_nothing_to_read_takes_the_output() -> None:
    entry = _entry(_judged(MissingReport(), Failed("sin informe")))
    assert consequence(entry, "traza") == Block("sin informe\ntraza")


def test_the_cases_of_an_acceptance_are_those_it_could_trust() -> None:
    report = JUnitReport((_PASS,))
    assert acceptance_cases(AcceptanceRun(_RAN, Reported(report))) == (_PASS,)
    assert acceptance_cases(AcceptanceRun(_RAN, Crashed("caída"))) == ()
    assert acceptance_cases(AcceptanceRun(_RAN, Unjudged(("x",)))) == ()


def test_a_verification_lasts_what_its_commands_ran() -> None:
    entries = (_entry(_judged(None, Passed())), _entry(Skipped("x")), _entry(NotExecuted("y")))
    acceptance = AcceptanceRun(
        Ran(("s",), Execution(0, 60, duration_ms=12), "r/a.log"), Crashed("c")
    )

    assert verification_duration(entries, acceptance) == 30 + 12
    assert verification_duration(entries, None) == 30
    assert verification_duration((), None) == 0


def test_a_result_finds_the_outcome_of_each_control_it_reached() -> None:
    result = VerifyResult(ok=True, controls=(_entry(Skipped("no toca")),))
    assert result.outcome("x") == Skipped("no toca")
    assert result.outcome("otro") is None


# The journal's codec.

_RECORDED_RUN = RecordedRun(1, 30, False, "r/control_x.log")


@pytest.mark.parametrize(
    ("outcome", "mode", "recorded"),
    [
        (Skipped("no toca"), "block", RecordedSkipped("block", "no toca")),
        (NotExecuted("borrado"), "measure", RecordedNotExecuted("measure", "borrado")),
        (
            _TAMPERED,
            "measure",
            RecordedExecuted("measure", _RECORDED_RUN, RecordedUnjudged(("seal.json",))),
        ),
        (
            _FAILED,
            "block",
            RecordedExecuted(
                "block",
                _RECORDED_RUN,
                RecordedJudged(False, "Fallaron 1 casos de test", None, {"failure": 1}),
            ),
        ),
        (
            _judged(CoverageReport(1, 2), Passed()),
            "block",
            RecordedExecuted(
                "block", _RECORDED_RUN, RecordedJudged(True, None, CoverageReport(1, 2).score, {})
            ),
        ),
    ],
    ids=["skipped", "not_executed", "unjudged", "failed", "scored"],
)
def test_a_control_record_reads_back_as_what_it_kept(
    outcome: CommandOutcome, mode: ControlMode, recorded: object
) -> None:
    record = control_record(_entry(outcome, mode))

    assert control_from_record(json.loads(json.dumps(record))) == recorded


def test_a_record_names_what_happened_to_the_control() -> None:
    assert control_record(_entry(Skipped("x")))["outcome"] == "skipped"
    assert control_record(_entry(NotExecuted("x")))["outcome"] == "not_executed"
    assert control_record(_entry(_TAMPERED))["judgement"] == "unjudged"
    assert control_record(_entry(_FAILED))["judgement"] == "judged"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"outcome": "skipped", "reason": "x"},
        {"outcome": "skipped", "mode": "otro", "reason": "x"},
        {"outcome": "skipped", "mode": "block"},
        {"outcome": "not_executed", "mode": "block", "reason": 3},
        {"outcome": "otra", "mode": "block"},
        {"ok": True, "mode": "block", "exit_code": 0},
    ],
    ids=["none", "lista", "sin modo", "modo raro", "sin motivo", "motivo raro", "otra", "v1"],
)
def test_a_record_that_doesnt_follow_the_schema_isnt_read(raw: object) -> None:
    assert control_from_record(raw) is None


def _executed_record(**changes: object) -> dict[str, object]:
    record = control_record(_entry(_FAILED))
    return {**record, **changes}


@pytest.mark.parametrize(
    "changes",
    [
        {"exit_code": "1"},
        {"exit_code": True},
        {"duration_ms": None},
        {"output_path": 3},
        {"timed_out": "no"},
        {"judgement": "otro"},
        {"passed": "sí"},
        {"cases": {"failure": "1"}},
        {"cases": []},
        {"score": {"x": 1}},
        {"judgement": "unjudged", "changes": "seal.json"},
        {"judgement": "unjudged", "changes": [1]},
    ],
)
def test_an_executed_record_with_a_field_out_of_its_shape_isnt_read(
    changes: dict[str, object],
) -> None:
    assert control_from_record(_executed_record(**changes)) is None


@pytest.mark.parametrize(
    ("judgement", "recorded"),
    [
        (
            Unjudged(("seal.json",)),
            RecordedAcceptance(_RECORDED_RUN, "unjudged", changes=("seal.json",)),
        ),
        (Crashed("se cayó"), RecordedAcceptance(_RECORDED_RUN, "crashed", reason="se cayó")),
        (
            Reported(JUnitReport((_PASS, _FAILURE, _FAILURE))),
            RecordedAcceptance(_RECORDED_RUN, "reported", cases={"passed": 1, "failure": 2}),
        ),
    ],
)
def test_an_acceptance_record_reads_back(
    judgement: Unjudged | Crashed | Reported, recorded: RecordedAcceptance
) -> None:
    record = acceptance_record(AcceptanceRun(_RAN, judgement))
    assert acceptance_from_record(json.loads(json.dumps(record))) == recorded


def test_no_acceptance_is_recorded_as_none() -> None:
    assert acceptance_record(None) is None
    assert acceptance_from_record(None) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"judgement": "otro"},
        {"judgement": "crashed", "reason": None},
        {"judgement": "unjudged", "changes": None},
        {"judgement": "reported", "cases": None},
        {"exit_code": None},
    ],
)
def test_an_acceptance_record_out_of_its_shape_isnt_read(changes: dict[str, object]) -> None:
    record = acceptance_record(AcceptanceRun(_RAN, Crashed("c")))
    assert record is not None
    assert acceptance_from_record({**record, **changes}) is None


def test_the_controls_of_a_record_leave_out_what_doesnt_follow() -> None:
    good = control_record(_entry(Skipped("x")))
    assert recorded_controls({"a": good, "b": {"ok": True}, 3: good}) == {
        "a": RecordedSkipped("block", "x")
    }
    assert recorded_controls(None) == {}


def test_a_recorded_verification_lasts_what_its_commands_ran() -> None:
    controls = recorded_controls(
        {"a": control_record(_entry(_FAILED)), "b": control_record(_entry(Skipped("x")))}
    )
    acceptance = acceptance_from_record(acceptance_record(AcceptanceRun(_RAN, Crashed("c"))))

    assert recorded_duration(controls, acceptance) == 60
    assert recorded_duration(controls, None) == 30


def test_the_event_names_its_version() -> None:
    assert VERIFY_FINISHED == "lictor.verify.finished.v2"
    assert "lictor.verify.finished.v1" in OLDER_EVENTS
    assert VERIFY_FINISHED not in OLDER_EVENTS


# --- ADR-0008: a reader's result, from its read control's ---

_READER = ReaderControl("coverage", "L2", "unit", "--cov {out}")
_MEASURED = CoverageReport(3, 4)
_EMPTY = CoverageReport(0, 0)
_NO_CASE = Judged(JUnitReport(()), Passed())
_GREEN = Judged(JUnitReport((_PASS,)), Passed())
_RED = Judged(JUnitReport((_FAILURE,)), Failed("Fallaron 1 casos de test"))
_CUT = Judged(MissingReport(), Failed("Se cortó a los 60 s, su tiempo límite"))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(Skipped("sin tests/unit"), Skipped("sin tests/unit"), id="skipped"),
        pytest.param(
            NotExecuted("el intento elimina tests/unit"),
            NotExecuted("unit no se ejecutó: el intento elimina tests/unit"),
            id="not-executed",
        ),
        pytest.param(
            ReadFact(_NO_CASE, MissingReport()),
            Read("unit", MissingReport(), Passed()),
            id="no-case-no-report",
        ),
        pytest.param(
            ReadFact(_NO_CASE, _EMPTY), Read("unit", _EMPTY, Passed()), id="no-case-nothing"
        ),
        pytest.param(
            ReadFact(_GREEN, MissingReport()),
            Read("unit", MissingReport(), Failed("Sin informe de cobertura")),
            id="no-report",
        ),
        pytest.param(
            ReadFact(_CUT, MissingReport()),
            Read("unit", MissingReport(), Failed("Sin informe de cobertura")),
            id="cut-without-report",
        ),
        pytest.param(
            ReadFact(_GREEN, _EMPTY),
            Read("unit", _EMPTY, Failed(NOTHING_MEASURED)),
            id="nothing-measured",
        ),
        pytest.param(
            ReadFact(_NO_CASE, InvalidReport("a medias")),
            Read(
                "unit",
                InvalidReport("a medias"),
                Failed("El informe de cobertura no es válido: a medias"),
            ),
            id="invalid-even-with-no-case",
        ),
        pytest.param(
            ReadFact(_RED, _MEASURED), Read("unit", _MEASURED, Passed()), id="scores-a-red-run"
        ),
        pytest.param(
            ReadFact(_NO_CASE, _MEASURED), Read("unit", _MEASURED, Passed()), id="zero-is-a-score"
        ),
    ],
)
def test_what_a_reader_ends_up_with(source: ReadSource, expected: ReaderOutcome) -> None:
    """Each row of the table of ADR-0008, point 6; an unjudged unit never reaches the reader."""
    assert reader_outcome(_READER, source) == expected


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        pytest.param(
            ReadFact(_NO_CASE, MissingReport()),
            "El control no midió nada y declara un mínimo de 80 %",
            id="nothing-to-score",
        ),
        pytest.param(
            ReadFact(_GREEN, _MEASURED),
            "La cobertura, 75,0 % (3 de 4 líneas), no llega al mínimo de 80 %",
            id="below",
        ),
    ],
)
def test_a_reader_fails_below_its_minimum(source: ReadSource, reason: str) -> None:
    outcome = reader_outcome(replace(_READER, min_score=80), source)

    assert isinstance(outcome, Read)
    assert outcome.verdict == Failed(reason)


def test_a_read_scores_only_what_measured() -> None:
    assert Read("unit", _MEASURED, Passed()).score == _MEASURED.score
    assert Read("unit", _EMPTY, Passed()).score is None
    assert Read("unit", MissingReport(), Passed()).score is None


def _read_entry(outcome: ReaderOutcome, mode: ControlMode = "block") -> ReaderEntry:
    return ReaderEntry(replace(_READER, mode=mode), outcome)


@pytest.mark.parametrize(
    ("read", "mode", "expected"),
    [
        (Read("unit", _MEASURED, Passed()), "block", Proceed()),
        (Read("unit", MissingReport(), Failed("Sin informe de cobertura")), "measure", Proceed()),
        (
            Read("unit", MissingReport(), Failed("Sin informe de cobertura")),
            "block",
            Block("Sin informe de cobertura"),
        ),
    ],
)
def test_what_a_verification_does_with_a_read(
    read: Read, mode: ControlMode, expected: object
) -> None:
    """A failed read blocks with its reason alone: no cases, and no output it never printed."""
    assert consequence(_read_entry(read, mode), "salida de otro") == expected


def test_a_read_passes_or_fails_by_its_verdict() -> None:
    assert passed(Read("unit", _MEASURED, Passed())) is True
    assert passed(Read("unit", _EMPTY, Failed(NOTHING_MEASURED))) is False
    assert failure_of(Read("unit", _MEASURED, Passed())) is None


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        pytest.param(Executed(_RAN, _RED), True, id="red"),
        pytest.param(Executed(_RAN, _GREEN), False, id="green"),
        pytest.param(Skipped("x"), False, id="skipped"),
    ],
)
def test_a_score_read_from_a_failed_run_is_told_from_both_results(
    unit: CommandOutcome, expected: bool
) -> None:
    read = Read("unit", _MEASURED, Passed())
    entries = [
        CommandEntry(CommandControl("unit", "L2", "pytest {out}", "junit", mode="measure"), unit),
        _read_entry(read),
    ]

    assert from_failed_run(read, entries) is expected


@pytest.mark.parametrize(
    ("read", "mode", "recorded"),
    [
        pytest.param(
            Read("unit", _MEASURED, Passed()),
            "measure",
            RecordedRead("measure", "unit", True, None, _MEASURED.score),
            id="scored",
        ),
        pytest.param(
            Read("unit", MissingReport(), Failed("Sin informe de cobertura")),
            "block",
            RecordedRead("block", "unit", False, "Sin informe de cobertura", None),
            id="failed",
        ),
    ],
)
def test_a_read_record_reads_back_as_what_it_kept(
    read: Read, mode: ControlMode, recorded: RecordedRead
) -> None:
    record = control_record(_read_entry(read, mode))

    assert record["outcome"] == "read"
    assert "exit_code" not in record
    assert "duration_ms" not in record
    assert "output_path" not in record
    assert control_from_record(json.loads(json.dumps(record))) == recorded


@pytest.mark.parametrize(
    "changes",
    [{"reads": 1}, {"passed": "sí"}, {"score": "x"}, {"reads": None}],
)
def test_a_read_record_out_of_its_shape_isnt_read(changes: dict[str, object]) -> None:
    record = {"outcome": "read", "mode": "measure", "reads": "unit", "passed": True, "score": None}

    assert control_from_record({**record, **changes}) is None


def test_a_recorded_score_from_a_failed_run_is_told_from_the_record_of_its_read_control() -> None:
    read = RecordedRead("measure", "unit", True, None, _MEASURED.score)
    red = RecordedExecuted(
        "measure", RecordedRun(1, 3, False, "x.log"), RecordedJudged(False, "r", None, {})
    )
    green = replace(red, judgement=RecordedJudged(True, None, None, {}))

    assert recorded_from_failed_run(read, {"unit": red, "coverage": read}) is True
    assert recorded_from_failed_run(read, {"unit": green}) is False
    assert recorded_from_failed_run(read, {}) is False


def test_a_read_takes_no_time_of_its_own() -> None:
    entries = [_read_entry(Read("unit", _MEASURED, Passed()))]

    assert verification_duration(entries, None) == 0


def test_only_a_coverage_report_that_measured_nothing_is_nothing_to_measure() -> None:
    """S18: the fact is the report's, never the text of the verdict."""
    empty = CoverageReport(0, 0, 0, 0)
    some = CoverageReport(1, 2, 0, 0)
    reader = ReaderControl("coverage", "L2", "unit", "--cov")
    command = CommandControl("cov", "L2", "pytest {out}", "coverage")

    assert nothing_to_measure(ReaderEntry(reader, Read("unit", empty, Failed("x"))))
    assert not nothing_to_measure(ReaderEntry(reader, Read("unit", some, Passed())))
    assert not nothing_to_measure(ReaderEntry(reader, Read("unit", MissingReport(), Failed("x"))))
    ran = Ran(("pytest",), Execution(0, 1), "log")
    assert nothing_to_measure(CommandEntry(command, Executed(ran, Judged(empty, Failed("x")))))
    assert not nothing_to_measure(CommandEntry(command, Executed(ran, Judged(None, Passed()))))
    assert not nothing_to_measure(CommandEntry(command, Skipped("no")))
