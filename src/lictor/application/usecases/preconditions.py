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

from lictor.application.ports import Executor, SpecSource, Vcs
from lictor.application.run_files import prepare_scratch
from lictor.application.skill import skill_stub_warning
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.errors import EnvironmentUnavailableError, InternalLictorError
from lictor.domain.model import PreconditionProblem
from lictor.domain.rules import NO_COMMITS, missing_executables
from lictor.domain.run_branches import uncommitted_left_out
from lictor.domain.stages import check_controls_warning

EnvironmentCheck = Literal["run", "throwaway", "none"]
UNCONFIGURED = "no comprobado: la configuración no carga"


@dataclass(frozen=True)
class PreconditionCheck:
    """One check, with what it found; `skipped` says why it wasn't made.

    `warnings` are worth telling but stop nothing: they don't count for the exit code.
    """

    name: str
    problems: tuple[PreconditionProblem, ...] = ()
    skipped: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreconditionsRequest:
    """What to check.

    `environment` decides how `verification.prepare` is proven: by building the run's own
    environment, by building one that is thrown away, or not at all, with `skip_reason`.
    """

    run_id: str
    clean_tree: bool = True
    # In the person's checkout what isn't committed only stays out of the run (ADR-0005).
    uncommitted_warns: bool = False
    environment: EnvironmentCheck = "run"
    log_dir: Path | None = None
    skip_reason: str = ""
    features: bool = False
    skill: bool = False
    # Why the configuration didn't load, for `doctor` to tell it beside everything else (F30).
    config_problem: str | None = None
    # `doctor` answers for `lictor check` too: it also looks for the `check` controls' programs,
    # and warns that a run doesn't run them. A run and a resume only need the `attempt` ones.
    check_stage: bool = False


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

    `commands` maps a label that completes "el comando …" to each command a run verifies with;
    `check_commands` maps the id of each control only `lictor check` runs to its command
    (ADR-0007).
    `aside` is the environment a throwaway is made from, so that its events stay out of the runs'
    journal; without it, the run's own is used. `spec_source` checks the features when asked.
    Without a configuration there is neither an executor nor an environment to build: what needs
    them is skipped, and the rest is checked all the same (F30).
    """

    vcs: Vcs
    executor: Executor | None
    environment: VerificationEnvironment | None
    project_root: Path
    commands: Mapping[str, str] = field(default_factory=dict)
    check_commands: Mapping[str, str] = field(default_factory=dict)
    aside: VerificationEnvironment | None = None
    spec_source: SpecSource | None = None

    def execute(self, req: PreconditionsRequest) -> PreconditionsReport:
        has_commits = self.vcs.has_commits()
        checks: list[PreconditionCheck] = []
        if req.config_problem is not None:
            problem = PreconditionProblem("config", req.config_problem)
            checks.append(PreconditionCheck("Configuración", (problem,)))
        checks.append(self._commits(has_commits))
        if req.clean_tree:
            checks.append(self._clean_tree(warn=req.uncommitted_warns))
        if req.features:
            checks.append(self._features())
        if req.skill and (warning := skill_stub_warning(self.project_root)):
            checks.append(PreconditionCheck("Skill de Lictor", warnings=(warning,)))
        if req.check_stage and self.check_commands:
            checks.append(self._check_stage())
        checks.append(self._identity())
        executor, cli_version = self._executor()
        checks.append(executor)
        scratch = self._scratch()
        checks.append(scratch)
        environment = self.environment
        if environment is None:
            checks.append(PreconditionCheck("Comandos de verificación", skipped=UNCONFIGURED))
            checks.append(PreconditionCheck("Entorno de verificación", skipped=UNCONFIGURED))
            return PreconditionsReport(tuple(checks), cli_version)
        checks.append(
            PreconditionCheck(
                "Comandos de verificación",
                tuple(
                    missing_executables(
                        self._commands(req),
                        lambda program: self._found(environment, program, has_commits),
                    )
                ),
            )
        )
        checks.append(
            self._environment(
                environment, req, checks, scratch_ok=not scratch.problems, has_commits=has_commits
            )
        )
        return PreconditionsReport(tuple(checks), cli_version)

    @staticmethod
    def _commits(has_commits: bool) -> PreconditionCheck:
        """Everything Lictor verifies is a commit, and its first one builds on HEAD."""
        if has_commits:
            return PreconditionCheck("Repositorio con commits")
        return PreconditionCheck(
            "Repositorio con commits", (PreconditionProblem("repository", NO_COMMITS),)
        )

    def _clean_tree(self, *, warn: bool) -> PreconditionCheck:
        changed = self.vcs.status()
        if not changed:
            return PreconditionCheck("Árbol git limpio")
        if warn:
            return PreconditionCheck("Árbol git limpio", warnings=(uncommitted_left_out(changed),))
        message = (
            f"El árbol de trabajo tiene cambios sin confirmar: {', '.join(changed)}. "
            "Confírmalos o descártalos antes de ejecutar."
        )
        return PreconditionCheck("Árbol git limpio", (PreconditionProblem("repository", message),))

    def _features(self) -> PreconditionCheck:
        """What `load_spec` would refuse, all of it, and what it would skip: the same rule."""
        if self.spec_source is None:
            raise InternalLictorError("Comprobar los escenarios necesita un SpecSource")
        problems = self.spec_source.problems(self.project_root / "features")
        return PreconditionCheck(
            "Escenarios",
            tuple(PreconditionProblem("config", str(p)) for p in problems if p.blocking),
            warnings=tuple(str(p) for p in problems if not p.blocking),
        )

    def _check_stage(self) -> PreconditionCheck:
        """Warn, without stopping anything, that a run leaves some controls to `lictor check`."""
        return PreconditionCheck(
            "Momento de los controles",
            warnings=(check_controls_warning(list(self.check_commands)),),
        )

    def _commands(self, req: PreconditionsRequest) -> dict[str, str]:
        """The commands whose program has to be found: a run's, or also `lictor check`'s."""
        if not req.check_stage:
            return dict(self.commands)
        checked = {f"del control '{cid}'": run for cid, run in self.check_commands.items()}
        return {**self.commands, **checked}

    def _identity(self) -> PreconditionCheck:
        try:
            self.vcs.committer_identity()
        except EnvironmentUnavailableError as err:
            return PreconditionCheck(
                "Identidad git", (PreconditionProblem("environment", str(err)),)
            )
        return PreconditionCheck("Identidad git")

    def _executor(self) -> tuple[PreconditionCheck, str | None]:
        if self.executor is None:
            return PreconditionCheck("Ejecutor", skipped=UNCONFIGURED), None
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

    def _found(self, environment: VerificationEnvironment, program: str, has_commits: bool) -> bool:
        """Resolve a program as the clone will: on the commands' PATH, or committed in HEAD.

        Without a HEAD a relative program can't be judged, and isn't: the missing commit already
        stops the command, and calling it missing would send the person to fix the machine.
        """
        if "/" not in program:
            return shutil.which(program, path=environment.search_path()) is not None
        if os.path.isabs(program):
            return os.path.isfile(program) and os.access(program, os.X_OK)
        return not has_commits or self.vcs.is_executable("HEAD", posixpath.normpath(program))

    def _environment(
        self,
        environment: VerificationEnvironment,
        req: PreconditionsRequest,
        earlier: list[PreconditionCheck],
        *,
        scratch_ok: bool,
        has_commits: bool,
    ) -> PreconditionCheck:
        name = "Entorno de verificación"
        if req.environment == "none":
            return PreconditionCheck(name, skipped=req.skip_reason)
        if not has_commits:
            return PreconditionCheck(name, skipped="no se construye: no hay ningún commit")
        if not scratch_ok:
            return PreconditionCheck(name, skipped="no se puede construir sin directorio temporal")
        # A run that is going to stop anyway doesn't download anything.
        if req.environment == "run" and any(check.problems for check in earlier):
            return PreconditionCheck(
                name, skipped="no se construye: antes hay que resolver lo anterior"
            )
        try:
            if req.environment == "run":
                environment.ensure(req.run_id)
            else:
                if req.log_dir is None:
                    raise InternalLictorError("Un entorno desechable necesita dónde dejar su log")
                source = self.aside or environment
                with source.throwaway("HEAD", req.log_dir) as env:
                    env.ensure(req.run_id)
        except EnvironmentUnavailableError as err:
            return PreconditionCheck(name, (PreconditionProblem("environment", str(err)),))
        return PreconditionCheck(name)
