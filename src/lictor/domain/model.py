"""Immutable domain models and value objects for Lictor."""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
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


AgentRole = Literal["driver", "implement"]


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


@dataclass(frozen=True)
class Health:
    """CLI and authentication health check result."""

    ok: bool
    cli_version: str
    auth_status: str
    message: str | None = None


@dataclass(frozen=True)
class CommandOutput:
    """Captured output of a system process execution."""

    returncode: int
    stdout: str
    stderr: str
    duration_ms: int = 0


@dataclass(frozen=True)
class TestCaseResult:
    """Outcome of an individual test case."""

    name: str
    classname: str = ""
    status: Literal["passed", "failure", "error", "skipped"] = "passed"
    message: str | None = None
    details: str | None = None


@dataclass(frozen=True)
class ControlResult:
    """Result of running a single verification control."""

    ok: bool
    duration_ms: int = 0
    error: str | None = None
    test_cases: tuple[TestCaseResult, ...] = field(default_factory=tuple)
    exit_code: int | None = None
    mode: str = "block"
    output_path: str | None = None
    skipped: str | None = None  # why the control didn't run, when it didn't


@dataclass(frozen=True)
class JournalDamage:
    """Lines of the journal that were skipped when reading it.

    `corrupt` lines aren't JSON: a write cut short, or an edit. `foreign` lines are JSON that no
    Lictor event looks like, which only someone else can have written. A forged line with the exact
    shape of an event can't be told apart here, and is in neither count.
    """

    corrupt: int = 0
    foreign: int = 0

    @property
    def total(self) -> int:
        return self.corrupt + self.foreign


@dataclass(frozen=True)
class RepoPin:
    """What a repository holds before untrusted code runs: its HEAD and its control files."""

    head: str
    files: dict[str, bytes] = field(default_factory=dict)


_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"


@dataclass(frozen=True)
class Candidate:
    """A commit outside the branch: only what passes verification becomes green."""

    sha: str
    tree: str
    parent: str


@dataclass(frozen=True)
class TreeChange:
    """A path that differs from HEAD in a tree, with the mode it has there."""

    path: str
    mode: str

    @property
    def is_link(self) -> bool:
        """Tell links and nested repositories: what they point to is not in the commit."""
        return self.mode in (_SYMLINK_MODE, _GITLINK_MODE)


@dataclass(frozen=True)
class VerifyResult:
    """Aggregated result of all verification steps for an attempt."""

    ok: bool
    failed_at: str | None = None
    condensed_error: str | None = None
    control_results: dict[str, ControlResult] = field(default_factory=dict)
    scenario_results: dict[str, str] = field(default_factory=dict)
    reverted_files: tuple[str, ...] = field(default_factory=tuple)
    error_path: str | None = None
    candidate: Candidate | None = None
    evidence: dict[str, str] = field(default_factory=dict)


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
