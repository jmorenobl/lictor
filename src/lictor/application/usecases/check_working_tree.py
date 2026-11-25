"""What `lictor check` verifies: the project as a run would, in a clean clone of a commit.

With nothing to commit it is HEAD. Otherwise the working tree is read over HEAD, as the agent's
copy is, into a commit outside the branch, so what is verified is exactly what committing now
would record. The environment is built for that same commit, apart from any run's.
"""

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor.application.api_baseline import ApiBaseline, resolve_base
from lictor.application.ports import ReportReaders, Vcs, VerificationCommands
from lictor.application.run_files import CHECK_DIR, aside_dir, verification_tree
from lictor.application.usecases.check_project import (
    CheckAcceptanceSpec,
    CheckProject,
    CheckProjectRequest,
    CheckProjectResult,
)
from lictor.application.verification_env import EnvironmentRunner, VerificationEnvironment
from lictor.domain.api import ApiBase, UnresolvedBase
from lictor.domain.controls import Control
from lictor.domain.errors import PreconditionError
from lictor.domain.model import CommandOutput
from lictor.domain.rules import NO_COMMITS

CANDIDATE_MESSAGE = "chore(lictor): árbol de trabajo verificado por lictor check"


@dataclass(frozen=True)
class CheckWorkingTreeRequest:
    """What to check; `rev` verifies that commit instead of the working tree."""

    run_id: str
    controls: Sequence[Control]
    acceptance: CheckAcceptanceSpec | None = None
    levels: Sequence[str] | None = None
    control_ids: Sequence[str] | None = None
    keep_going: bool = False
    error_max_chars: int = 4000
    rev: str | None = None
    # A commit to compare the public API with (ADR-0017); without one it only gives facts.
    api_base: str | None = None


@dataclass(frozen=True)
class CheckWorkingTreeResult:
    """What was verified, how it went, and whether the environment changed under the commands."""

    subject: str
    check: CheckProjectResult
    tampered: str | None = None


@dataclass(frozen=True)
class _Subject:
    rev: str
    description: str
    deleted: tuple[str, ...] = ()


class CheckWorkingTree:
    """Verify the working tree, or a commit, in a clone and in a verification environment."""

    def __init__(
        self,
        vcs: Vcs,
        environment: VerificationEnvironment,
        readers: ReportReaders,
        project_root: Path,
        api: ApiBaseline | None = None,
    ) -> None:
        self.vcs = vcs
        self.environment = environment
        self.readers = readers
        self.project_root = project_root
        self.evidence_dir = aside_dir(project_root, CHECK_DIR)
        self.api = api

    def execute(self, req: CheckWorkingTreeRequest) -> CheckWorkingTreeResult:
        """Check; raise `EnvironmentUnavailableError` if git can't commit or nothing builds.

        Raise `PreconditionError` if the repository has no commit: there is nothing to verify on.
        """
        subject = self._subject(req.rev)
        base = self._base(req, subject.rev)
        # Each check replaces what the previous one left.
        shutil.rmtree(self.evidence_dir, ignore_errors=True)
        self.evidence_dir.mkdir(parents=True)
        with self.environment.throwaway(subject.rev, self.evidence_dir) as environment:
            environment.ensure(req.run_id)
            check = CheckProject(EnvironmentRunner(environment), self.readers)
            api_base = self._api_base(base, environment)
            with verification_tree(self.vcs, subject.rev, self.project_root) as tree:
                result = check.execute(
                    CheckProjectRequest(
                        project_root=self.project_root,
                        controls=req.controls,
                        acceptance=req.acceptance,
                        levels=req.levels,
                        error_max_chars=req.error_max_chars,
                        tree=tree,
                        evidence_dir=self.evidence_dir,
                        keep_going=req.keep_going,
                        control_ids=req.control_ids,
                        deleted=subject.deleted,
                        installed_plugins=environment.installed_plugins,
                        api_base=api_base,
                    )
                )
            tampered = environment.ensure(req.run_id)
        return CheckWorkingTreeResult(subject.description, result, tampered)

    def _base(self, req: CheckWorkingTreeRequest, rev: str) -> str | None:
        """Where what is checked left `--base`, or `api.base` as a run compares (H4 of 0.20.0).

        Raise `PreconditionError` if that names no commit, or none it shares here: before any
        environment is built for nothing.
        """
        if self.api is None or (ref := req.api_base or self.api.ref) is None:
            return None
        found = resolve_base(self.vcs, rev, ref)
        if isinstance(found, UnresolvedBase):
            raise PreconditionError(found.message)
        return found

    def _api_base(self, sha: str | None, environment: VerificationCommands) -> ApiBase | None:
        """That base, measured in a clean clone of it like what is checked."""
        if sha is None or self.api is None:
            return None

        def run_in(
            command: Sequence[str], tree: Path, timeout_s: int
        ) -> tuple[CommandOutput, tuple[str, ...]]:
            # `check` keeps no snapshot: nothing it runs is trusted to leave the state alone.
            return environment.run(command, tree, timeout_s), ()

        return self.api.base(sha, self.vcs, self.project_root, run_in, self.evidence_dir)

    def _subject(self, rev: str | None) -> _Subject:
        if rev is not None:
            return _Subject(rev, f"commit {rev[:12]}")
        if not self.vcs.has_commits():
            raise PreconditionError(NO_COMMITS)
        head = self.vcs.get_head_sha()
        tree = self.vcs.capture(self.project_root, skip=[".lictor"])
        if tree == self.vcs.get_tree(head):
            return _Subject(head, f"HEAD {head[:12]}")
        # Only a candidate needs an identity: it is a commit, even outside the branch.
        self.vcs.committer_identity()
        candidate = self.vcs.commit_tree(tree, CANDIDATE_MESSAGE, parent=head)
        changes = self.vcs.changes(tree)
        return _Subject(
            candidate.sha,
            f"árbol de trabajo ({len(changes)} ficheros sin confirmar) sobre HEAD {head[:12]}",
            tuple(change.path for change in changes if change.is_deleted),
        )
