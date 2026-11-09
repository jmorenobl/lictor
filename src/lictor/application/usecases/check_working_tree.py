"""What `lictor check` verifies: the project as a run would, in a clean clone of a commit.

With nothing to commit it is HEAD. Otherwise the working tree is read over HEAD, as the agent's
copy is, into a commit outside the branch, so what is verified is exactly what committing now
would record. The environment is built for that same commit, apart from any run's.
"""

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor.application.ports import ResultReader, Vcs
from lictor.application.run_files import verification_tree
from lictor.application.usecases.check_project import (
    CheckAcceptanceSpec,
    CheckControlSpec,
    CheckProject,
    CheckProjectRequest,
    CheckProjectResult,
)
from lictor.application.verification_env import EnvironmentRunner, VerificationEnvironment
from lictor.domain.errors import PreconditionError
from lictor.domain.rules import NO_COMMITS

CANDIDATE_MESSAGE = "chore(lictor): árbol de trabajo verificado por lictor check"


@dataclass(frozen=True)
class CheckWorkingTreeRequest:
    """What to check; `rev` verifies that commit instead of the working tree."""

    run_id: str
    controls: Sequence[CheckControlSpec]
    acceptance: CheckAcceptanceSpec | None = None
    levels: Sequence[str] | None = None
    control_ids: Sequence[str] | None = None
    keep_going: bool = False
    error_max_chars: int = 4000
    rev: str | None = None


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
        readers: Mapping[str, ResultReader],
        project_root: Path,
        evidence_dir: Path,
    ) -> None:
        self.vcs = vcs
        self.environment = environment
        self.readers = readers
        self.project_root = project_root
        self.evidence_dir = evidence_dir

    def execute(self, req: CheckWorkingTreeRequest) -> CheckWorkingTreeResult:
        """Check; raise `EnvironmentUnavailableError` if git can't commit or nothing builds.

        Raise `PreconditionError` if the repository has no commit: there is nothing to verify on.
        """
        subject = self._subject(req.rev)
        # Each check replaces what the previous one left.
        shutil.rmtree(self.evidence_dir, ignore_errors=True)
        self.evidence_dir.mkdir(parents=True)
        with self.environment.throwaway(subject.rev, self.evidence_dir) as environment:
            environment.ensure(req.run_id)
            check = CheckProject(EnvironmentRunner(environment), self.readers)
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
                    )
                )
            tampered = environment.ensure(req.run_id)
        return CheckWorkingTreeResult(subject.description, result, tampered)

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
