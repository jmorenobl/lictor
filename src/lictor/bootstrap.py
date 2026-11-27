"""Composition root for Lictor runtime and dependency injection."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor import __version__
from lictor.adapters.agents.claude_cli import ClaudeCLI
from lictor.adapters.agents.fake import FakeExecutor
from lictor.adapters.ci.yaml_ci import YamlCiReader
from lictor.adapters.config.loader import (
    Defences,
    PackagedProfiles,
    ProfileConfigInitializer,
    config_fingerprint,
    declared_profile,
    load_config,
    load_profile,
    project_root_of,
    resolve_defences,
    rule_files,
    write_reviewed_conventions,
)
from lictor.adapters.config.models import LictorConfig, RetentionConfig, domain_controls
from lictor.adapters.readers.api import ApiReader
from lictor.adapters.readers.complexity import ComplexityReader
from lictor.adapters.readers.coverage import CoverageReader
from lictor.adapters.readers.duplication import DuplicationReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.adapters.readers.survey import SurveyReader
from lictor.adapters.runner.subprocess_runner import SubprocessRunner
from lictor.adapters.spec.gherkin_source import GherkinSource
from lictor.adapters.store.clock import SystemClock
from lictor.adapters.store.jsonl_eventlog import JsonlEventLog
from lictor.adapters.vcs.git_cli import GitCli
from lictor.application.api_baseline import ApiBaseline, api_control
from lictor.application.conventions import rule_files_at
from lictor.application.events import ObservedEventLog
from lictor.application.ports import (
    AuditSink,
    Clock,
    CommandRunner,
    EventLog,
    Executor,
    ReportReaders,
    SpecSource,
    Vcs,
)
from lictor.application.run_files import CHECK_DIR, DOCTOR_DIR, aside_dir
from lictor.application.skill import (
    ProjectProfile,
    profile_part,
    render_skill,
)
from lictor.application.spec_history import SpecHistory
from lictor.application.start_survey import StartSurvey, SurveyedCommandSpec
from lictor.application.unexecuted import UnexecutedSurvey
from lictor.application.usecases.build_report import BuildReport
from lictor.application.usecases.check_red import CheckRed
from lictor.application.usecases.check_working_tree import CheckWorkingTree
from lictor.application.usecases.commit_green import CommitGreen
from lictor.application.usecases.commit_refactor import CommitRefactor
from lictor.application.usecases.implement_scenario import ImplementScenario
from lictor.application.usecases.init_project import InitProject
from lictor.application.usecases.preconditions import CheckPreconditions
from lictor.application.usecases.prepare_driver import PrepareDriver
from lictor.application.usecases.quarantine import Quarantine
from lictor.application.usecases.refactor_scenario import RefactorScenario
from lictor.application.usecases.register_scenarios import RegisterScenarios
from lictor.application.usecases.verify_attempt import VerifyAttempt
from lictor.application.verification_env import VerificationEnvironment, VerificationEnvSpec
from lictor.domain.blind_spots import StartRules
from lictor.domain.controls import ACCEPTANCE_ID, Control, command_controls, controls_of_stage
from lictor.domain.conventions import (
    RuleFile,
    describe_conventions,
    describe_recorded,
    reviewed_record,
)
from lictor.domain.errors import ConfigError
from lictor.domain.model import Event
from lictor.domain.proof_level import ProofDefences
from lictor.domain.refactor_scope import CodePaths
from lictor.domain.rules import NO_COMMITS, RedOracle, control_label
from lictor.domain.stages import ATTEMPT, CHECK
from lictor.orchestration.graph import GraphContext


@dataclass(frozen=True)
class BootstrapResult:
    """Aggregated container holding configured adapters, use cases, and graph context."""

    config: LictorConfig
    config_hash: str
    # Every control, in the order the configuration declares them: the only conversion.
    controls: tuple[Control, ...]
    clock: Clock
    event_log: EventLog
    vcs: Vcs
    runner: CommandRunner
    readers: ReportReaders
    spec_source: SpecSource
    executor: Executor
    init_project: InitProject
    build_report: BuildReport
    prepare_driver: PrepareDriver
    check_red: CheckRed
    implement_scenario: ImplementScenario
    verify_attempt: VerifyAttempt
    commit_green: CommitGreen
    quarantine: Quarantine
    preconditions: CheckPreconditions
    check_working_tree: CheckWorkingTree
    register_scenarios: RegisterScenarios
    spec_history: SpecHistory
    verification: VerificationEnvSpec
    graph_context: GraphContext
    # How mature the profile is: `doctor` says it beside its name when it isn't stable.
    profile_status: str = "stable"


def bootstrap_init(project_root: Path) -> InitProject:
    """Build InitProject use case with default profile initializer."""
    initializer = ProfileConfigInitializer()
    return InitProject(initializer=initializer)


def skill_content(cwd: Path, profile: str | None = None) -> str:
    """Lictor's skill as the installed version tells it, with the project's profile.

    The profile is the one asked for, the one `lictor.json` names, or the one `lictor init` would
    detect, from the project's root even when asked from a subdirectory; with none of them, the
    skill lists them for the person to choose.
    """
    root = project_root_of(cwd)
    declared: str | None = None
    unreadable: str | None = None
    try:
        declared = declared_profile(root)
    except ConfigError as err:
        unreadable = str(err)
    project = ProjectProfile(root=root, cwd=cwd.resolve(), declared=declared, unreadable=unreadable)
    return render_skill(__version__, profile_part(project, PackagedProfiles(), requested=profile))


def project_journal(project_root: Path) -> EventLog:
    """The project's journal, where every run writes its events."""
    return JsonlEventLog(project_root.resolve() / ".lictor" / "journal.jsonl")


def observed_journal(project_root: Path, observe: Callable[[Event], None]) -> EventLog:
    """The project's journal, telling `observe` each event once it is written."""
    return ObservedEventLog(project_journal(project_root), observe)


def checkout_repository(project_root: Path) -> GitCli:
    """The repository of a checkout, before any configuration is read: `run` starts here."""
    return GitCli(project_root.resolve())


def retired_runs_kept(project_root: Path) -> int:
    """How many retired runs the checkout keeps: its `lictor.json`'s, or the default if it fails.

    `retire` reads it in the person's checkout, whose configuration may not load: losing a run's
    evidence because of a broken `lictor.json` would be worse than keeping the default number.
    """
    try:
        return load_config(project_root).retention.keep_retired
    except ConfigError:
        return RetentionConfig().keep_retired


def project_conventions(project_root: Path) -> str:
    """What `lictor conventions` prints: the project's rules and each rule file's state (F79)."""
    root = project_root.resolve()
    config = load_config(root)
    files = _head_rule_files(root, config)
    return describe_conventions(config.conventions.text, files, config.conventions.reviewed)


def record_reviewed_conventions(project_root: Path) -> str:
    """Take HEAD's rule files as reviewed, as they are now, in `lictor.json` (F79)."""
    root = project_root.resolve()
    files = _head_rule_files(root, load_config(root))
    write_reviewed_conventions(root, reviewed_record(files))
    return describe_recorded(files)


def _head_rule_files(root: Path, config: LictorConfig) -> tuple[RuleFile, ...]:
    vcs = GitCli(root)
    if not vcs.has_commits():
        raise ConfigError(f"Los ficheros de reglas se leen de HEAD. {NO_COMMITS}")
    return rule_files_at(vcs, "HEAD", rule_files(config))


def new_run_id() -> str:
    """Name a run before its worktree exists: the branch may be named after it."""
    return SystemClock().generate_run_id()


def unconfigured_preconditions(project_root: Path) -> tuple[CheckPreconditions, Clock]:
    """What `doctor` can still check when `lictor.json` doesn't load (F30).

    Without a configuration there is no executor to ask and no environment to build: the
    repository, git, the features, the skill and the temporary directory don't need either.
    """
    resolved_root = project_root.resolve()
    checks = CheckPreconditions(
        GitCli(resolved_root), None, None, resolved_root, spec_source=GherkinSource()
    )
    return checks, SystemClock()


def _start_survey(config: LictorConfig, defences: Defences) -> StartSurvey | None:
    """The profile's survey, asked about every command control of both stages and the
    acceptance (ADR-0015)."""
    if defences.survey_run is None:
        return None
    return StartSurvey(
        run=defences.survey_run,
        commands=[
            *(SurveyedCommandSpec(c.id, c.run) for c in command_controls(domain_controls(config))),
            SurveyedCommandSpec(ACCEPTANCE_ID, config.acceptance.run),
        ],
        detect=defences.detect,
        reader=SurveyReader(),
        ci=YamlCiReader(defences.ci_hooks),
    )


def proof_defences(defences: Defences, controls: Sequence[Control]) -> ProofDefences:
    """The resolved defences the level of proof is computed from (ADR-0020): one conversion.

    `controls` are the ones a run verifies, the `attempt` ones.
    """
    return ProofDefences(
        link=defences.map_link,
        red_kinds=bool(defences.red_kinds),
        stub_impostors=defences.impostors is not None,
        interface_impostor=defences.interface_impostor is not None,
        gherkin=defences.interface_impostor is not None
        and defences.interface_impostor.gherkin is not None,
        env_hashed=bool(defences.verification.prepare),
        suppressions=bool(defences.suppressions or defences.banned_patterns),
        blocking_controls=any(c.mode == "block" for c in controls),
        survey=defences.survey_run is not None,
        plugin_controls=tuple((c.id, c.when_plugin) for c in controls if c.when_plugin is not None),
    )


def _start_rules(config: LictorConfig, defences: Defences) -> StartRules:
    """Who may write what, and which controls only `check` runs: `L0-rutas`'s rules."""
    return StartRules(
        writable=config.paths.writable,
        driver=config.paths.driver,
        forbidden=defences.forbidden_paths,
        check_only=[c.id for c in controls_of_stage(domain_controls(config), CHECK)],
        accepted=config.blind_spots.accepted,
    )


def _unexecuted(
    vcs: GitCli, root: Path, config: LictorConfig, defences: Defences, readers: ReportReaders
) -> UnexecutedSurvey | None:
    """The survey of the start, and what tells a change no control executed with it (H2)."""
    survey = _start_survey(config, defences)
    if survey is None:
        return None
    rules = _start_rules(config, defences)
    return UnexecutedSurvey(vcs, root, survey, rules, readers.junit, _junit_ids(config))


def _junit_ids(config: LictorConfig) -> tuple[str, ...]:
    """The commands whose JUnit says which test files ran: the controls with one, and the
    acceptance."""
    junit = (c.id for c in command_controls(domain_controls(config)) if c.report == "junit")
    return (*junit, ACCEPTANCE_ID)


def _api_baseline(config: LictorConfig, readers: ReportReaders) -> ApiBaseline | None:
    """How the public API of a base is measured (ADR-0017): with the API control, if any."""
    api = api_control(domain_controls(config))
    if api is None:
        return None
    return ApiBaseline(
        api.run, api.timeout_s, readers.api, tuple(config.api.accepted), ref=config.api.base
    )


def bootstrap_runtime(
    project_root: Path,
    *,
    executor: Executor | None = None,
    clock: Clock | None = None,
    event_log: EventLog | None = None,
    config: LictorConfig | None = None,
) -> BootstrapResult:
    """Assemble all adapters, use cases, and graph context from lictor.json."""
    resolved_root = project_root.resolve()
    active_config = config if config is not None else load_config(resolved_root)
    defences = resolve_defences(active_config)

    active_clock = clock if clock is not None else SystemClock()
    lictor_dir = resolved_root / ".lictor"
    lictor_dir.mkdir(parents=True, exist_ok=True)
    active_event_log = event_log if event_log is not None else project_journal(resolved_root)

    vcs = GitCli(resolved_root, ignored=active_config.gitignore)
    runner = SubprocessRunner()
    readers = ReportReaders(
        junit=JUnitReader(),
        coverage=CoverageReader(),
        mutation=MutationReader(),
        complexity=ComplexityReader(),
        duplication=DuplicationReader(),
        api=ApiReader(),
    )
    spec_source = GherkinSource()

    if executor is None:
        if active_config.backend.kind == "claude-cli":
            executor = ClaudeCLI(runner, auth=active_config.backend.auth)
        elif active_config.backend.kind == "fake":
            fake_config_path = lictor_dir / "fake_executor.json"
            if fake_config_path.is_file():
                try:
                    fake_data = json.loads(fake_config_path.read_text(encoding="utf-8"))
                    executor = FakeExecutor(**fake_data)
                except (json.JSONDecodeError, OSError, TypeError) as exc:
                    raise ConfigError(f"Error al leer fake_executor.json: {exc}") from exc
            else:
                executor = FakeExecutor(mode="good")
        else:
            raise ConfigError(f"Tipo de backend no soportado: '{active_config.backend.kind}'")

    initializer = ProfileConfigInitializer()
    init_uc = InitProject(initializer=initializer)
    report_uc = BuildReport(
        event_log=active_event_log, search=vcs, writable=active_config.paths.writable
    )

    prepare_driver_uc = PrepareDriver(
        vcs=vcs,
        executor=executor,
        event_log=active_event_log,
        clock=active_clock,
    )

    audit_sink = AuditSink(event_log=active_event_log, clock=active_clock)
    environment = VerificationEnvironment(
        vcs=vcs,
        runner=runner,
        audit=audit_sink,
        project_root=resolved_root,
        spec=defences.verification,
    )

    spec_history = SpecHistory(vcs, spec_source)
    check_red_uc = CheckRed(
        environment=environment,
        reader=readers.junit,
        vcs=vcs,
        history=spec_history,
        audit=audit_sink,
    )

    implement_uc = ImplementScenario(
        vcs=vcs,
        executor=executor,
        spec_source=spec_source,
        event_log=active_event_log,
        clock=active_clock,
    )

    verify_attempt_uc = VerifyAttempt(
        vcs=vcs,
        environment=environment,
        readers=readers,
        project_root=resolved_root,
        api=_api_baseline(active_config, readers),
    )

    commit_green_uc = CommitGreen(
        vcs=vcs,
        event_log=active_event_log,
        clock=active_clock,
    )

    quarantine_uc = Quarantine(
        event_log=active_event_log,
        clock=active_clock,
    )

    unexecuted = _unexecuted(vcs, resolved_root, active_config, defences, readers)
    check_audit = AuditSink(
        JsonlEventLog(aside_dir(resolved_root, CHECK_DIR) / "journal.jsonl"), active_clock
    )
    check_working_tree_uc = CheckWorkingTree(
        vcs=vcs,
        environment=VerificationEnvironment(
            vcs, runner, check_audit, resolved_root, defences.verification
        ),
        readers=readers,
        project_root=resolved_root,
        api=_api_baseline(active_config, readers),
        unexecuted=unexecuted,
    )

    # A run never runs the `check` controls (ADR-0007): it gets them apart, only to name them.
    controls = domain_controls(active_config)
    attempt_controls = controls_of_stage(controls, ATTEMPT)
    check_controls = controls_of_stage(controls, CHECK)
    # A reader runs nothing: only the controls with a command of their own need its program.
    commands = {control_label(c.id): c.run for c in command_controls(attempt_controls)}
    commands["de la aceptación"] = active_config.acceptance.run
    doctor_audit = AuditSink(
        JsonlEventLog(aside_dir(resolved_root, DOCTOR_DIR) / "journal.jsonl"), active_clock
    )
    preconditions_uc = CheckPreconditions(
        vcs=vcs,
        executor=executor,
        environment=environment,
        project_root=resolved_root,
        commands=commands,
        check_stage_ids=[c.id for c in check_controls],
        check_stage_commands={c.id: c.run for c in command_controls(check_controls)},
        aside=VerificationEnvironment(
            vcs, runner, doctor_audit, resolved_root, defences.verification
        ),
        spec_source=spec_source,
        needs_plugins=defences.needs_plugins,
        survey=unexecuted.survey if unexecuted is not None else None,
        start_rules=_start_rules(active_config, defences),
        head_controls=attempt_controls,
        readers=readers,
        api=_api_baseline(active_config, readers),
        rule_files=rule_files(active_config),
        reviewed=active_config.conventions.reviewed,
        proof=proof_defences(defences, attempt_controls),
        env_prepared=bool(defences.verification.prepare),
        acceptance_run=active_config.acceptance.run,
        agent_tools=active_config.agent_tools,
        code_paths=CodePaths(tuple(defences.code_paths), tuple(defences.code_exclude)),
    )

    graph_context = GraphContext(
        project_root=resolved_root,
        paths_writable=active_config.paths.writable,
        paths_protected=active_config.paths.protected,
        paths_driver=active_config.paths.driver,
        suppressions=defences.suppressions,
        driver_hints=defences.driver_hints,
        hints=active_config.hints,
        conventions=active_config.conventions.text,
        api_base_ref=active_config.api.base,
        unexecuted=unexecuted,
        model=active_config.backend.model,
        allowed_tools=active_config.agent_tools,
        max_turns=active_config.backend.max_turns,
        timeout_s=active_config.backend.timeout_s,
        max_attempts=active_config.limits.max_attempts,
        max_driver_attempts=active_config.limits.max_driver_attempts,
        error_max_chars=active_config.limits.error_max_chars,
        acceptance_run=active_config.acceptance.run,
        acceptance_timeout_s=active_config.acceptance.timeout_s,
        controls=attempt_controls,
        check_controls=check_controls,
        environment=environment,
        prepare_driver=prepare_driver_uc,
        check_red=check_red_uc,
        implement_scenario=implement_uc,
        verify_attempt=verify_attempt_uc,
        commit_green=commit_green_uc,
        quarantine=quarantine_uc,
        preconditions=preconditions_uc,
        spec_source=spec_source,
        spec_history=spec_history,
        vcs=vcs,
        executor=executor,
        event_log=active_event_log,
        clock=active_clock,
        profile_name=active_config.profile,
        result_branch=vcs.current_branch() if vcs.is_linked_worktree() else "",
        build_report=report_uc,
        acceptance_failure_exit_codes=active_config.acceptance.failure_exit_codes,
        forbidden_paths=defences.forbidden_paths,
        driver_exempt_paths=defences.driver_exempt_paths,
        banned_patterns=defences.banned_patterns,
        oracle=RedOracle(
            red_kinds=defences.red_kinds,
            writable=active_config.paths.writable,
            driver=active_config.paths.driver,
        ),
        map_link=defences.map_link,
        impostors=defences.impostors,
        interface_impostor=defences.interface_impostor,
        # The refactor after each implemented green (ADR-0010), unless the project turned it off.
        refactor_scenario=(
            RefactorScenario(vcs, executor, spec_source, active_event_log, active_clock)
            if active_config.refactor.enabled
            else None
        ),
        commit_refactor=(
            CommitRefactor(vcs, active_event_log, active_clock)
            if active_config.refactor.enabled
            else None
        ),
        refactor_hints=defences.refactor_hints,
        code_paths=CodePaths(tuple(defences.code_paths), tuple(defences.code_exclude)),
        agent_files=tuple(defences.verification.files),
    )

    return BootstrapResult(
        config=active_config,
        config_hash=config_fingerprint(active_config, defences),
        controls=controls,
        clock=active_clock,
        event_log=active_event_log,
        vcs=vcs,
        runner=runner,
        readers=readers,
        spec_source=spec_source,
        executor=executor,
        init_project=init_uc,
        build_report=report_uc,
        prepare_driver=prepare_driver_uc,
        check_red=check_red_uc,
        implement_scenario=implement_uc,
        verify_attempt=verify_attempt_uc,
        commit_green=commit_green_uc,
        quarantine=quarantine_uc,
        preconditions=preconditions_uc,
        check_working_tree=check_working_tree_uc,
        register_scenarios=RegisterScenarios(vcs, spec_history, audit_sink, resolved_root),
        spec_history=spec_history,
        verification=defences.verification,
        graph_context=graph_context,
        profile_status=load_profile(active_config.profile).status,
    )
