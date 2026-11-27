"""VerifyAttempt use case with deterministic verification pipeline.

Includes ADR-0001 integrity snapshot for state protection.
"""

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from lictor.application.api_baseline import ApiBaseline
from lictor.application.control_reports import (
    ControlEvidence,
    ControlRun,
    Judging,
    Readings,
    failure_reason,
    not_run,
    run_control,
    take_turn,
)
from lictor.application.ports import ReportReaders, Vcs, VerificationCommands
from lictor.application.refactor_scope import green_scope
from lictor.application.run_files import (
    attempt_prefix,
    describe_crash,
    fill_report_paths,
    format_output,
    new_step_dir,
    relative_to_project,
    save_output,
    step_evidence,
    verification_tree,
    write_text,
)
from lictor.application.seal import collect_monitored_files
from lictor.domain.api import ApiBase
from lictor.domain.controls import (
    Control,
    RunStep,
    verification_steps,
)
from lictor.domain.errors import DriverError, InternalLictorError, SpecError
from lictor.domain.model import (
    AgentResult,
    Candidate,
    CommandOutput,
    RepoPin,
    ScenarioId,
    TestCaseResult,
)
from lictor.domain.refactor import (
    REFACTOR_REGRESSION,
    Measure,
    oracle_measures,
    refactor_regressions,
    refactor_subject,
    regression_reason,
)
from lictor.domain.refactor_scope import (
    NO_CODE,
    REFACTOR_SCOPE,
    CodePaths,
    out_of_scope,
    out_of_scope_reason,
)
from lictor.domain.results import (
    AcceptanceRun,
    Block,
    ControlEntry,
    NotExecuted,
    Proceed,
    Ran,
    Skipped,
    StateTampered,
    Unjudged,
    VerifyResult,
    consequence,
    control_record,
)
from lictor.domain.rules import (
    MapLink,
    acceptance_failure,
    collateral_greens,
    condense_error,
    control_path_status,
    describe_suppressions,
    forbidden_paths,
    map_test_cases_to_scenarios,
    mapping_failure_reason,
    path_within,
    plugin_skip_reason,
    present_scenarios,
    registration_subject,
    render_full_error,
    routes_rejected,
    suppression_matches,
    suppressions_not_moved,
)
from lictor.domain.verdicts import (
    Crashed,
    Execution,
    report_cases,
    trust,
)

RoleKind = Literal["implement", "driver", "refactor"]

STATE_TAMPERED = "El estado de la ejecución fue modificado indebidamente"


@dataclass(frozen=True, kw_only=True)
class VerifyAttemptRequest:
    """Request payload for VerifyAttempt use case; paths and suppressions come from the profile."""

    scenario_id: str | ScenarioId
    run_id: str
    role: RoleKind = "implement"
    paths_writable: Sequence[str]
    paths_driver: Sequence[str]
    suppressions: Sequence[str]
    controls: Sequence[Control] = field(default_factory=tuple)  # the `attempt` ones
    acceptance_run: str = ""
    acceptance_timeout_s: int = 600
    green_scenario_ids: Sequence[str] = field(default_factory=tuple)
    pending_scenario_ids: Sequence[str] = field(default_factory=tuple)
    all_scenario_ids: Sequence[str | ScenarioId] = field(default_factory=tuple)
    scenario_files: Mapping[str, str] = field(default_factory=dict)  # relative to the project
    error_max_chars: int = 4000
    attempt: int = 1
    acceptance_failure_exit_codes: Sequence[int] = (1,)
    forbidden_paths: Sequence[str] = ()
    banned_patterns: Sequence[str] = ()
    tree: str | None = None
    link: MapLink = "name"
    # A refactor (ADR-0010): the green it starts from, and what its verification measured.
    refactor_of: str = ""
    baseline: Mapping[str, Measure] = field(default_factory=dict)
    code_paths: CodePaths = NO_CODE  # where the code is: a refactor stays in its own (F84)
    # The commit the run started from, against which the public API is compared (ADR-0017).
    api_base: str | None = None
    api: ApiBase | None = None  # filled in by VerifyAttempt.execute
    step_dir: Path | None = None  # filled in by VerifyAttempt.execute
    tree_dir: Path | None = None  # filled in by VerifyAttempt.execute


@dataclass(frozen=True)
class _Failure:
    """A failed gate: what the agent reads next is condensed, the rest goes to `runs/`."""

    failed_at: str
    reason: str
    label: str | None = None
    cases: Sequence[TestCaseResult] = ()
    all_cases: Sequence[TestCaseResult] | None = None
    output: str = ""
    controls: tuple[ControlEntry, ...] = ()
    acceptance: AcceptanceRun | None = None
    scenario_results: dict[str, str] = field(default_factory=dict)
    reverted_files: tuple[str, ...] = ()
    failure: tuple[str, str] | None = None


def control_summaries(result: VerifyResult) -> dict[str, dict[str, object]]:
    """What the journal keeps of each control's result, by its id (ADR-0009)."""
    return {entry.control.id: control_record(entry) for entry in result.controls}


class IntegritySnapshot:
    """ADR-0001: state files and repository, held in memory while untrusted code runs.

    What changed is restored and listed in `changes`. SQLite keeps recent writes in `-wal`, so the
    checkpoint is both files. `-shm` is only an index over them, moved by anyone who merely reads
    the database, so it doesn't count as a change and only returns along with the data.
    """

    MONITORED_LICTOR_FILES = (
        "seal.json",
        "checkpoints.sqlite",
        "checkpoints.sqlite-wal",
        "journal.jsonl",
        "verify_env.json",
        "current_run",
        "fake_executor.json",
    )
    WAL_INDEX = "checkpoints.sqlite-shm"

    def __init__(
        self, lictor_dir: Path, project_root: Path | None = None, vcs: Vcs | None = None
    ) -> None:
        self.lictor_dir = lictor_dir
        self.project_root = project_root if project_root is not None else lictor_dir.parent
        self.vcs = vcs
        self.snapshots: dict[Path, bytes | None] = {}
        self.pin: RepoPin | None = None
        self.changes: list[str] = []
        self.take()

    def _monitored_paths(self) -> list[Path]:
        paths = [self.lictor_dir / name for name in self.MONITORED_LICTOR_FILES]
        paths.append(self.project_root / "lictor.json")
        paths.append(self.lictor_dir / "lictor.json")
        return paths

    def take(self) -> None:
        for target in [*self._monitored_paths(), self.lictor_dir / self.WAL_INDEX]:
            self.snapshots[target] = target.read_bytes() if target.is_file() else None
        self.pin = self.vcs.pin() if self.vcs is not None else None

    def _restore_file(self, target: Path) -> bool:
        """Bring a file back to what it held and tell whether it had changed."""
        initial_bytes = self.snapshots[target]
        if initial_bytes is None:
            if not target.exists():
                return False
            target.unlink(missing_ok=True)
            return True
        if target.is_file() and target.read_bytes() == initial_bytes:
            return False
        target.write_bytes(initial_bytes)
        return True

    def verify_and_restore(self) -> bool:
        """Check if anything monitored changed. If so, restore it and return False."""
        changed = [path for path in self._monitored_paths() if self._restore_file(path)]
        if changed:
            self._restore_file(self.lictor_dir / self.WAL_INDEX)
        self.changes = [relative_to_project(path, self.project_root) for path in changed]
        if self.vcs is not None and self.pin is not None:
            self.changes.extend(self.vcs.restore(self.pin))
        return not self.changes


@dataclass(frozen=True)
class AgentCopyOutcome:
    """What an agent call left: its result and what could be read from its copy."""

    agent_result: AgentResult
    tree: str | None = None
    tampered: tuple[str, ...] = ()
    unreadable: str | None = None
    reverted: tuple[str, ...] = ()

    @property
    def failed_at(self) -> str:
        return "L0-estado" if self.tampered else "L0-rutas"

    @property
    def error(self) -> str | None:
        return describe_tampering(self.tampered) if self.tampered else self.unreadable


def describe_tampering(changes: Sequence[str]) -> str:
    """Name what was restored: the agent reads it, and so does whoever audits the run."""
    return f"{STATE_TAMPERED}: {', '.join(changes)}. Se ha restaurado."


def _scenario_str(scenario_id: str | ScenarioId) -> str:
    return str(scenario_id.value if isinstance(scenario_id, ScenarioId) else scenario_id).strip()


class VerifyAttempt:
    """Deterministic multi-gate verification pipeline for an agent attempt."""

    def __init__(
        self,
        vcs: Vcs,
        environment: VerificationCommands,
        readers: ReportReaders,
        project_root: Path,
        api: ApiBaseline | None = None,
    ) -> None:
        self.vcs = vcs
        self.environment = environment
        self.readers = readers
        self.project_root = project_root
        self.api = api

    def execute(self, req: VerifyAttemptRequest) -> VerifyResult:
        """Run verification steps L0 to Acceptance sequentially."""
        scenario = _scenario_str(req.scenario_id)
        prefix = attempt_prefix(self.project_root, req.run_id, scenario, req.attempt)
        step = "refactor-verify" if req.role == "refactor" else "verify"
        step_dir = new_step_dir(prefix.with_name(f"{prefix.name}-{step}"))
        req = replace(req, step_dir=step_dir)
        result = self._verify(req, scenario)
        return replace(result, evidence=step_evidence(step_dir, self.project_root))

    def _verify(self, req: VerifyAttemptRequest, scenario: str) -> VerifyResult:
        gates = self._run_gates(req)
        if not gates.ok:
            return gates

        # Entorno, candidato y clon limpio: nada de lo ignorado o sin versionar cuenta
        env_problem = self._environment_problem(req)
        if env_problem:
            return self._fail(req, _Failure("L0-entorno", env_problem))

        candidate = self._create_candidate(req, scenario)
        req = replace(req, api=self._api_base(req))
        with verification_tree(self.vcs, candidate.sha, self.project_root) as tree:
            result = self._run_commands(replace(req, tree_dir=tree))
        if not result.ok:
            return result

        # The commands above run project code, which can reach the environment as well.
        env_problem = self._environment_problem(req)
        if env_problem:
            return self._fail(
                req,
                _Failure(
                    "L0-entorno",
                    env_problem,
                    controls=result.controls,
                    acceptance=result.acceptance,
                ),
            )
        collateral = collateral_greens(result.scenario_results, req.pending_scenario_ids, scenario)
        if collateral:
            candidate = self._chain_collateral(req, candidate, collateral)
        return replace(result, candidate=candidate, collateral=tuple(collateral))

    def _chain_collateral(
        self, req: VerifyAttemptRequest, candidate: Candidate, collateral: Sequence[str]
    ) -> Candidate:
        """Register on top of the candidate what the same tree also turns green.

        The branch then moves to both at once: no state in between can be left by a crash.
        """
        chained = self.vcs.commit_tree(
            candidate.tree,
            message=registration_subject(collateral, "verde colateral"),
            trailers={
                "Scenario": list(collateral),
                "Lictor-Collateral": candidate.sha,
                "Lictor-Run": req.run_id,
            },
            parent=candidate.sha,
            empty=True,
        )
        return Candidate(chained.sha, candidate.tree, candidate.parent)

    def _api_base(self, req: VerifyAttemptRequest) -> ApiBase | None:
        """The run's base for an API control the verification runs, measured once per run.

        Its command is Lictor's module, but a project may change it, so it runs as any control.
        """
        if self.api is None or req.api_base is None:
            return None
        if not any(control.report == "api" for control in req.controls):
            return None

        def run_in(
            command: Sequence[str], tree: Path, timeout_s: int
        ) -> tuple[CommandOutput, tuple[str, ...]]:
            snapshot = IntegritySnapshot(self.project_root / ".lictor", self.project_root, self.vcs)
            output = self.environment.run(command, tree, timeout_s)
            snapshot.verify_and_restore()
            return output, tuple(snapshot.changes)

        return self.api.base(req.api_base, self.vcs, self.project_root, run_in, self._step_dir(req))

    def _run_gates(self, req: VerifyAttemptRequest) -> VerifyResult:
        """Run L0 over what the agent left: the tree read from its copy, before any project code."""
        if not self.vcs.changes(self._tree(req)):
            return self._fail(req, _Failure("L0-sin-cambios", "El intento no cambia nada"))

        routes_res = self._check_routes(req)
        if not routes_res.ok:
            return routes_res

        if req.role == "refactor":
            scope_res = self._check_scope(req)
            if not scope_res.ok:
                return scope_res

        if req.role != "driver":
            seal_res = self._check_seal(req)
            if not seal_res.ok:
                return seal_res

        return self._check_suppressions(req)

    def _environment_problem(self, req: VerifyAttemptRequest) -> str | None:
        return self.environment.ensure(req.run_id)

    def _run_project_command(
        self, cmd: Sequence[str], req: VerifyAttemptRequest, timeout_s: int
    ) -> CommandOutput:
        return self.environment.run(cmd, self._tree_dir(req), timeout_s)

    def _create_candidate(self, req: VerifyAttemptRequest, scenario: str) -> Candidate:
        if req.role == "refactor":
            return self.vcs.commit_tree(
                self._tree(req),
                message=refactor_subject(scenario),
                trailers={"Lictor-Run": req.run_id, "Lictor-Refactor-Of": req.refactor_of},
            )
        return self.vcs.commit_tree(
            self._tree(req),
            message=f"feat({scenario}): implementar escenario",
            trailers={
                "Scenario": scenario,
                "Lictor-Run": req.run_id,
                "Lictor-Attempts": str(req.attempt),
            },
        )

    def _run_commands(self, req: VerifyAttemptRequest) -> VerifyResult:
        """Run the controls of a run, which are all the `attempt` ones, then the acceptance."""
        entries: list[ControlEntry] = []
        readings = Readings()
        installed = self.environment.installed_plugins
        for step in verification_steps(req.controls, req.controls, installed):
            ctrl_run = take_turn(
                step, readings, lambda run: self._run_present_control(run, req, readings)
            )
            entries.append(ctrl_run.entry)
            ctrl = step.control
            match consequence(ctrl_run.entry, ctrl_run.output):
                case StateTampered(changes=changes):
                    failure = _Failure(
                        "L0-estado", describe_tampering(changes), controls=tuple(entries)
                    )
                    return self._fail(req, failure)
                case Block(reason=reason, cases=cases):
                    failure = _Failure(
                        ctrl.id,
                        failure_reason(step, ctrl_run.entry, reason),
                        label=f"{ctrl.level} ({ctrl.id})" if ctrl.level else ctrl.id,
                        cases=cases,
                        output=ctrl_run.output,
                        controls=tuple(entries),
                    )
                    return self._fail(req, failure)
                case Proceed():
                    pass
        if req.role == "refactor":
            regressions = refactor_regressions(req.baseline, oracle_measures(entries))
            if regressions:
                failure = _Failure(
                    REFACTOR_REGRESSION, regression_reason(regressions), controls=tuple(entries)
                )
                return self._fail(req, failure)
        return self._run_acceptance(req, tuple(entries))

    def _fail(self, req: VerifyAttemptRequest, failure: _Failure) -> VerifyResult:
        """Condense the failure for the agent and keep it whole in the attempt's directory."""
        label = failure.label or failure.failed_at
        all_cases = failure.cases if failure.all_cases is None else failure.all_cases
        error_path = self._step_dir(req) / "error.txt"
        write_text(
            error_path,
            render_full_error(label, failure.reason, all_cases, failure.output),
        )
        return VerifyResult(
            ok=False,
            failed_at=failure.failed_at,
            condensed_error=condense_error(
                label, failure.reason, failure.cases, max_chars=req.error_max_chars
            ),
            controls=failure.controls,
            acceptance=failure.acceptance,
            scenario_results=failure.scenario_results,
            reverted_files=failure.reverted_files,
            error_path=relative_to_project(error_path, self.project_root),
            failure=failure.failure,
        )

    def _tree(self, req: VerifyAttemptRequest) -> str:
        if req.tree is None:
            raise InternalLictorError("El intento llegó a verificación sin árbol")
        return req.tree

    def _step_dir(self, req: VerifyAttemptRequest) -> Path:
        if req.step_dir is None:
            raise InternalLictorError("step_dir lo asigna execute")
        return req.step_dir

    def _tree_dir(self, req: VerifyAttemptRequest) -> Path:
        if req.tree_dir is None:
            raise InternalLictorError("tree_dir lo asigna execute")
        return req.tree_dir

    def _check_routes(self, req: VerifyAttemptRequest) -> VerifyResult:
        allowed = list(req.paths_writable)
        if req.role == "driver":
            allowed.extend(req.paths_driver)

        changes = self.vcs.changes(self._tree(req))
        changed_files = [change.path for change in changes]
        rejected = set(forbidden_paths(changed_files, req.forbidden_paths))
        # A link in the commit can point to what the commit leaves out.
        rejected.update(change.path for change in changes if change.is_link)
        forbidden: list[str] = [
            f for f in changed_files if f in rejected or not path_within(f, allowed)
        ]

        if forbidden:
            err = routes_rejected(forbidden, req.paths_driver)
            return self._fail(req, _Failure("L0-rutas", err, reverted_files=tuple(forbidden)))
        return VerifyResult(ok=True)

    def _check_scope(self, req: VerifyAttemptRequest) -> VerifyResult:
        """`L0-alcance`: a refactor changes only the code its scenario changed (F84)."""
        if not req.refactor_of:
            raise InternalLictorError("El refactor llegó a verificación sin su verde")
        scope = green_scope(self.vcs, req.refactor_of, req.baseline, req.code_paths)
        outside = out_of_scope(scope, self.vcs.changes(self._tree(req)), req.code_paths)
        if outside:
            failure = _Failure(
                REFACTOR_SCOPE,
                out_of_scope_reason(outside, scope),
                reverted_files=tuple(outside),
            )
            return self._fail(req, failure)
        return VerifyResult(ok=True)

    def _check_seal(self, req: VerifyAttemptRequest) -> VerifyResult:
        seal_path = self.project_root / ".lictor" / "seal.json"
        if not seal_path.is_file():
            err = "No se encontró el archivo de sello .lictor/seal.json"
            return self._fail(req, _Failure("L0-sello", err))

        try:
            seal_data = json.loads(seal_path.read_text(encoding="utf-8"))
            sealed_files: dict[str, str] = seal_data.get("files", {})
        except (json.JSONDecodeError, OSError):
            err = "El archivo de sello .lictor/seal.json es inválido o ilegible"
            return self._fail(req, _Failure("L0-sello", err))

        validation_err = self._validate_sealed_files(sealed_files, req)
        if validation_err:
            return self._fail(req, _Failure("L0-sello", validation_err))

        return VerifyResult(ok=True)

    def _validate_sealed_files(
        self,
        sealed_files: dict[str, str],
        req: VerifyAttemptRequest,
    ) -> str | None:
        if not sealed_files:
            return "El archivo de sello .lictor/seal.json no contiene archivos sellados"

        try:
            current_files = collect_monitored_files(self.project_root, req.paths_driver)
        except SpecError as exc:
            return str(exc)

        for rel_path in sorted(sealed_files):
            if rel_path not in current_files:
                return f"Falta archivo sellado: {rel_path}"
            if current_files[rel_path] != sealed_files[rel_path]:
                return f"El sello no coincide para {rel_path}"

        for rel_path in sorted(current_files):
            if rel_path not in sealed_files:
                prefix = (
                    "Archivo de características no sellado"
                    if rel_path.startswith("features/")
                    else "Archivo de driver no sellado"
                )
                return f"{prefix}: {rel_path}"

        return None

    def _check_suppressions(self, req: VerifyAttemptRequest) -> VerifyResult:
        if not req.suppressions and not req.banned_patterns:
            return VerifyResult(ok=True)

        tree = self._tree(req)
        added_lines = self.vcs.added_lines(tree)
        if req.role == "driver":
            detected = suppression_matches(added_lines, req.suppressions, req.banned_patterns)
        else:
            # The implementer and the refactor move code that already had its suppressions (F72).
            detected = suppressions_not_moved(
                added_lines, self.vcs.removed_lines(tree), req.suppressions, req.banned_patterns
            )
        if detected:
            return self._fail(req, _Failure("L0-supresiones", describe_suppressions(detected)))

        return VerifyResult(ok=True)

    def _run_present_control(
        self, step: RunStep, req: VerifyAttemptRequest, readings: Readings
    ) -> ControlRun:
        """Skip a control whose path neither HEAD nor the candidate has; fail one removed.

        Skip one whose plugin the environment lacks, too: the dependency is the project's.
        """
        ctrl = step.control
        if step.missing is not None:
            return not_run(step, Skipped(plugin_skip_reason(step.missing)), readings)
        if ctrl.when_exists is None:
            return self._run_single_control(step, req, readings)
        in_tree = os.path.lexists(self._tree_dir(req) / ctrl.when_exists)
        deleted = [change.path for change in self.vcs.changes(self._tree(req)) if change.is_deleted]
        status = control_path_status(ctrl.when_exists, in_tree=in_tree, deleted=deleted)
        if status == "absent":
            reason = f"No existe {ctrl.when_exists} ni en HEAD ni en el intento"
            return not_run(step, Skipped(reason), readings)
        if status == "removed":
            reason = (
                f"El intento elimina {ctrl.when_exists}, que existía: "
                f"el control {ctrl.id} ya no puede ejecutarse"
            )
            return not_run(step, NotExecuted(reason), readings)
        return self._run_single_control(step, req, readings)

    def _run_single_control(
        self, step: RunStep, req: VerifyAttemptRequest, readings: Readings
    ) -> ControlRun:
        step_dir = self._step_dir(req)

        def evidence_of(control_id: str) -> ControlEvidence:
            return ControlEvidence(
                step_dir / f"control_{control_id}.xml",
                step_dir / f"control_{control_id}.log",
                self.project_root,
            )

        def execute(
            command: Sequence[str], timeout_s: int
        ) -> tuple[CommandOutput, tuple[str, ...]]:
            snapshot = IntegritySnapshot(self.project_root / ".lictor", self.project_root, self.vcs)
            output = self._run_project_command(command, req, timeout_s)
            snapshot.verify_and_restore()
            return output, tuple(snapshot.changes)

        return run_control(step, Judging(self.readers, req.api), evidence_of, execute, readings)

    def _run_acceptance(
        self, req: VerifyAttemptRequest, controls: tuple[ControlEntry, ...]
    ) -> VerifyResult:
        step_dir = self._step_dir(req)
        out_file = step_dir / "acceptance.xml"
        log_file = step_dir / "acceptance.log"
        cmd_tokens = fill_report_paths([(req.acceptance_run, out_file)])

        snapshot = IntegritySnapshot(self.project_root / ".lictor", self.project_root, self.vcs)
        cmd_out = self._run_project_command(cmd_tokens, req, req.acceptance_timeout_s)
        tampered = not snapshot.verify_and_restore()
        save_output(log_file, cmd_tokens, cmd_out)
        execution = Execution.of(cmd_out, req.acceptance_timeout_s)
        ran = Ran(tuple(cmd_tokens), execution, relative_to_project(log_file, self.project_root))
        if tampered:
            changes = tuple(snapshot.changes)
            tampering = _Failure(
                "L0-estado",
                describe_tampering(changes),
                controls=controls,
                acceptance=AcceptanceRun(ran, Unjudged(changes)),
            )
            return self._fail(req, tampering)

        output = format_output(cmd_out)
        junit = self.readers.junit.read(out_file)
        trusted = trust(junit, execution, req.acceptance_failure_exit_codes)
        acceptance = AcceptanceRun(ran, trusted)
        if isinstance(trusted, Crashed):
            return self._fail(
                req,
                _Failure(
                    "acceptance",
                    describe_crash(trusted.reason, output),
                    cases=report_cases(junit),
                    output=output,
                    controls=controls,
                    acceptance=acceptance,
                ),
            )

        # Map test cases to scenario IDs
        try:
            mapped = map_test_cases_to_scenarios(
                trusted.cases, req.all_scenario_ids, req.link, req.scenario_files
            )
        except DriverError as exc:
            return self._fail(
                req,
                _Failure(
                    "acceptance",
                    _cut_short_reason(req, str(exc), trusted.cases, output),
                    cases=trusted.cases,
                    output=output,
                    controls=controls,
                    acceptance=acceptance,
                ),
            )

        scenario_results: dict[str, str] = {sid: str(tc.status) for sid, tc in mapped.items()}

        # A refactor has no scenario of its own to turn green: only the greens must stay green.
        target = None if req.role == "refactor" else _scenario_str(req.scenario_id)
        current = mapped.get(target) if target is not None else None
        failure = acceptance_failure(target, req.green_scenario_ids, mapped)
        if failure is not None:
            reason, relevant_cases = failure
            failed = current is not None and current.status != "passed"
            return self._fail(
                req,
                _Failure(
                    "acceptance",
                    reason,
                    cases=relevant_cases,
                    all_cases=trusted.cases,
                    output=output,
                    controls=controls,
                    acceptance=acceptance,
                    scenario_results=scenario_results,
                    failure=(current.failure_kind, current.origin) if failed and current else None,
                ),
            )

        return VerifyResult(
            ok=True,
            controls=controls,
            acceptance=acceptance,
            scenario_results=scenario_results,
        )


def _cut_short_reason(
    req: VerifyAttemptRequest, reason: str, cases: Sequence[TestCaseResult], output: str
) -> str:
    """Why the report doesn't map, with the driver sealed: a case missing was cut short."""
    ids = [_scenario_str(sid) for sid in req.all_scenario_ids]
    present = present_scenarios(cases, ids, req.link)
    return mapping_failure_reason(reason, ids, present, sealed=True, output=output)
