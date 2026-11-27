"""What has to hold before a command spends anything: the same checks for `run` and `doctor`.

Each check says what it found, so `doctor` can show them all, and the domain decides the exit
code from the kind of each problem: the project's (10) or the machine's (40).
"""

import os
import posixpath
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from lictor.application.api_baseline import ApiBaseline, resolve_base
from lictor.application.conventions import rule_files_warnings
from lictor.application.ports import Executor, ReportReaders, SpecSource, Vcs
from lictor.application.run_files import (
    prepare_scratch,
    relative_to_project,
    verification_tree,
)
from lictor.application.skill import skill_stub_warning
from lictor.application.start_survey import StartSurvey
from lictor.application.usecases.check_project import CheckProject, CheckProjectRequest
from lictor.application.usecases.verify_attempt import IntegritySnapshot
from lictor.application.verification_env import EnvironmentRunner, VerificationEnvironment
from lictor.domain.api import UnresolvedBase, compare_api
from lictor.domain.blind_spots import (
    BlindSpots,
    StartRules,
    blind_spots,
    configuration_problems,
    metadata_facts,
    missing_plugins,
    program_problems,
)
from lictor.domain.controls import Control, command_controls, unrunnable_by_agent
from lictor.domain.conventions import CONVENTIONS_CHECK
from lictor.domain.errors import EnvironmentUnavailableError, InternalLictorError
from lictor.domain.head_controls import (
    blocking_on_head,
    head_api_broken,
    head_control_problem,
    head_not_started,
    head_tampered,
)
from lictor.domain.model import CommandOutput, PreconditionProblem
from lictor.domain.proof_level import (
    ProofDefences,
    ProofLevel,
    level_name,
    proof_level,
    warning_lines,
)
from lictor.domain.reports import ApiComparison, ApiReport, InvalidReport
from lictor.domain.rules import (
    CHECK_STAGE_ONLY,
    NO_COMMITS,
    control_label,
    missing_executables,
)
from lictor.domain.run_branches import uncommitted_left_out
from lictor.domain.stages import check_controls_warning

EnvironmentCheck = Literal["run", "throwaway", "none"]
UNCONFIGURED = "no comprobado: la configuración no carga"
DEPENDENCIES = "Dependencias de la verificación"
BLIND_SPOTS = "Lo que los controles no ven"
HEAD_CONTROLS = "Los controles pasan en HEAD"
API_BASE = "La API frente a su base"
NONE_STARTS = (
    "no comprobado: no arranca ningún control que bloquea, como dice «Comandos de verificación»"
)
NO_SURVEY = "no comprobado: el perfil no declara cómo leerlo"
NO_NEEDS = "no comprobado: el perfil no declara qué necesita"
UNPREPARED = "sin prepare: no se construye ni se vigila"
PROOF_LEVEL = "Nivel de prueba"
AGENT_COMMANDS = "Órdenes del agente"


@dataclass(frozen=True)
class PreconditionCheck:
    """One check, with what it found; `skipped` says why it wasn't made.

    `warnings` are worth telling but stop nothing: they don't count for the exit code.
    """

    name: str
    problems: tuple[PreconditionProblem, ...] = ()
    skipped: str | None = None
    warnings: tuple[str, ...] = ()
    # What a check that passed has to tell: `✓ <name>: <detail>`.
    detail: str | None = None


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
    # Where the survey of the start contract leaves its evidence (ADR-0015); without it, what
    # the project needs and what its controls don't see aren't checked.
    survey_dir: Path | None = None
    # `doctor` tells the rule files whose content the person hasn't reviewed (F79).
    conventions: bool = False


@dataclass(frozen=True)
class PreconditionsReport:
    """Every check made, in order, and the version of the agent's CLI if it answered."""

    checks: tuple[PreconditionCheck, ...] = ()
    cli_version: str | None = None
    blind_spots: BlindSpots | None = None
    # HEAD against `api.base` at the start (H4 of 0.20.0): what was already broken, so a run only
    # warns of what it breaks.
    api_start: ApiComparison | None = None
    # What a green of the run can be trusted for (ADR-0020), with the plugins the environment
    # loads when it was built.
    proof: ProofLevel | None = None

    @property
    def problems(self) -> list[PreconditionProblem]:
        return [problem for check in self.checks for problem in check.problems]

    @property
    def dependency_warnings(self) -> tuple[str, ...]:
        """What «Dependencias de la verificación» warned of, which a new run tells too."""
        return tuple(w for c in self.checks if c.name == DEPENDENCIES for w in c.warnings)


# What checking on the environment just built gives: its checks, the blind spots, and HEAD's API
# against its base.
type _Contract = tuple[
    list[PreconditionCheck], BlindSpots | None, ApiComparison | None, ProofLevel | None
]


@dataclass
class CheckPreconditions:
    """Check the repository, git, the agent, the temporary directory and the commands.

    `commands` maps a label that completes "el comando …" to each command a run verifies with;
    `check_stage_ids` are the controls only `lictor check` runs (ADR-0007), which `doctor` warns
    about; `check_stage_commands` maps those that run a command of their own to it, whose
    program it looks for: a reader runs nothing (ADR-0008).
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
    check_stage_ids: Sequence[str] = ()
    check_stage_commands: Mapping[str, str] = field(default_factory=dict)
    aside: VerificationEnvironment | None = None
    spec_source: SpecSource | None = None
    # The start contract (ADR-0015): what the acceptance needs, and how to read what is seen.
    needs_plugins: Mapping[str, str] = field(default_factory=dict)
    survey: StartSurvey | None = None
    start_rules: StartRules | None = None
    # The controls a run verifies with, and how to read them: those that block must pass on HEAD
    # before any agent works, or the first attempt pays for what it can't fix.
    head_controls: Sequence[Control] = ()
    readers: ReportReaders | None = None
    # How the base of the public API is measured, and `api.base` (H4 of 0.20.0).
    api: ApiBaseline | None = None
    # The profile's rule files, and the hash of each one the person reviewed (F79).
    rule_files: Sequence[str] = ()
    reviewed: Mapping[str, str] = field(default_factory=dict)
    # The level of proof the defences give (ADR-0020), whether the profile or the project builds
    # the environment, and what the agent may run, to warn of what it couldn't check (D10).
    proof: ProofDefences | None = None
    env_prepared: bool = True
    acceptance_run: str = ""
    agent_tools: Sequence[str] = ()

    def execute(self, req: PreconditionsRequest) -> PreconditionsReport:
        has_commits = self.vcs.has_commits()
        checks = self._project_checks(req, has_commits)
        checks.append(self._identity())
        executor, cli_version = self._executor()
        checks.append(executor)
        scratch = self._scratch()
        checks.append(scratch)
        environment = self.environment
        if environment is None:
            checks.append(PreconditionCheck("Comandos de verificación", skipped=UNCONFIGURED))
            checks.append(PreconditionCheck("Entorno de verificación", skipped=UNCONFIGURED))
            checks.extend(self._unchecked(req, UNCONFIGURED))
            return PreconditionsReport(tuple(checks), cli_version)
        checks.append(
            self._commands(req, lambda program: self._found(environment, program, has_commits))
        )
        built, spots, api_start, proof = self._environment(
            environment, req, checks, scratch_ok=not scratch.problems, has_commits=has_commits
        )
        checks.extend(built)
        return PreconditionsReport(tuple(checks), cli_version, spots, api_start, proof)

    def _project_checks(
        self, req: PreconditionsRequest, has_commits: bool
    ) -> list[PreconditionCheck]:
        """What is the project's own, before the machine: configuration, repository, rules."""
        checks: list[PreconditionCheck] = []
        if req.config_problem is not None:
            problem = PreconditionProblem("config", req.config_problem)
            checks.append(PreconditionCheck("Configuración", (problem,)))
        checks.append(self._commits(has_commits))
        if req.clean_tree:
            checks.append(self._clean_tree(warn=req.uncommitted_warns))
        if req.features:
            checks.append(self._features())
        return [*checks, *self._advice(req, has_commits)]

    def _advice(self, req: PreconditionsRequest, has_commits: bool) -> list[PreconditionCheck]:
        """What `doctor` warns of and never stops for: the skill, the rules, the `check` stage."""
        checks: list[PreconditionCheck] = []
        if req.skill and (warning := skill_stub_warning(self.project_root)):
            checks.append(PreconditionCheck("Skill de Lictor", warnings=(warning,)))
        if req.conventions and has_commits and (rules := self._conventions()) is not None:
            checks.append(rules)
        if req.check_stage and self.check_stage_ids:
            checks.append(self._check_stage())
        return checks

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

    def _conventions(self) -> PreconditionCheck | None:
        """The rule files of HEAD not reviewed as they are now; nothing without any (F79)."""
        warnings = rule_files_warnings(self.vcs, self.rule_files, self.reviewed)
        return None if warnings is None else PreconditionCheck(CONVENTIONS_CHECK, warnings=warnings)

    def _check_stage(self) -> PreconditionCheck:
        """Warn, without stopping anything, that a run leaves some controls to `lictor check`."""
        return PreconditionCheck(
            "Momento de los controles",
            warnings=(check_controls_warning(list(self.check_stage_ids)),),
        )

    def _commands(
        self, req: PreconditionsRequest, found: Callable[[str], bool]
    ) -> PreconditionCheck:
        """Whether the program of each command a run verifies with can be found.

        `doctor` also looks for the `check` controls' (ADR-0007), but a missing one is a warning:
        a run starts without it, and only `lictor check` would fail. An empty command is still the
        project's mistake, whichever stage it belongs to.
        """
        problems = missing_executables(self.commands, found)
        if not req.check_stage:
            return PreconditionCheck("Comandos de verificación", tuple(problems))
        later = missing_executables(
            {control_label(cid): run for cid, run in self.check_stage_commands.items()}, found
        )
        problems += [p for p in later if p.kind == "config"]
        return PreconditionCheck(
            "Comandos de verificación",
            tuple(problems),
            warnings=tuple(f"{p.message} {CHECK_STAGE_ONLY}" for p in later if p.kind != "config"),
        )

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
    ) -> _Contract:
        """Build the environment and, with it still there, check the start contract on it."""
        name = "Entorno de verificación"
        skipped = self._why_not_built(req, earlier, scratch_ok=scratch_ok, has_commits=has_commits)
        if skipped is not None:
            checks, proof = self._level(req, None)
            return (
                [PreconditionCheck(name, skipped=skipped), *self._unchecked(req, skipped), *checks],
                None,
                None,
                proof,
            )
        try:
            if req.environment == "run":
                environment.ensure(req.run_id)
                return self._built(environment, req)
            if req.log_dir is None:
                raise InternalLictorError("Un entorno desechable necesita dónde dejar su log")
            source = self.aside or environment
            with source.throwaway("HEAD", req.log_dir) as env:
                env.ensure(req.run_id)
                return self._built(env, req)
        except EnvironmentUnavailableError as err:
            failed = PreconditionCheck(name, (PreconditionProblem("environment", str(err)),))
            unchecked = self._unchecked(req, "no comprobado: el entorno no se pudo construir")
            checks, proof = self._level(req, None)
            return [failed, *unchecked, *checks], None, None, proof

    @staticmethod
    def _why_not_built(
        req: PreconditionsRequest,
        earlier: list[PreconditionCheck],
        *,
        scratch_ok: bool,
        has_commits: bool,
    ) -> str | None:
        """Why the environment isn't built, or None when it is to be."""
        if req.environment == "none":
            return req.skip_reason
        if not has_commits:
            return "no se construye: no hay ningún commit"
        if not scratch_ok:
            return "no se puede construir sin directorio temporal"
        # A run that is going to stop anyway doesn't download anything.
        if req.environment == "run" and any(check.problems for check in earlier):
            return "no se construye: antes hay que resolver lo anterior"
        return None

    def _unchecked(self, req: PreconditionsRequest, reason: str) -> list[PreconditionCheck]:
        if req.survey_dir is None:
            return []
        return [
            PreconditionCheck(DEPENDENCIES, skipped=reason),
            PreconditionCheck(BLIND_SPOTS, skipped=reason),
            PreconditionCheck(HEAD_CONTROLS, skipped=reason),
            *([PreconditionCheck(API_BASE, skipped=reason)] if self._compares_api() else []),
        ]

    def _compares_api(self) -> bool:
        """Whether there is an API control and an `api.base` to compare HEAD with."""
        return self.api is not None and self.api.ref is not None

    def _built(self, environment: VerificationEnvironment, req: PreconditionsRequest) -> _Contract:
        """The start contract on the environment just built, its controls on HEAD, and HEAD's API
        against its base."""
        checks, spots = self._start_contract(environment, req)
        if req.survey_dir is None:
            return checks, spots, None, None
        checks.append(self._head_controls(environment, req, req.survey_dir))
        api_start = None
        if self.api is not None and self.api.ref is not None:
            api_check, api_start = self._api_against_base(
                environment, req.survey_dir, self.api, self.api.ref
            )
            checks.append(api_check)
        level, proof = self._level(req, environment.installed_plugins)
        return [*checks, *level], spots, api_start, proof

    def _level(
        self, req: PreconditionsRequest, installed: Sequence[str] | None
    ) -> tuple[list[PreconditionCheck], ProofLevel | None]:
        """What a green of this run can be trusted for, and what the agent can't check (D10).

        Facts, never an error (D3): the level is told in every report too. Without the
        environment the controls that need a plugin can't be judged, and that is said.
        """
        if req.survey_dir is None or self.proof is None:
            return [], None
        proof = proof_level(self.proof, installed)
        warned = warning_lines(proof)
        name = level_name(proof.level)
        level = (
            PreconditionCheck(PROOF_LEVEL, warnings=(name, *warned))
            if warned
            else PreconditionCheck(PROOF_LEVEL, detail=name)
        )
        checks = [level]
        unrunnable = unrunnable_by_agent(
            self.head_controls, self.acceptance_run, self.agent_tools, installed or ()
        )
        if unrunnable:
            checks.append(PreconditionCheck(AGENT_COMMANDS, warnings=unrunnable))
        return checks, proof

    def _api_against_base(
        self,
        environment: VerificationEnvironment,
        survey_dir: Path,
        api: ApiBaseline,
        ref: str,
    ) -> tuple[PreconditionCheck, ApiComparison | None]:
        """HEAD's public API against where it left `api.base` (H4 of 0.20.0).

        A break of HEAD that the person hasn't accepted fails every attempt when the control
        blocks, whatever the agent does; when it measures, the run only warns of new ones.
        """
        head = self.vcs.get_head_sha()
        found = resolve_base(self.vcs, head, ref)
        if isinstance(found, UnresolvedBase):
            unresolved = PreconditionProblem("config", found.message)
            return PreconditionCheck(API_BASE, (unresolved,)), None
        evidence = survey_dir / "api"

        def run_in(
            command: Sequence[str], tree: Path, timeout_s: int
        ) -> tuple[CommandOutput, tuple[str, ...]]:
            snapshot = IntegritySnapshot(self.project_root / ".lictor", self.project_root, self.vcs)
            output = environment.run(command, tree, timeout_s)
            snapshot.verify_and_restore()
            return output, tuple(snapshot.changes)

        base = api.base(found, self.vcs, self.project_root, run_in, evidence / "base")
        measured = api.base(head, self.vcs, self.project_root, run_in, evidence / "head").found
        report = measured if isinstance(measured, ApiReport) else InvalidReport(measured)
        compared = compare_api(report, base)
        blocks = any(c.report == "api" and c.mode == "block" for c in self.head_controls)
        if not isinstance(compared, ApiComparison):
            return _api_check(f"no se pudo medir la API de HEAD: {measured}", blocks=blocks), None
        if compared.problem is not None:
            return _api_check(compared.problem, blocks=blocks), compared
        if compared.broken:
            message = head_api_broken(compared, ref, blocks=blocks)
            return _api_check(message, blocks=blocks), compared
        return PreconditionCheck(API_BASE), compared

    def _blocking_on_head(
        self, environment: VerificationEnvironment
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The blocking controls to run on HEAD, and those left out because they can't start.

        With `api.base`, HEAD's API against its base is its own check, measured once there.
        """
        cannot_start = {
            control.id
            for control in command_controls(self.head_controls)
            if missing_executables(
                {control.id: control.run},
                lambda program: self._found(environment, program, has_commits=True),
            )
        }
        blocking, left_out = blocking_on_head(self.head_controls, cannot_start)
        if self._compares_api():
            blocking = tuple(cid for cid in blocking if not _is_api(cid, self.head_controls))
        return blocking, left_out

    def _head_controls(
        self, environment: VerificationEnvironment, req: PreconditionsRequest, survey_dir: Path
    ) -> PreconditionCheck:
        """Run on a clean clone of HEAD the controls that block, as the first attempt would.

        What fails there is the project's before any agent touches it: a path its tests need, a
        warning made an error, its own metadata missing, an unformatted base. Project code runs,
        so the state is guarded as in an attempt, and the environment is checked afterwards.
        """
        blocking, left_out = self._blocking_on_head(environment)
        notes = (head_not_started(left_out),) if left_out else ()
        if not blocking or self.readers is None:
            skipped = NONE_STARTS if notes else "ningún control bloquea"
            return PreconditionCheck(HEAD_CONTROLS, skipped=skipped)
        evidence = survey_dir / "controles"
        shutil.rmtree(evidence, ignore_errors=True)
        evidence.mkdir(parents=True)
        snapshot = IntegritySnapshot(self.project_root / ".lictor", self.project_root, self.vcs)
        with verification_tree(self.vcs, "HEAD", self.project_root) as tree:
            result = CheckProject(EnvironmentRunner(environment), self.readers).execute(
                CheckProjectRequest(
                    project_root=self.project_root,
                    controls=self.head_controls,
                    control_ids=blocking,
                    tree=tree,
                    evidence_dir=evidence,
                    keep_going=True,
                    installed_plugins=environment.installed_plugins,
                )
            )
        snapshot.verify_and_restore()
        shown = relative_to_project(evidence, self.project_root)
        problems = [
            PreconditionProblem(
                "config", head_control_problem(failure.id, failure.condensed(), f"{shown}/")
            )
            for failure in result.blocked
        ]
        if snapshot.changes:
            problems.append(PreconditionProblem("config", head_tampered(snapshot.changes)))
        if tampered := environment.ensure(req.run_id):
            problems.append(PreconditionProblem("environment", tampered))
        return PreconditionCheck(HEAD_CONTROLS, tuple(problems), warnings=notes)

    def _start_contract(
        self, environment: VerificationEnvironment, req: PreconditionsRequest
    ) -> tuple[list[PreconditionCheck], BlindSpots | None]:
        """The environment's check, and what the project needs and its controls don't see."""
        detail = None if self.env_prepared else UNPREPARED
        checks = [PreconditionCheck("Entorno de verificación", detail=detail)]
        if req.survey_dir is None:
            return checks, None
        plugins = missing_plugins(self.needs_plugins, environment.installed_plugins)
        if self.survey is None or self.start_rules is None:
            dependencies = (
                PreconditionCheck(DEPENDENCIES, tuple(plugins))
                if self.needs_plugins
                else PreconditionCheck(DEPENDENCIES, skipped=NO_NEEDS)
            )
            # Kept, so the report says why nothing was looked at, as `doctor` does (ADR-0020).
            unread = BlindSpots(skipped=NO_SURVEY)
            return [
                *checks,
                dependencies,
                PreconditionCheck(BLIND_SPOTS, skipped=NO_SURVEY),
            ], unread
        surveyed = self.survey.execute(environment, self.vcs, self.project_root, req.survey_dir)
        if isinstance(surveyed, str):
            reason = f"no se pudo leer lo que ve cada control: {surveyed}"
            dependencies = PreconditionCheck(DEPENDENCIES, tuple(plugins), warnings=(reason,))
            return [*checks, dependencies, PreconditionCheck(BLIND_SPOTS, warnings=(reason,))], None
        programs, later = program_problems(surveyed, self.start_rules.check_only)
        programs += configuration_problems(surveyed)
        detail, metadata = metadata_facts(surveyed.metadata)
        dependencies = PreconditionCheck(
            DEPENDENCIES, (*plugins, *programs), warnings=(*later, *metadata), detail=detail
        )
        spots = blind_spots(surveyed, self.start_rules)
        if spots.skipped is not None:
            return [
                *checks,
                dependencies,
                PreconditionCheck(BLIND_SPOTS, skipped=spots.skipped),
            ], spots
        return [*checks, dependencies, PreconditionCheck(BLIND_SPOTS, warnings=spots.lines)], spots


def _api_check(message: str, *, blocks: bool) -> PreconditionCheck:
    """What every attempt would pay is the project's to fix; what only measures is a warning."""
    if blocks:
        return PreconditionCheck(API_BASE, (PreconditionProblem("config", message),))
    return PreconditionCheck(API_BASE, warnings=(message,))


def _is_api(control_id: str, controls: Sequence[Control]) -> bool:
    return any(c.id == control_id and c.report == "api" for c in controls)
