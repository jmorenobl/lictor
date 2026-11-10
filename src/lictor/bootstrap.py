"""Composition root for Lictor runtime and dependency injection."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lictor import __version__
from lictor.adapters.agents.claude_cli import ClaudeCLI
from lictor.adapters.agents.fake import FakeExecutor
from lictor.adapters.config.loader import (
    ProfileConfigInitializer,
    config_fingerprint,
    load_config,
    profile_skill_sections,
    resolve_defences,
)
from lictor.adapters.config.models import LictorConfig
from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.adapters.runner.subprocess_runner import SubprocessRunner
from lictor.adapters.spec.gherkin_source import GherkinSource
from lictor.adapters.store.clock import SystemClock
from lictor.adapters.store.jsonl_eventlog import JsonlEventLog
from lictor.adapters.vcs.git_cli import GitCli
from lictor.application.events import ObservedEventLog
from lictor.application.ports import (
    AuditSink,
    Clock,
    CommandRunner,
    EventLog,
    Executor,
    ResultReader,
    SpecSource,
    Vcs,
)
from lictor.application.run_files import CHECK_DIR, DOCTOR_DIR, aside_dir
from lictor.application.skill import render_skill
from lictor.application.spec_history import SpecHistory
from lictor.application.usecases.build_report import BuildReport
from lictor.application.usecases.check_red import CheckRed
from lictor.application.usecases.check_working_tree import CheckWorkingTree
from lictor.application.usecases.commit_green import CommitGreen
from lictor.application.usecases.implement_scenario import ImplementScenario
from lictor.application.usecases.init_project import InitProject
from lictor.application.usecases.preconditions import CheckPreconditions
from lictor.application.usecases.prepare_driver import PrepareDriver
from lictor.application.usecases.quarantine import Quarantine
from lictor.application.usecases.register_scenarios import RegisterScenarios
from lictor.application.usecases.verify_attempt import VerifyAttempt
from lictor.application.verification_env import VerificationEnvironment, VerificationEnvSpec
from lictor.domain.errors import ConfigError
from lictor.domain.model import Event
from lictor.domain.rules import RedOracle
from lictor.orchestration.graph import GraphContext


@dataclass(frozen=True)
class BootstrapResult:
    """Aggregated container holding configured adapters, use cases, and graph context."""

    config: LictorConfig
    config_hash: str
    clock: Clock
    event_log: EventLog
    vcs: Vcs
    runner: CommandRunner
    readers: dict[str, ResultReader]
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


def bootstrap_init(project_root: Path) -> InitProject:
    """Build InitProject use case with default profile initializer."""
    initializer = ProfileConfigInitializer()
    return InitProject(initializer=initializer)


def skill_content() -> str:
    """Lictor's skill as the installed version tells it, with what each profile adds."""
    return render_skill(__version__, profile_skill_sections())


def project_journal(project_root: Path) -> EventLog:
    """The project's journal, where every run writes its events."""
    return JsonlEventLog(project_root.resolve() / ".lictor" / "journal.jsonl")


def observed_journal(project_root: Path, observe: Callable[[Event], None]) -> EventLog:
    """The project's journal, telling `observe` each event once it is written."""
    return ObservedEventLog(project_journal(project_root), observe)


def checkout_repository(project_root: Path) -> GitCli:
    """The repository of a checkout, before any configuration is read: `run` starts here."""
    return GitCli(project_root.resolve())


def new_run_id() -> str:
    """Name a run before its worktree exists: the branch may be named after it."""
    return SystemClock().generate_run_id()


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

    vcs = GitCli(resolved_root)
    runner = SubprocessRunner()
    readers: dict[str, ResultReader] = {
        "exit-code": ExitCodeReader(),
        "junit": JUnitReader(),
        "mutation": MutationReader(),
    }
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
    report_uc = BuildReport(event_log=active_event_log)

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

    acceptance_reader = readers.get(active_config.acceptance.report, readers["junit"])
    spec_history = SpecHistory(vcs, spec_source)
    check_red_uc = CheckRed(
        environment=environment,
        reader=acceptance_reader,
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
        runner=runner,
        readers=readers,
        project_root=resolved_root,
        environment=environment,
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

    check_dir = aside_dir(resolved_root, CHECK_DIR)
    check_audit = AuditSink(JsonlEventLog(check_dir / "journal.jsonl"), active_clock)
    check_working_tree_uc = CheckWorkingTree(
        vcs=vcs,
        environment=VerificationEnvironment(
            vcs, runner, check_audit, resolved_root, defences.verification
        ),
        readers=readers,
        project_root=resolved_root,
        evidence_dir=check_dir,
    )

    commands = {f"del control '{c.id}'": c.run for c in active_config.controls}
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
        aside=VerificationEnvironment(
            vcs, runner, doctor_audit, resolved_root, defences.verification
        ),
        spec_source=spec_source,
    )

    graph_context = GraphContext(
        project_root=resolved_root,
        paths_writable=active_config.paths.writable,
        paths_protected=active_config.paths.protected,
        paths_driver=active_config.paths.driver,
        suppressions=defences.suppressions,
        driver_hints=defences.driver_hints,
        hints=active_config.hints,
        check_commands=active_config.agent_commands,
        model=active_config.backend.model,
        allowed_tools=active_config.agent_tools,
        max_turns=active_config.backend.max_turns,
        timeout_s=active_config.backend.timeout_s,
        max_attempts=active_config.limits.max_attempts,
        max_driver_attempts=active_config.limits.max_driver_attempts,
        error_max_chars=active_config.limits.error_max_chars,
        acceptance_run=active_config.acceptance.run,
        acceptance_report=active_config.acceptance.report,
        acceptance_timeout_s=active_config.acceptance.timeout_s,
        controls=active_config.controls,
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
    )

    return BootstrapResult(
        config=active_config,
        config_hash=config_fingerprint(active_config, defences),
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
    )
