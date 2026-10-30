"""Ports (Protocols) of the application layer in hexagonal architecture."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from lictor.domain.model import (
    AgentResult,
    AgentTask,
    CommandOutput,
    ControlResult,
    Event,
    Health,
    Scenario,
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

    def reset_to_head(self) -> None:
        """Discard all uncommitted changes and clean untracked files."""
        ...

    def commit(
        self,
        message: str,
        trailers: dict[str, str] | None = None,
        paths: Sequence[str] | None = None,
    ) -> str:
        """Create a commit with optional git trailers and return commit sha."""
        ...

    def get_trailers(self, key: str, commit_ref: str | None = None) -> list[str]:
        """Get trailer values for the given key from git log, optionally limited to a commit."""
        ...

    def get_head_sha(self) -> str:
        """Return the commit sha of HEAD."""
        ...

    def get_head_message(self) -> str:
        """Return the subject line of HEAD commit."""
        ...

    def revert_paths(self, paths: Sequence[str]) -> None:
        """Selectively revert modified or untracked file paths."""
        ...

    def diff(self) -> str:
        """Return working tree diff."""
        ...


@runtime_checkable
class CommandRunner(Protocol):
    """Abstraction for running system commands without shell."""

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandOutput:
        """Execute a command synchronously."""
        ...


@runtime_checkable
class ResultReader(Protocol):
    """Abstraction for translating command output into a ControlResult."""

    def read(
        self,
        output: str,
        exit_code: int,
        duration_ms: int,
        allow_empty: bool = False,
    ) -> ControlResult:
        """Parse raw output or report files into a ControlResult."""
        ...


@runtime_checkable
class SpecSource(Protocol):
    """Abstraction for discovering, parsing, and sealing Gherkin specification scenarios."""

    def load(self, features_dir: Path) -> list[Scenario]:
        """Load and validate all scenarios from the given directory."""
        ...

    def verify_seal(self, project_root: Path, driver_paths: Sequence[str]) -> bool:
        """Verify that current features and driver files match .lictor/seal.json."""
        ...

    def write_seal(
        self,
        project_root: Path,
        run_id: str,
        driver_paths: Sequence[str],
    ) -> dict[str, Any]:
        """Compute and write seal to .lictor/seal.json."""
        ...


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
