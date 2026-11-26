"""Immutable domain models and value objects for Lictor."""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal

from lictor.domain.errors import SpecError

_TAG_PATTERN = re.compile(r"^@[A-Z][A-Z0-9]*-[0-9]+$")
_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")


@dataclass(frozen=True)
class ScenarioId:
    """Strictly validated Gherkin scenario identifier.

    Canonical format matches [A-Z][A-Z0-9]*-[0-9]+ (e.g. 'IVA-001').
    Associated Gherkin tag matches @[A-Z][A-Z0-9]*-[0-9]+ (e.g. '@IVA-001').
    """

    value: str

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise SpecError(f"Scenario ID must be a string, got {type(value).__name__}")

        raw = value.strip()
        if raw.startswith("@"):
            if not _TAG_PATTERN.match(raw):
                raise SpecError(f"Invalid scenario ID or tag: {value!r}")
            raw = raw[1:]
        elif not _ID_PATTERN.match(raw):
            raise SpecError(f"Invalid scenario ID or tag: {value!r}")

        object.__setattr__(self, "value", raw)

    @classmethod
    def from_tag(cls, tag: str) -> "ScenarioId":
        """Instantiate ScenarioId from a Gherkin tag like '@IVA-001'."""
        if not isinstance(tag, str) or not _TAG_PATTERN.match(tag.strip()):
            raise SpecError(f"Invalid scenario ID or tag: {tag!r}")
        return cls(tag.strip())

    @property
    def tag(self) -> str:
        """Gherkin tag format, e.g. '@IVA-001'."""
        return f"@{self.value}"

    @property
    def normalized(self) -> str:
        """Normalized string for matching test names, e.g. 'iva_001'.

        Converts to lowercase and replaces non-alphanumeric chars with '_'.
        """
        return re.sub(r"[^a-z0-9]+", "_", self.value.lower()).strip("_")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"ScenarioId({self.value!r})"


@dataclass(frozen=True)
class Scenario:
    """A single specification scenario."""

    id: ScenarioId
    name: str
    file: str
    line: int
    spec_hash: str = ""  # what the scenario demands, without names, tags or comments

    def __post_init__(self) -> None:
        if not isinstance(self.id, ScenarioId):
            object.__setattr__(self, "id", ScenarioId(self.id))


@dataclass(frozen=True)
class Tokens:
    """Token accounting across input, output, and prompt caching."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def __add__(self, other: "Tokens") -> "Tokens":
        if not isinstance(other, Tokens):
            return NotImplemented
        return Tokens(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
        )


@dataclass(frozen=True)
class Totals:
    """Accumulator for tokens, cost, and execution durations."""

    tokens: Tokens = field(default_factory=Tokens)
    cost_usd: float = 0.0
    agent_duration_ms: int = 0
    verify_duration_ms: int = 0

    def add_agent(
        self,
        tokens: Tokens,
        cost_usd: float | None,
        duration_ms: int,
    ) -> "Totals":
        return Totals(
            tokens=self.tokens + tokens,
            cost_usd=self.cost_usd + (cost_usd or 0.0),
            agent_duration_ms=self.agent_duration_ms + duration_ms,
            verify_duration_ms=self.verify_duration_ms,
        )

    def add_verify(self, duration_ms: int) -> "Totals":
        return Totals(
            tokens=self.tokens,
            cost_usd=self.cost_usd,
            agent_duration_ms=self.agent_duration_ms,
            verify_duration_ms=self.verify_duration_ms + duration_ms,
        )

    def __add__(self, other: "Totals") -> "Totals":
        if not isinstance(other, Totals):
            return NotImplemented
        return Totals(
            tokens=self.tokens + other.tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            agent_duration_ms=self.agent_duration_ms + other.agent_duration_ms,
            verify_duration_ms=self.verify_duration_ms + other.verify_duration_ms,
        )


AgentRole = Literal["driver", "implement", "refactor"]


@dataclass(frozen=True)
class AgentTask:
    """Task passed to an agent executor."""

    role: AgentRole
    prompt: str
    prompt_sha: str
    cwd: Path
    model: str
    allowed_tools: Sequence[str]
    max_turns: int
    timeout_s: int
    raw_path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "raw_path", Path(self.raw_path))


AgentStatus = Literal["ok", "error", "auth", "max_turns", "rate_limited", "timeout"]
# What a call means for the run, once its status and what it left in the copy are read.
AgentSignal = Literal["ok", "spec_conflict", "rate_limited", "unavailable", "error", "rejected"]
CostKind = Literal["notional_subscription", "estimated"]


@dataclass(frozen=True)
class PermissionDenial:
    """A tool call the agent attempted and the backend refused."""

    tool: str
    target: str = ""


@dataclass(frozen=True)
class AgentResult:
    """Result returned by an agent executor."""

    status: AgentStatus
    spec_conflict: str | None = None
    text: str = ""
    session_id: str | None = None
    num_turns: int = 0
    duration_ms: int = 0
    tokens: Tokens = field(default_factory=Tokens)
    cost_usd: float | None = None
    cost_kind: CostKind = "notional_subscription"
    raw_path: Path | None = None
    permission_denials: tuple[PermissionDenial, ...] = ()
    exit_code: int | None = None
    stderr_path: Path | None = None
    cli_version: str = ""  # as the backend reported it when the call started
    project_skills: tuple[str, ...] = ()  # the project's skills the backend loaded; never any


@dataclass(frozen=True)
class Health:
    """CLI and authentication health check result."""

    ok: bool
    cli_version: str
    auth_status: str
    message: str | None = None


PreconditionKind = Literal["config", "repository", "environment"]


@dataclass(frozen=True)
class PreconditionProblem:
    """Something that keeps a command from starting, and who has to fix it.

    `config` and `repository` are fixed in the project; `environment` is fixed on the machine.
    """

    kind: PreconditionKind
    message: str


@dataclass(frozen=True)
class MutationScore:
    """Mutants of a mutation run by outcome, and the share the tests killed."""

    killed: int = 0
    survived: int = 0
    timeout: int = 0
    no_tests: int = 0
    suspicious: int = 0
    segfault: int = 0
    skipped: int = 0
    total: int = 0
    percent: float = 0.0
    kind: Literal["mutation"] = "mutation"


@dataclass(frozen=True)
class CoverageScore:
    """Lines and branches the tests ran, and the share of both together, as coverage.py counts."""

    covered_lines: int = 0
    num_statements: int = 0
    covered_branches: int = 0
    num_branches: int = 0
    percent: float = 0.0
    kind: Literal["coverage"] = "coverage"


# What a control's report scores; `kind` tells them apart once in the journal.
Score = MutationScore | CoverageScore


@dataclass(frozen=True)
class CommandOutput:
    """Captured output of a system process execution."""

    returncode: int
    stdout: str
    stderr: str
    duration_ms: int = 0
    timed_out: bool = False  # killed for running out of time; the returncode says nothing then
    started: bool = True  # False when the command couldn't even start, e.g. it wasn't found


# Properties a profile's runner may attach to each test case. Lictor names them, not the tool.
FAILURE_PROPERTY = "lictor.failure"  # assertion | not_implemented | impostor | error
ORIGIN_PROPERTY = "lictor.origin"  # innermost frame of the failure, relative to the project
FRAME_PROPERTY = "lictor.frame"  # last frame of the failure inside the project, relative to it
LINE_PROPERTY = "lictor.line"  # the line of that frame, from 1
INTERFACE_CALLS_PROPERTY = "lictor.interface.calls"  # calls from the driver to the interface
INTERFACE_ALTERED_PROPERTY = "lictor.interface.altered"  # those whose result the impostor altered
# Of those, the ones whose real result was None, as an order that only changes the state returns.
INTERFACE_ALTERED_NONE_PROPERTY = "lictor.interface.altered_none"
# Calls the driver made through a library, never altered: the project calling the interface after
# the driver entered it through a library, and a library calling a function the driver handed it.
INTERFACE_VIA_PROPERTY = "lictor.interface.via"
INTERFACE_HANDED_PROPERTY = "lictor.interface.handed"
INTERFACE_ENTRY_PROPERTY = "lictor.interface.entry"  # by step: the libraries, comma-separated
SCENARIO_PROPERTY = "lictor.scenario"  # tags of the Gherkin scenario the test executed
FEATURE_PROPERTY = "lictor.feature"  # its feature file, relative to the project
STEP_PROPERTY = "lictor.step"  # the kind of Gherkin step the failure was raised in
# The driver's calls to the interface, numbered from 0 in each test: those that raised unaltered,
# and those whose exception the impostor swallowed or replaced, with their step (`3:then`).
INTERFACE_RAISED_AT_PROPERTY = "lictor.interface.raised_at"
INTERFACE_SWALLOWED_AT_PROPERTY = "lictor.interface.swallowed_at"
# Each call of the driver's, in a run that traces them: `step:function:hash.hash` (ADR-0016).
INTERFACE_TRACE_PROPERTY = "lictor.interface.trace"
GHERKIN_VALUES_PROPERTY = "lictor.gherkin.values"  # the values the Then steps got from the Gherkin
GHERKIN_ALTERABLE_PROPERTY = "lictor.gherkin.alterable"  # which of them could be changed
GHERKIN_UNREAD_PROPERTY = "lictor.gherkin.unread"  # JSON: [step, parameter, value] of each unread
GHERKIN_UNRECEIVED_PROPERTY = "lictor.gherkin.unreceived"  # extracted, not taken by the step
GHERKIN_ALTERED_PROPERTY = "lictor.gherkin.altered"  # "1" if the run changed the value it was told

# The kinds of a Gherkin step, as the runner names them: «And» and «But» take the one before.
Step = Literal["given", "when", "then"]
STEPS: tuple[Step, ...] = ("given", "when", "then")
InterfaceCount = Literal["calls", "altered", "via", "handed"]
_INTERFACE_PROPERTIES: Mapping[InterfaceCount, str] = {
    "calls": INTERFACE_CALLS_PROPERTY,
    "altered": INTERFACE_ALTERED_PROPERTY,
    "via": INTERFACE_VIA_PROPERTY,
    "handed": INTERFACE_HANDED_PROPERTY,
}


def step_property(name: str, step: Step) -> str:
    """The property that counts, for one kind of step, what `name` counts for the whole test."""
    return f"{name}.{step}"


def _count(value: str) -> int:
    """Read a count a plugin reported; anything that isn't one counts as nothing."""
    return int(value) if value.isascii() and value.isdigit() else 0


def _entries(value: str) -> tuple[str, ...]:
    return tuple(entry for entry in value.split(",") if entry)


def _numbers(value: str) -> tuple[int, ...]:
    return tuple(int(n) for n in _entries(value) if n.isascii() and n.isdigit())


@dataclass(frozen=True)
class UnreadValue:
    """A value of the Gherkin a Then got and never reads: the step, its parameter, the value."""

    step: str
    parameter: str
    value: str


@dataclass(frozen=True)
class StepCounts:
    """A count of one test, split by the kind of step it happened in, and what fell outside one."""

    given: int = 0
    when: int = 0
    then: int = 0
    outside: int = 0

    @property
    def from_when(self) -> int:
        """What happened from the When on: what a step that acts and one that checks did."""
        return self.when + self.then


@dataclass(frozen=True)
class TestCaseResult:
    """Outcome of an individual test case."""

    name: str
    classname: str = ""
    status: Literal["passed", "failure", "error", "skipped"] = "passed"
    message: str | None = None
    details: str | None = None
    properties: Mapping[str, str] = field(default_factory=dict)

    @property
    def failure_kind(self) -> str:
        """What kind of failure the runner saw, or "" if it didn't say."""
        return self.properties.get(FAILURE_PROPERTY, "")

    @property
    def origin(self) -> str:
        """Where the failure was raised, or "" if outside the project or not reported."""
        return self.properties.get(ORIGIN_PROPERTY, "")

    @property
    def frame(self) -> str:
        """The last frame of the failure inside the project, or "" if none is or it isn't said.

        An assertion the test runner raises for the test, as when an expected exception never
        came, has its innermost frame inside the runner: this is the test's own line.
        """
        return self.properties.get(FRAME_PROPERTY, "")

    @property
    def frame_line(self) -> int | None:
        """The line of `frame`, or None if it isn't said."""
        line = _count(self.properties.get(LINE_PROPERTY, ""))
        return line or None

    @property
    def interface_calls(self) -> int:
        """How many times the test called the interface, as the impostor counted them."""
        return _count(self.properties.get(INTERFACE_CALLS_PROPERTY, ""))

    @property
    def interface_altered(self) -> int:
        """How many results of those calls the impostor managed to alter."""
        return _count(self.properties.get(INTERFACE_ALTERED_PROPERTY, ""))

    @property
    def interface_altered_none(self) -> int:
        """How many of the altered results were None: telling them apart says nothing of a test."""
        return _count(self.properties.get(INTERFACE_ALTERED_NONE_PROPERTY, ""))

    @property
    def failed_step(self) -> Step | None:
        """The kind of step the failure was raised in, or None outside a step or not reported."""
        value = self.properties.get(STEP_PROPERTY, "")
        return next((step for step in STEPS if step == value), None)

    def interface_by_step(self, count: InterfaceCount) -> StepCounts:
        """What the impostor counted for this test, by step; `outside` is what no step holds."""
        name = _INTERFACE_PROPERTIES[count]
        given, when, then = (
            _count(self.properties.get(step_property(name, step), "")) for step in STEPS
        )
        total = _count(self.properties.get(name, ""))
        return StepCounts(given, when, then, max(total - given - when - then, 0))

    def interface_entries(self, steps: Sequence[Step]) -> tuple[str, ...]:
        """The libraries the driver called into from these steps, as the impostor named them."""
        found = {
            name
            for step in steps
            for name in self.properties.get(
                step_property(INTERFACE_ENTRY_PROPERTY, step), ""
            ).split(",")
            if name
        }
        return tuple(sorted(found))

    @property
    def interface_raised_at(self) -> frozenset[int]:
        """The driver's calls, by number, that raised with nothing altered."""
        return frozenset(_numbers(self.properties.get(INTERFACE_RAISED_AT_PROPERTY, "")))

    @property
    def interface_swallowed_at(self) -> tuple[tuple[int, Step | None], ...]:
        """The driver's calls whose exception the impostor swallowed or replaced, with the step."""
        found: list[tuple[int, Step | None]] = []
        for entry in _entries(self.properties.get(INTERFACE_SWALLOWED_AT_PROPERTY, "")):
            number, _, step = entry.partition(":")
            if number.isascii() and number.isdigit():
                found.append((int(number), next((s for s in STEPS if s == step), None)))
        return tuple(found)

    @property
    def interface_trace(self) -> tuple[str, ...]:
        """Each call of the driver's to the interface, in a run that traced them."""
        return _entries(self.properties.get(INTERFACE_TRACE_PROPERTY, ""))

    @property
    def gherkin_values(self) -> int:
        """How many values the Then steps received from the Gherkin, as its impostor counted."""
        return _count(self.properties.get(GHERKIN_VALUES_PROPERTY, ""))

    @property
    def gherkin_alterable(self) -> tuple[int, ...]:
        """The numbers of those values the impostor of the Gherkin can change."""
        return _numbers(self.properties.get(GHERKIN_ALTERABLE_PROPERTY, ""))

    @property
    def gherkin_unread(self) -> tuple[UnreadValue, ...]:
        """The values a Then received from the Gherkin and its code never reads."""
        try:
            raw = json.loads(self.properties.get(GHERKIN_UNREAD_PROPERTY, "") or "[]")
        except ValueError:
            return ()
        if not isinstance(raw, list):
            return ()
        return tuple(
            UnreadValue(*entry)
            for entry in raw
            if isinstance(entry, list)
            and len(entry) == len(fields(UnreadValue))
            and all(isinstance(part, str) for part in entry)
        )

    @property
    def gherkin_unreceived(self) -> int:
        """How many values the step's parser extracted that its function doesn't take."""
        return _count(self.properties.get(GHERKIN_UNRECEIVED_PROPERTY, ""))

    @property
    def gherkin_altered(self) -> bool:
        """Whether the run changed the value of the Gherkin it was told to."""
        return self.properties.get(GHERKIN_ALTERED_PROPERTY, "") == "1"

    @property
    def scenario_tags(self) -> tuple[str, ...]:
        """Tags of the Gherkin scenario the test executed; empty if it executed none."""
        return tuple(self.properties.get(SCENARIO_PROPERTY, "").split())

    @property
    def feature(self) -> str:
        """Feature file of the scenario the test executed, or ""."""
        return self.properties.get(FEATURE_PROPERTY, "")


@dataclass(frozen=True)
class JournalDamage:
    """Lines of the journal that were skipped when reading it.

    `corrupt` lines aren't JSON: a write cut short, or an edit. `foreign` lines are JSON that no
    Lictor event looks like, which only someone else can have written. `older` are events of a
    shape this version no longer reads, left by an earlier one. A forged line with the exact shape
    of an event can't be told apart here, and is in no count.
    """

    corrupt: int = 0
    foreign: int = 0
    older: int = 0

    @property
    def total(self) -> int:
        return self.corrupt + self.foreign + self.older


@dataclass(frozen=True)
class RepoPin:
    """What a repository holds before untrusted code runs: its HEAD and its control files."""

    head: str
    files: dict[str, bytes] = field(default_factory=dict)


_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"
DELETED_MODE = "000000"


@dataclass(frozen=True)
class Candidate:
    """A commit outside the branch: only what passes verification becomes green."""

    sha: str
    tree: str
    parent: str


@dataclass(frozen=True)
class TreeChange:
    """A path that differs from HEAD in a tree, with the mode it has there and the one it had."""

    path: str
    mode: str
    old_mode: str  # `000000` for a path HEAD doesn't have

    @property
    def is_link(self) -> bool:
        """Tell links and nested repositories: what they point to is not in the commit."""
        return self.mode in (_SYMLINK_MODE, _GITLINK_MODE)

    @property
    def is_deleted(self) -> bool:
        """Tell a path the tree no longer has."""
        return self.mode == DELETED_MODE

    @property
    def is_new(self) -> bool:
        """Tell a path HEAD didn't have."""
        return self.old_mode == DELETED_MODE


@dataclass(frozen=True)
class Event:
    """Event in CloudEvents 1.0 format."""

    id: str
    type: str
    source: str
    time: str
    data: dict[str, object]
    specversion: str = "1.0"
    runid: str | None = None
    scenarioid: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Convert to a CloudEvents JSON-serializable dictionary."""
        res: dict[str, object] = {
            "specversion": self.specversion,
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "time": self.time,
            "data": self.data,
        }
        if self.runid is not None:
            res["runid"] = self.runid
        if self.scenarioid is not None:
            res["scenarioid"] = self.scenarioid
        return res

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> "Event":
        """Reconstruct an Event from a dictionary."""
        data_raw = d.get("data")
        data_dict: dict[str, object] = data_raw if isinstance(data_raw, dict) else {}
        runid_raw = d.get("runid")
        scenarioid_raw = d.get("scenarioid")
        return cls(
            specversion=str(d.get("specversion", "1.0")),
            id=str(d["id"]),
            source=str(d["source"]),
            type=str(d["type"]),
            time=str(d["time"]),
            runid=str(runid_raw) if runid_raw is not None else None,
            scenarioid=str(scenarioid_raw) if scenarioid_raw is not None else None,
            data=data_dict,
        )
