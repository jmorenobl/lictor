"""Results as variants, what a verification does with each, and the journal's codec (ADR-0009)."""

import json

import pytest

from lictor.domain.controls import CommandControl, ControlMode
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
    ControlEntry,
    Executed,
    Judged,
    NotExecuted,
    Outcome,
    Proceed,
    Ran,
    RecordedAcceptance,
    RecordedExecuted,
    RecordedJudged,
    RecordedNotExecuted,
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
    passed,
    recorded_controls,
    recorded_duration,
    verification_duration,
)
from lictor.domain.verdicts import Crashed, Execution, Failed, Passed, Reported

_FAILURE = TestCaseResult(name="f", status="failure")
_PASS = TestCaseResult(name="p")
_RAN = Ran(("cmd", "{out}"), Execution(1, 60, duration_ms=30), "r/control_x.log")


def _entry(outcome: Outcome, mode: ControlMode = "block") -> ControlEntry:
    return ControlEntry(CommandControl("x", "L1", "cmd", "junit", mode=mode), outcome)


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
    outcome: Outcome, mode: ControlMode, expected: object
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
    outcome: Outcome, mode: ControlMode, recorded: object
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
