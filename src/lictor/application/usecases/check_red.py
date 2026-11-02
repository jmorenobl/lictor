"""CheckRed use case for verifying acceptance test driver against red rule."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from lictor.application.ports import AuditSink, ResultReader, SpecSource, Vcs
from lictor.application.run_files import (
    DRIVER_OWNER,
    attempt_prefix,
    describe_crash,
    fill_report_path,
    format_output,
    new_step_dir,
    relative_to_project,
    save_output,
    step_evidence,
    verification_tree,
    write_text,
)
from lictor.application.usecases.verify_attempt import IntegritySnapshot, describe_tampering
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.errors import DriverError
from lictor.domain.model import Candidate, CommandOutput, Event, ScenarioId, TestCaseResult
from lictor.domain.rules import (
    check_red_rule,
    condense_error,
    describe_red_violation,
    map_test_cases_to_scenarios,
    render_full_error,
    suite_crash_reason,
)

DRIVER_COMMIT_MESSAGE = "chore(lictor): driver de aceptación"

_COLLATERAL_HINT = (
    " Si el escenario ya pasa por un commit anterior, regístralo y relanza: "
    'git commit --allow-empty -m "<mensaje>" --trailer "Scenario: <id>"'
)

RejectionKind = Literal["L0-estado", "L0-entorno", "suite_caida", "mapeo", "regla_del_rojo"]


@dataclass(frozen=True)
class CheckRedRequest:
    """Request payload for CheckRed use case."""

    project_root: Path
    run_id: str
    acceptance_run: str
    driver_fresh: bool
    driver_attempts: int
    max_driver_attempts: int
    driver_paths: Sequence[str]
    all_scenario_ids: Sequence[ScenarioId | str]
    acceptance_report: str = "junit"
    acceptance_timeout_s: int = 600
    error_max_chars: int = 4000
    acceptance_failure_exit_codes: Sequence[int] = (1,)
    tree: str | None = None


@dataclass(frozen=True)
class _Rejection:
    """Why the driver can't be sealed: what the agent reads and what stays in `runs/`."""

    kind: RejectionKind
    reason: str
    cases: Sequence[TestCaseResult] = ()
    all_cases: Sequence[TestCaseResult] | None = None
    output: str = ""


@dataclass(frozen=True)
class CheckRedResult:
    """Result of CheckRed use case."""

    ok: bool
    scenario_status: dict[str, str] = field(default_factory=dict)
    last_error: str | None = None
    commit_sha: str | None = None


class CheckRed:
    """Use case to verify driver against red rule, seal, and commit."""

    def __init__(
        self,
        environment: VerificationEnvironment,
        reader: ResultReader,
        vcs: Vcs,
        spec_source: SpecSource,
        audit: AuditSink,
    ) -> None:
        self.environment = environment
        self.reader = reader
        self.vcs = vcs
        self.spec_source = spec_source
        self.event_log = audit.event_log
        self.clock = audit.clock

    def execute(self, request: CheckRedRequest) -> CheckRedResult:
        """Run acceptance tests, check red rule, and seal driver if fresh and valid."""
        prefix = attempt_prefix(
            request.project_root, request.run_id, DRIVER_OWNER, request.driver_attempts
        )
        step_dir = new_step_dir(prefix.with_name(f"{prefix.name}-red"))

        env_problem = self.environment.ensure(request.run_id)
        if env_problem:
            return self._reject_or_raise(request, step_dir, _Rejection("L0-entorno", env_problem))

        candidate = self._create_candidate(request)
        rev = candidate.sha if candidate else "HEAD"
        with verification_tree(self.vcs, rev, request.project_root) as tree:
            result, sealable = self._check_tree(request, step_dir, tree)
            if not result.ok:
                return result

            # Project code just ran, and it can reach the environment as well.
            env_problem = self.environment.ensure(request.run_id)
            if env_problem:
                rejection = _Rejection("L0-entorno", env_problem)
                return self._reject_or_raise(request, step_dir, rejection)

            commit_sha: str | None = None
            if sealable:
                commit_sha = self._seal_and_advance(request, tree, candidate, step_dir)
        return replace(result, commit_sha=commit_sha)

    def _create_candidate(self, request: CheckRedRequest) -> Candidate | None:
        """Take the fresh driver from what the agent left, unless HEAD already holds it.

        That is the case after a crash that left it committed, and when the driver needed no change.
        """
        if not request.driver_fresh or request.tree is None:
            return None
        if self.vcs.get_tree("HEAD") == request.tree:
            return None
        return self.vcs.commit_tree(
            request.tree,
            message=DRIVER_COMMIT_MESSAGE,
            trailers={"Lictor-Run": request.run_id},
        )

    def _check_tree(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        tree: Path,
    ) -> tuple[CheckRedResult, bool]:
        out_file = step_dir / "acceptance.xml"
        output, tampered = self._run_acceptance_suite(request, out_file, tree)
        save_output(step_dir / "acceptance.log", output)
        if tampered:
            reason = describe_tampering(tampered)
            if not request.driver_fresh:
                self._reject(request, step_dir, _Rejection("L0-estado", reason), final=True)
                raise DriverError(f"{reason} Ocurrió durante la aceptación.")
            return self._reject(request, step_dir, _Rejection("L0-estado", reason)), False

        content = ""
        if request.acceptance_report == "junit" and out_file.is_file():
            content = out_file.read_text(encoding="utf-8", errors="replace")

        control_res = self.reader.read(
            output=content if content else output.stdout,
            exit_code=output.returncode,
            duration_ms=output.duration_ms,
            allow_empty=False,
        )

        raw_output = format_output(output)
        crash = suite_crash_reason(
            output.returncode, request.acceptance_failure_exit_codes, control_res.test_cases
        )
        if crash is not None:
            rejection = _Rejection(
                "suite_caida",
                describe_crash(crash, control_res, raw_output),
                control_res.test_cases,
                output=raw_output,
            )
            return self._reject_or_raise(request, step_dir, rejection), False

        try:
            mapped = map_test_cases_to_scenarios(
                control_res.test_cases,
                request.all_scenario_ids,
            )
        except DriverError as exc:
            rejection = _Rejection("mapeo", str(exc), control_res.test_cases, output=raw_output)
            return self._reject_or_raise(request, step_dir, rejection), False

        scenario_results: dict[str, str] = {sid: str(tc.status) for sid, tc in mapped.items()}
        trailers = set(self.vcs.get_trailers("Scenario"))
        green_ids = [
            str(sid.value if isinstance(sid, ScenarioId) else sid)
            for sid in request.all_scenario_ids
            if str(sid.value if isinstance(sid, ScenarioId) else sid) in trailers
        ]

        if not check_red_rule(scenario_results, green_ids):
            reason, offenders = describe_red_violation(mapped, green_ids)
            rejection = _Rejection(
                "regla_del_rojo",
                reason,
                offenders,
                all_cases=control_res.test_cases,
                output=raw_output,
            )
            return self._reject_or_raise(request, step_dir, rejection), False

        scenario_status = {
            str(sid.value if isinstance(sid, ScenarioId) else sid): (
                "green"
                if str(sid.value if isinstance(sid, ScenarioId) else sid) in green_ids
                else "pending"
            )
            for sid in request.all_scenario_ids
        }
        return CheckRedResult(ok=True, scenario_status=scenario_status), request.driver_fresh

    def _run_acceptance_suite(
        self,
        request: CheckRedRequest,
        out_file: Path,
        tree: Path,
    ) -> tuple[CommandOutput, list[str]]:
        cmd_args = fill_report_path(request.acceptance_run, out_file)

        lictor_dir = request.project_root / ".lictor"
        snapshot = IntegritySnapshot(lictor_dir, request.project_root, self.vcs)
        output = self.environment.run(cmd_args, tree, request.acceptance_timeout_s)
        snapshot.verify_and_restore()
        return output, snapshot.changes

    def _reject_or_raise(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rejection: _Rejection,
    ) -> CheckRedResult:
        """Give a fresh driver another try, or stop: nobody can fix a sealed or exhausted one."""
        exhausted = request.driver_attempts >= request.max_driver_attempts
        final = not request.driver_fresh or exhausted
        result = self._reject(request, step_dir, rejection, final=final)
        if not request.driver_fresh:
            hint = _COLLATERAL_HINT if rejection.kind == "regla_del_rojo" else ""
            raise DriverError(f"El driver sellado no es válido.{hint}\n{result.last_error}")
        if exhausted:
            raise DriverError(
                f"Driver rechazado tras {request.driver_attempts} intentos.\n{result.last_error}"
            )
        return result

    def _reject(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rejection: _Rejection,
        *,
        final: bool = False,
    ) -> CheckRedResult:
        """Leave the reason for the next attempt, in the journal and in full in `runs/`."""
        all_cases = rejection.cases if rejection.all_cases is None else rejection.all_cases
        error_path = step_dir / "error.txt"
        write_text(
            error_path,
            render_full_error("red_check", rejection.reason, all_cases, rejection.output),
        )
        condensed = condense_error(
            failed_at=rejection.kind if rejection.kind == "L0-estado" else "red_check",
            raw_error=rejection.reason,
            test_cases=rejection.cases,
            max_chars=request.error_max_chars,
        )
        self.event_log.append(
            Event(
                id=uuid.uuid4().hex,
                type="lictor.driver.rejected.v1",
                source="lictor/0.1.0",
                time=self.clock.now_iso(),
                runid=request.run_id,
                scenarioid=None,
                data={
                    "attempt": request.driver_attempts,
                    "reason": rejection.kind,
                    "final": final,
                    "error": condensed,
                    "error_path": relative_to_project(error_path, request.project_root),
                    "evidence": step_evidence(step_dir, request.project_root),
                },
            )
        )
        return CheckRedResult(ok=False, last_error=condensed)

    def _seal_and_advance(
        self,
        request: CheckRedRequest,
        tree: Path,
        candidate: Candidate | None,
        step_dir: Path,
    ) -> str:
        """Seal what was verified, not what the working tree holds now."""
        seal_data = self.spec_source.write_seal(
            project_root=request.project_root,
            run_id=request.run_id,
            driver_paths=request.driver_paths,
            source_root=tree,
        )

        if candidate is None:
            commit_sha = self.vcs.get_head_sha()
        else:
            self.vcs.advance_head(candidate.sha, expected=candidate.parent)
            commit_sha = candidate.sha

        event = Event(
            id=uuid.uuid4().hex,
            type="lictor.driver.sealed.v1",
            source="lictor/0.1.0",
            time=self.clock.now_iso(),
            runid=request.run_id,
            scenarioid=None,
            data={
                "commit": commit_sha,
                "files": seal_data.get("files", {}),
                "evidence": step_evidence(step_dir, request.project_root),
            },
        )
        self.event_log.append(event)
        return commit_sha
