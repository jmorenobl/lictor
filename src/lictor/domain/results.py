"""What a control and the acceptance end up with, and what the journal keeps of it (ADR-0009).

A result is one of a few variants, each with only what it has: a control that was skipped has a
reason and nothing else, one that ran has what its command left, and one that ran and tampered
with the run's state has no verdict, because nobody gave one. What a verification does with a
result is `consequence`; what the agent reads of a failure is `failure_of`.

The journal keeps a projection of each result (`verify.finished.v2`): the verdict, the score and
how many cases ended how, never the report itself, which is in the evidence with its hash. The
codec lives here, so whoever writes and whoever reads use the same keys.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal, assert_never

from lictor.domain.controls import CommandControl, ControlMode, ReaderControl
from lictor.domain.model import Candidate, Score, TestCaseResult
from lictor.domain.reports import (
    CoverageReport,
    InvalidReport,
    JUnitReport,
    MissingReport,
    Report,
    ReportOf,
)
from lictor.domain.rules import score_from_record
from lictor.domain.verdicts import (
    Crashed,
    Execution,
    Failed,
    Passed,
    Reported,
    Verdict,
    coverage_read_verdict,
    enforce_min_score,
    report_cases,
    report_score,
)

VERIFY_FINISHED = "lictor.verify.finished.v2"
# Events whose shape this version no longer reads: a report counts them and leaves them alone.
OLDER_EVENTS = frozenset({"lictor.verify.finished.v1"})


@dataclass(frozen=True)
class Skipped:
    """It wasn't the control's turn: its path isn't there, or its plugin isn't loaded."""

    reason: str


@dataclass(frozen=True)
class NotExecuted:
    """It was its turn and it couldn't run: what it needed was removed by the attempt."""

    reason: str


@dataclass(frozen=True)
class Ran:
    """What a command left: what ran, how it ended, and where its log is."""

    command: tuple[str, ...]
    execution: Execution
    log: str  # relative to the project


@dataclass(frozen=True)
class Judged:
    """What the command left was read and judged; its cases and score come from that report."""

    report: Report | None  # None for an exit-code control, which leaves no report
    verdict: Verdict

    @property
    def cases(self) -> tuple[TestCaseResult, ...]:
        return report_cases(self.report)

    @property
    def score(self) -> Score | None:
        return report_score(self.report)


@dataclass(frozen=True)
class Unjudged:
    """The command changed the run's state: its report wasn't read, and there is no verdict."""

    changes: tuple[str, ...]


@dataclass(frozen=True)
class Executed:
    """The control ran its command."""

    ran: Ran
    judgement: Judged | Unjudged


@dataclass(frozen=True)
class Read:
    """A reader's verdict over the coverage its read control's run left (ADR-0008, point 6).

    It ran nothing, so it has no exit code, duration or log: those are its read control's.
    """

    reads: str
    report: ReportOf[CoverageReport]
    verdict: Verdict

    @property
    def score(self) -> Score | None:
        return report_score(self.report)


type CommandOutcome = Skipped | NotExecuted | Executed
type ReaderOutcome = Skipped | NotExecuted | Read
type Outcome = CommandOutcome | ReaderOutcome


@dataclass(frozen=True)
class CommandEntry:
    """A control that runs a command, and what it ended up with: never a `Read`."""

    control: CommandControl
    outcome: CommandOutcome


@dataclass(frozen=True)
class ReaderEntry:
    """A reader, and what it ended up with: never an `Executed`, because it runs nothing."""

    control: ReaderControl
    outcome: ReaderOutcome


# The mode is the control's, not the result's.
type ControlEntry = CommandEntry | ReaderEntry


@dataclass(frozen=True)
class ReadFact:
    """What a reader reads: the verdict its read control got, and the coverage that run left."""

    judged: "Judged"
    coverage: ReportOf[CoverageReport]


# What a reader finds at its turn. An unjudged read control ended the verification before it.
type ReadSource = Skipped | NotExecuted | ReadFact


def reader_outcome(reader: ReaderControl, source: ReadSource) -> ReaderOutcome:
    """What a reader ends up with, from what its read control ended up with (ADR-0008, point 6).

    A skipped read control skips it with the same reason; one that couldn't run leaves it
    without a run to read. Otherwise it scores whatever report there is, whatever the verdict
    of its read control, and its own `min_score` applies.
    """
    match source:
        case Skipped(reason=reason):
            return Skipped(reason)
        case NotExecuted(reason=reason):
            return NotExecuted(f"{reader.reads} no se ejecutó: {reason}")
        case ReadFact(judged=judged, coverage=coverage):
            verdict = coverage_read_verdict(coverage, ran_no_case=_ran_no_case(judged))
            return Read(
                reader.reads,
                coverage,
                enforce_min_score(verdict, report_score(coverage), reader.min_score),
            )
        case _:
            assert_never(source)


def _ran_no_case(judged: "Judged") -> bool:
    """Whether a JUnit control passed with no case: how it counts a run with no tests."""
    return (
        isinstance(judged.verdict, Passed)
        and isinstance(judged.report, JUnitReport)
        and not judged.report.cases
    )


@dataclass(frozen=True)
class AcceptanceRun:
    """The acceptance, which is no control: no mode, and no `ok` that means the same everywhere.

    In a run its verdict is the scenarios'; in `lictor check`, `acceptance_verdict`.
    """

    ran: Ran
    judgement: Unjudged | Crashed | Reported


@dataclass(frozen=True)
class VerifyResult:
    """Aggregated result of all verification steps for an attempt."""

    ok: bool
    failed_at: str | None = None
    condensed_error: str | None = None
    controls: tuple[ControlEntry, ...] = ()
    acceptance: AcceptanceRun | None = None  # None when the verification stopped before it
    scenario_results: dict[str, str] = field(default_factory=dict)
    reverted_files: tuple[str, ...] = field(default_factory=tuple)
    error_path: str | None = None
    candidate: Candidate | None = None
    evidence: dict[str, str] = field(default_factory=dict)
    collateral: tuple[str, ...] = ()  # pending scenarios the candidate also turns green
    failure: tuple[str, str] | None = None  # kind and origin of the scenario's failed test

    def outcome(self, control_id: str) -> Outcome | None:
        """What the control with that id ended up with, if the verification reached it."""
        return next((e.outcome for e in self.controls if e.control.id == control_id), None)


@dataclass(frozen=True)
class Proceed:
    """The verification goes on."""


@dataclass(frozen=True)
class Block:
    """The verification fails at this control."""

    reason: str
    cases: tuple[TestCaseResult, ...] = ()


@dataclass(frozen=True)
class StateTampered:
    """The verification ends at `L0-estado`, whatever the control's mode."""

    changes: tuple[str, ...]


type Consequence = Proceed | Block | StateTampered


def passed(outcome: Outcome) -> bool:
    """Whether a result is a pass: skipped, or judged and passed. Unjudged is no pass."""
    match outcome:
        case Skipped():
            return True
        case NotExecuted():
            return False
        case Executed(judgement=judgement):
            return isinstance(judgement, Judged) and isinstance(judgement.verdict, Passed)
        case Read(verdict=verdict):
            return isinstance(verdict, Passed)
        case _:
            assert_never(outcome)


def failure_of(outcome: Outcome, output: str = "") -> Block | None:
    """What the agent reads of a failed result, or None if there was no failure to read.

    The output joins the reason when only it can explain the failure: the command left no report,
    an unreadable one, or a JUnit report with no case. An unjudged result has no failure: its
    reason is `L0-estado`'s. A reader's failure is its reason alone: it printed nothing, and the
    output of the run it read belongs to its read control.
    """
    match outcome:
        case NotExecuted(reason=reason):
            return Block(reason)
        case Read(verdict=verdict):
            return Block(verdict.reason) if isinstance(verdict, Failed) else None
        case Executed(judgement=judgement):
            if not isinstance(judgement, Judged) or not isinstance(judgement.verdict, Failed):
                return None
            reason = judgement.verdict.reason
            if _only_the_output_explains(judgement.report) and output:
                reason = f"{reason}\n{output}"
            return Block(reason, judgement.cases)
        case Skipped():
            return None
        case _:
            assert_never(outcome)


def _only_the_output_explains(report: Report | None) -> bool:
    if isinstance(report, MissingReport | InvalidReport):
        return True
    return isinstance(report, JUnitReport) and not report.cases


def consequence(entry: ControlEntry, output: str = "") -> Consequence:
    """What a verification does with a control's result: go on, fail there, or end at L0-estado.

    A control that tampered ends it whatever its mode: a `measure` control can't let that pass.
    """
    outcome = entry.outcome
    if isinstance(outcome, Executed) and isinstance(outcome.judgement, Unjudged):
        return StateTampered(outcome.judgement.changes)
    failure = failure_of(outcome, output)
    if failure is None or entry.control.mode == "measure":
        return Proceed()
    return failure


def failed_run_mark(reads: str) -> str:
    """What a score read from a run whose own verdict failed carries (ADR-0008, point 8)."""
    return f" (de un {reads} fallido)"


def from_failed_run(read: Read, entries: Sequence[ControlEntry]) -> bool:
    """Whether a reader read a run whose control failed, from both results of one verification.

    It is one fact, kept once: in the read control's result, never copied into the reader's.
    """
    source = next((e.outcome for e in entries if e.control.id == read.reads), None)
    return (
        isinstance(source, Executed)
        and isinstance(source.judgement, Judged)
        and isinstance(source.judgement.verdict, Failed)
    )


def acceptance_cases(acceptance: AcceptanceRun) -> tuple[TestCaseResult, ...]:
    """The cases of an acceptance report that could be trusted; a crashed one gives none."""
    return acceptance.judgement.cases if isinstance(acceptance.judgement, Reported) else ()


def verification_duration(
    controls: Sequence[ControlEntry], acceptance: AcceptanceRun | None
) -> int:
    """How long the commands of a verification took: what ran, and nothing that didn't."""
    durations = [
        entry.outcome.ran.execution.duration_ms
        for entry in controls
        if isinstance(entry.outcome, Executed)
    ]
    if acceptance is not None:
        durations.append(acceptance.ran.execution.duration_ms)
    return sum(durations)


# The journal's projection of each result, and how it reads back.

type JudgementKind = Literal["judged", "unjudged"]


@dataclass(frozen=True)
class RecordedSkipped:
    mode: ControlMode
    reason: str


@dataclass(frozen=True)
class RecordedNotExecuted:
    mode: ControlMode
    reason: str


@dataclass(frozen=True)
class RecordedRun:
    """How a recorded command ended, and where its log is."""

    exit_code: int
    duration_ms: int
    timed_out: bool
    output_path: str


@dataclass(frozen=True)
class RecordedJudged:
    passed: bool
    reason: str | None
    score: Score | None
    cases: Mapping[str, int]


@dataclass(frozen=True)
class RecordedUnjudged:
    changes: tuple[str, ...]


@dataclass(frozen=True)
class RecordedExecuted:
    mode: ControlMode
    ran: RecordedRun
    judgement: RecordedJudged | RecordedUnjudged


@dataclass(frozen=True)
class RecordedRead:
    """A reader's verdict, and the control whose run it read: no exit code, duration or log."""

    mode: ControlMode
    reads: str
    passed: bool
    reason: str | None
    score: Score | None


type RecordedControl = RecordedSkipped | RecordedNotExecuted | RecordedExecuted | RecordedRead


@dataclass(frozen=True)
class RecordedAcceptance:
    ran: RecordedRun
    judgement: Literal["unjudged", "crashed", "reported"]
    reason: str | None = None
    changes: tuple[str, ...] = ()
    cases: Mapping[str, int] = field(default_factory=dict)


def _case_counts(cases: Sequence[TestCaseResult]) -> dict[str, int]:
    return dict(Counter(case.status for case in cases))


def _run_record(ran: Ran) -> dict[str, object]:
    return {
        "exit_code": ran.execution.exit_code,
        "duration_ms": ran.execution.duration_ms,
        "timed_out": ran.execution.timed_out,
        "output_path": ran.log,
    }


def control_record(entry: ControlEntry) -> dict[str, object]:
    """What the journal keeps of a control's result."""
    mode = entry.control.mode
    match entry.outcome:
        case Skipped(reason=reason):
            return {"outcome": "skipped", "mode": mode, "reason": reason}
        case NotExecuted(reason=reason):
            return {"outcome": "not_executed", "mode": mode, "reason": reason}
        case Executed(ran=ran, judgement=judged):
            record = {"outcome": "executed", "mode": mode, **_run_record(ran)}
            if isinstance(judged, Unjudged):
                return {**record, "judgement": "unjudged", "changes": list(judged.changes)}
            verdict = judged.verdict
            return {
                **record,
                "judgement": "judged",
                "passed": isinstance(verdict, Passed),
                "reason": verdict.reason if isinstance(verdict, Failed) else None,
                "score": asdict(judged.score) if judged.score else None,
                "cases": _case_counts(judged.cases),
            }
        case Read(reads=reads, verdict=verdict) as read:
            return {
                "outcome": "read",
                "mode": mode,
                "reads": reads,
                "passed": isinstance(verdict, Passed),
                "reason": verdict.reason if isinstance(verdict, Failed) else None,
                "score": asdict(read.score) if read.score else None,
            }
        case _:
            assert_never(entry.outcome)


def acceptance_record(acceptance: AcceptanceRun | None) -> dict[str, object] | None:
    """What the journal keeps of the acceptance, or None if the verification stopped before it."""
    if acceptance is None:
        return None
    record = _run_record(acceptance.ran)
    match acceptance.judgement:
        case Unjudged(changes=changes):
            return {**record, "judgement": "unjudged", "changes": list(changes)}
        case Crashed(reason=reason):
            return {**record, "judgement": "crashed", "reason": reason}
        case Reported(cases=cases):
            return {**record, "judgement": "reported", "cases": _case_counts(cases)}
        case _:
            assert_never(acceptance.judgement)


def _mode(raw: Mapping[str, object]) -> ControlMode | None:
    match raw.get("mode"):
        case "block":
            return "block"
        case "measure":
            return "measure"
        case _:
            return None


def _text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _int(raw: Mapping[str, object], key: str) -> int | None:
    value = raw.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _counts(raw: object) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    counts = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, int)}
    return counts if len(counts) == len(raw) else None


def _ran_from(raw: Mapping[str, object]) -> RecordedRun | None:
    exit_code, duration = _int(raw, "exit_code"), _int(raw, "duration_ms")
    output_path, timed_out = _text(raw, "output_path"), raw.get("timed_out")
    if exit_code is None or duration is None or output_path is None:
        return None
    if not isinstance(timed_out, bool):
        return None
    return RecordedRun(exit_code, duration, timed_out, output_path)


def _changes(raw: object) -> tuple[str, ...] | None:
    if not isinstance(raw, list) or not all(isinstance(c, str) for c in raw):
        return None
    return tuple(raw)


def control_from_record(raw: object) -> RecordedControl | None:
    """A control's record read back, or None if it doesn't follow the schema of this version."""
    if not isinstance(raw, dict) or (mode := _mode(raw)) is None:
        return None
    match outcome := raw.get("outcome"):
        case "skipped" | "not_executed":
            reason = _text(raw, "reason")
            kind = RecordedSkipped if outcome == "skipped" else RecordedNotExecuted
            return None if reason is None else kind(mode, reason)
        case "executed":
            ran, judgement = _ran_from(raw), _judgement_from(raw)
            return (
                None if ran is None or judgement is None else RecordedExecuted(mode, ran, judgement)
            )
        case "read":
            return _read_from(mode, raw)
        case _:
            return None


def _read_from(mode: ControlMode, raw: Mapping[str, object]) -> RecordedRead | None:
    reads, was_passed = _text(raw, "reads"), raw.get("passed")
    if reads is None or not isinstance(was_passed, bool):
        return None
    score_raw = raw.get("score")
    score = score_from_record(score_raw) if score_raw is not None else None
    if score_raw is not None and score is None:
        return None
    return RecordedRead(mode, reads, was_passed, _text(raw, "reason"), score)


def _judgement_from(raw: Mapping[str, object]) -> RecordedJudged | RecordedUnjudged | None:
    if raw.get("judgement") == "unjudged":
        changes = _changes(raw.get("changes"))
        return RecordedUnjudged(changes) if changes is not None else None
    if raw.get("judgement") != "judged":
        return None
    was_passed, cases = raw.get("passed"), _counts(raw.get("cases"))
    if not isinstance(was_passed, bool) or cases is None:
        return None
    score_raw = raw.get("score")
    score = score_from_record(score_raw) if score_raw is not None else None
    if score_raw is not None and score is None:
        return None
    return RecordedJudged(was_passed, _text(raw, "reason"), score, cases)


def recorded_from_failed_run(read: RecordedRead, recorded: Mapping[str, RecordedControl]) -> bool:
    """`from_failed_run` over what the journal kept of one verification."""
    source = recorded.get(read.reads)
    return (
        isinstance(source, RecordedExecuted)
        and isinstance(source.judgement, RecordedJudged)
        and not source.judgement.passed
    )


def recorded_controls(raw: object) -> dict[str, RecordedControl]:
    """The controls of a recorded verification, by id; a record that doesn't follow is left out."""
    if not isinstance(raw, dict):
        return {}
    return {
        control_id: recorded
        for control_id, control in raw.items()
        if isinstance(control_id, str) and (recorded := control_from_record(control)) is not None
    }


def acceptance_from_record(raw: object) -> RecordedAcceptance | None:
    """The acceptance's record read back, or None if there is none or it doesn't follow it."""
    if not isinstance(raw, dict):
        return None
    ran = _ran_from(raw)
    if ran is None:
        return None
    match raw.get("judgement"):
        case "unjudged":
            changes = _changes(raw.get("changes"))
            return None if changes is None else RecordedAcceptance(ran, "unjudged", changes=changes)
        case "crashed":
            reason = _text(raw, "reason")
            return RecordedAcceptance(ran, "crashed", reason=reason) if reason else None
        case "reported":
            cases = _counts(raw.get("cases"))
            return RecordedAcceptance(ran, "reported", cases=cases) if cases is not None else None
        case _:
            return None


def recorded_duration(
    controls: Mapping[str, RecordedControl], acceptance: RecordedAcceptance | None
) -> int:
    """How long the commands of a recorded verification took, as `verification_duration` says."""
    durations = [c.ran.duration_ms for c in controls.values() if isinstance(c, RecordedExecuted)]
    if acceptance is not None:
        durations.append(acceptance.ran.duration_ms)
    return sum(durations)
