"""What has to hold before a command spends anything: the same checks for `run` and `doctor`.

Each check says what it found, so `doctor` can show them all, and the domain decides the exit
code from the kind of each problem: the project's (10) or the machine's (40).
"""

import os
import posixpath
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from lictor.application.ports import Executor, Vcs
from lictor.application.run_files import prepare_scratch
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.errors import EnvironmentUnavailableError, InternalLictorError
from lictor.domain.model import PreconditionProblem
from lictor.domain.rules import missing_executables

EnvironmentCheck = Literal["run", "throwaway", "none"]


@dataclass(frozen=True)
class PreconditionCheck:
    """One check, with what it found; `skipped` says why it wasn't made."""

    name: str
    problems: tuple[PreconditionProblem, ...] = ()
    skipped: str | None = None


@dataclass(frozen=True)
class PreconditionsRequest:
    """What to check.

    `environment` decides how `verification.prepare` is proven: by building the run's own
    environment, by building one that is thrown away, or not at all, with `skip_reason`.
    """

    run_id: str
    clean_tree: bool = True
    environment: EnvironmentCheck = "run"
    log_dir: Path | None = None
    skip_reason: str = ""


@dataclass(frozen=True)
class PreconditionsReport:
    """Every check made, in order, and the version of the agent's CLI if it answered."""

    checks: tuple[PreconditionCheck, ...] = ()
    cli_version: str | None = None

    @property
    def problems(self) -> list[PreconditionProblem]:
        return [problem for check in self.checks for problem in check.problems]


@dataclass
class CheckPreconditions:
    """Check the repository, git, the agent, the temporary directory and the commands.

    `commands` maps a label that completes "el comando …" to each command verification runs.
    `aside` is the environment a throwaway is made from, so that its events stay out of the runs'
    journal; without it, the run's own is used.
    """

    vcs: Vcs
    executor: Executor
    environment: VerificationEnvironment
    project_root: Path
    commands: Mapping[str, str] = field(default_factory=dict)
    aside: VerificationEnvironment | None = None

    def execute(self, req: PreconditionsRequest) -> PreconditionsReport:
        checks: list[PreconditionCheck] = []
        if req.clean_tree:
            checks.append(self._clean_tree())
        checks.append(self._identity())
        executor, cli_version = self._executor()
        checks.append(executor)
        scratch = self._scratch()
        checks.append(scratch)
        checks.append(
            PreconditionCheck(
                "Comandos de verificación", tuple(missing_executables(self.commands, self._found))
            )
        )
        checks.append(self._environment(req, checks, scratch_ok=not scratch.problems))
        return PreconditionsReport(tuple(checks), cli_version)

    def _clean_tree(self) -> PreconditionCheck:
        changed = self.vcs.status()
        if not changed:
            return PreconditionCheck("Árbol git limpio")
        message = (
            f"El árbol de trabajo tiene cambios sin confirmar: {', '.join(changed)}. "
            "Confírmalos o descártalos antes de ejecutar."
        )
        return PreconditionCheck("Árbol git limpio", (PreconditionProblem("repository", message),))

    def _identity(self) -> PreconditionCheck:
        try:
            self.vcs.committer_identity()
        except EnvironmentUnavailableError as err:
            return PreconditionCheck(
                "Identidad git", (PreconditionProblem("environment", str(err)),)
            )
        return PreconditionCheck("Identidad git")

    def _executor(self) -> tuple[PreconditionCheck, str | None]:
        health = self.executor.health()
        if health.ok:
            return PreconditionCheck("Ejecutor"), health.cli_version
        message = f"el ejecutor no está disponible: {health.message or health.auth_status}"
        return PreconditionCheck("Ejecutor", (PreconditionProblem("environment", message),)), None

    def _scratch(self) -> PreconditionCheck:
        try:
            prepare_scratch(self.project_root)
        except EnvironmentUnavailableError as err:
            return PreconditionCheck(
                "Directorio temporal", (PreconditionProblem("environment", str(err)),)
            )
        return PreconditionCheck("Directorio temporal")

    def _found(self, program: str) -> bool:
        """Resolve a program as the clone will: on the commands' PATH, or committed in HEAD."""
        if "/" not in program:
            return shutil.which(program, path=self.environment.search_path()) is not None
        if os.path.isabs(program):
            return os.path.isfile(program) and os.access(program, os.X_OK)
        return self.vcs.is_executable("HEAD", posixpath.normpath(program))

    def _environment(
        self, req: PreconditionsRequest, earlier: list[PreconditionCheck], *, scratch_ok: bool
    ) -> PreconditionCheck:
        name = "Entorno de verificación"
        if req.environment == "none":
            return PreconditionCheck(name, skipped=req.skip_reason)
        if not scratch_ok:
            return PreconditionCheck(name, skipped="no se puede construir sin directorio temporal")
        # A run that is going to stop anyway doesn't download anything.
        if req.environment == "run" and any(check.problems for check in earlier):
            return PreconditionCheck(
                name, skipped="no se construye: antes hay que resolver lo anterior"
            )
        try:
            if req.environment == "run":
                self.environment.ensure(req.run_id)
            else:
                if req.log_dir is None:
                    raise InternalLictorError("Un entorno desechable necesita dónde dejar su log")
                source = self.aside or self.environment
                with source.throwaway("HEAD", req.log_dir) as env:
                    env.ensure(req.run_id)
        except EnvironmentUnavailableError as err:
            return PreconditionCheck(name, (PreconditionProblem("environment", str(err)),))
        return PreconditionCheck(name)
