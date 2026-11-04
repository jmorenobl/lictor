"""Ports (Protocols) of the application layer in hexagonal architecture."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from lictor.domain.model import (
    AgentResult,
    AgentTask,
    Candidate,
    CommandOutput,
    ControlResult,
    Event,
    Health,
    JournalDamage,
    RepoPin,
    Scenario,
    TreeChange,
)


@runtime_checkable
class Executor(Protocol):
    """Abstraction for calling an AI agent executor."""

    def health(self) -> Health:
        """Check CLI availability and authentication status."""
        ...

    def run(self, task: AgentTask) -> AgentResult:
        """Execute an agent task and return the result."""
        ...


@runtime_checkable
class EventLog(Protocol):
    """Abstraction for appending and retrieving CloudEvents journal entries."""

    def append(self, event: Event) -> None:
        """Append an event to the journal."""
        ...

    def events(self, run_id: str | None = None) -> list[Event]:
        """Retrieve all recorded events, optionally filtered by run ID."""
        ...

    def damage(self) -> JournalDamage:
        """Count the lines that reading the journal has to skip."""
        ...


@runtime_checkable
class Clock(Protocol):
    """Abstraction for time and run identifier generation."""

    def now(self) -> datetime:
        """Return current timezone-aware datetime."""
        ...

    def now_iso(self) -> str:
        """Return current time formatted as ISO 8601 UTC string."""
        ...

    def generate_run_id(self) -> str:
        """Generate a run identifier formatted as YYYYMMDD-HHMMSS-<4 hex>."""
        ...


@runtime_checkable
class Vcs(Protocol):
    """Abstraction for Version Control System operations."""

    def status(self) -> list[str]:
        """Return modified or untracked file paths."""
        ...

    def capture(self, workdir: Path, skip: Sequence[str] = ()) -> str:
        """Return the tree of HEAD with what `workdir` holds, leaving the paths in `skip` alone."""
        ...

    def changes(self, tree: str) -> list[TreeChange]:
        """Return every path of `tree` that differs from HEAD, deleted ones included."""
        ...

    def added_lines(self, tree: str) -> list[str]:
        """Return the lines that `tree` adds over HEAD."""
        ...

    def commit_tree(
        self,
        tree: str,
        message: str,
        trailers: Mapping[str, str | Sequence[str]] | None = None,
        *,
        parent: str | None = None,
        empty: bool = False,
    ) -> Candidate:
        """Build a commit of `tree` on `parent`, or HEAD, leaving the branch alone.

        A commit that changes nothing over its parent is refused unless `empty` says it registers.
        """
        ...

    def export_tree(self, rev: str, dest: Path) -> None:
        """Materialize a commit in a clean clone at `dest`, with nothing ignored or untracked."""
        ...

    def export_copy(self, rev: str, dest: Path) -> None:
        """Materialize a commit at `dest` as plain files, with no repository around them."""
        ...

    def advance_head(self, sha: str, expected: str) -> None:
        """Fast-forward from `expected` to `sha`, refusing to overwrite what the user changed."""
        ...

    def pin(self) -> RepoPin:
        """Record HEAD and the repository's control files before untrusted code runs."""
        ...

    def restore(self, pin: RepoPin) -> list[str]:
        """Bring back what changed since `pin`, control files first, and say what it was."""
        ...

    def fingerprint(self) -> str:
        """Hash the repository's control files without running git, which would obey them."""
        ...

    def get_tree(self, rev: str) -> str:
        """Return the tree hash of a commit."""
        ...

    def get_trailers(self, key: str, commit_ref: str | None = None) -> list[str]:
        """Get trailer values for the given key from git log, optionally limited to a commit."""
        ...

    def trailer_commits(self, key: str) -> list[tuple[str, list[str]]]:
        """Return each commit with trailer `key`, newest first, with all its values."""
        ...

    def list_files(self, rev: str, directory: str) -> list[str]:
        """Return the paths of the files under `directory` in the tree of `rev`."""
        ...

    def read_file(self, rev: str, path: str) -> str:
        """Return the content of a file in the tree of `rev`."""
        ...

    def parent_of(self, rev: str) -> str:
        """Return the first parent of a commit, or "" for a root commit."""
        ...

    def is_executable(self, rev: str, path: str) -> bool:
        """Tell whether `path` is an executable file in the tree of `rev`."""
        ...

    def committer_identity(self) -> str:
        """Return who `commit_tree` would record as committer, or say why git refuses.

        Raises `EnvironmentUnavailableError` with git's own message when there is none.
        """
        ...

    def get_head_sha(self) -> str:
        """Return the commit sha of HEAD."""
        ...

    def get_head_message(self) -> str:
        """Return the subject line of HEAD commit."""
        ...


@runtime_checkable
class CommandRunner(Protocol):
    """Abstraction for running system commands without shell."""

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutput:
        """Execute a command synchronously; `env` replaces what it would inherit, if given."""
        ...


@runtime_checkable
class ResultReader(Protocol):
    """Abstraction for translating command output into a ControlResult."""

    def read(
        self,
        output: str,
        exit_code: int,
        duration_ms: int,
        empty_exit_codes: Collection[int] = (),
    ) -> ControlResult:
        """Parse raw output or report files into a ControlResult.

        A run with no test cases passes only when its exit code is one of `empty_exit_codes`.
        """
        ...


@runtime_checkable
class SpecSource(Protocol):
    """Abstraction for discovering, parsing, and sealing Gherkin specification scenarios."""

    def load(self, features_dir: Path) -> list[Scenario]:
        """Load and validate all scenarios from the given directory."""
        ...

    def spec_hashes(self, text: str) -> dict[str, str]:
        """Hash each tagged scenario of a feature's text; what can't be read is left out."""
        ...

    def verify_seal(self, project_root: Path, driver_paths: Sequence[str]) -> bool:
        """Verify that current features and driver files match .lictor/seal.json."""
        ...

    def scenario_text(self, project_root: Path, scenario: Scenario) -> str:
        """The scenario's own text with its backgrounds; only its header if it can't be found."""
        ...

    def write_seal(
        self,
        project_root: Path,
        run_id: str,
        driver_paths: Sequence[str],
        source_root: Path | None = None,
    ) -> dict[str, Any]:
        """Hash `source_root`, or the project, and write the seal to .lictor/seal.json."""
        ...


@dataclass(frozen=True)
class AuditSink:
    """Audit sink grouping EventLog and Clock dependencies."""

    event_log: EventLog
    clock: Clock


@dataclass(frozen=True)
class ProfileInitConfig:
    """Configuration payload and gitignore entries for project initialization."""

    profile_name: str
    gitignore_entries: Sequence[str]
    config_json: str


@runtime_checkable
class ConfigInitializer(Protocol):
    """Abstraction for detecting profiles and generating project configuration."""

    def generate_config(
        self,
        project_root: Path,
        profile_name: str | None = None,
        model: str | None = None,
    ) -> ProfileInitConfig:
        """Generate project configuration JSON and gitignore entries from profile."""
        ...
