"""CheckRed use case for verifying acceptance test driver against red rule."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from lictor.application.events import new_event
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
from lictor.application.spec_history import SpecHistory
from lictor.application.usecases.verify_attempt import IntegritySnapshot, describe_tampering
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.errors import DriverError
from lictor.domain.model import Candidate, CommandOutput, ScenarioId, TestCaseResult
from lictor.domain.rules import (
    Impostors,
    MapLink,
    RedOracle,
    RedVerdict,
    condense_error,
    impostor_problem,
    impostor_source,
    impostor_survivors,
    judge_red,
    map_test_cases_to_scenarios,
    render_full_error,
    suite_crash_reason,
    unmapped_cases,
)

DRIVER_COMMIT_MESSAGE = "chore(lictor): driver de aceptación"

_REGISTER_HINT = (
    "Corrige el test para que compruebe el resultado. Solo si el escenario ya estaba implementado "
    "por un commit anterior, regístralo y relanza: "
    'git commit --allow-empty -m "<mensaje>" --trailer "Scenario: <id>".'
)

RejectionKind = Literal[
    "L0-estado",
    "L0-entorno",
    "suite_caida",
    "mapeo",
    "sin_escenario",
    "regla_del_rojo",
    "impostor",
    "sin_prueba",
    "especificacion_cambiada",
]


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
    scenario_hashes: Mapping[str, str] = field(default_factory=dict)
    scenario_files: Mapping[str, str] = field(default_factory=dict)  # relative to the project
    oracle: RedOracle = field(default_factory=RedOracle)
    link: MapLink = "name"
    impostors: Impostors | None = None


@dataclass(frozen=True)
class _Rejection:
    """Why the driver can't be sealed: what the agent reads and what stays in `runs/`."""

    kind: RejectionKind
    reason: str
    cases: Sequence[TestCaseResult] = ()
    all_cases: Sequence[TestCaseResult] | None = None
    output: str = ""


@dataclass(frozen=True)
class _Registration:
    """What the red check turns green without the agent."""

    reopened: tuple[str, ...] = ()
    collateral: tuple[str, ...] = ()
    new_pending: tuple[str, ...] = ()  # nothing has proved their tests yet

    @property
    def subject(self) -> str:
        if not self.reopened:
            return "verde colateral"
        if not self.collateral:
            return "volver a registrar sin agente"
        return "registrar sin agente"


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
        history: SpecHistory,
        audit: AuditSink,
    ) -> None:
        self.environment = environment
        self.reader = reader
        self.vcs = vcs
        self.history = history
        self.spec_source: SpecSource = history.spec_source
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
            result, registration = self._check_tree(request, step_dir, tree)
            if not result.ok or registration is None:
                return result

            # Project code just ran, and it can reach the environment as well.
            env_problem = self.environment.ensure(request.run_id)
            if env_problem:
                rejection = _Rejection("L0-entorno", env_problem)
                return self._reject_or_raise(request, step_dir, rejection)

            if request.driver_fresh and request.impostors and registration.new_pending:
                impostor = self._check_impostors(
                    request, step_dir, rev, request.impostors, registration.new_pending
                )
                if impostor is not None:
                    return self._reject_or_raise(request, step_dir, impostor)

            commit_sha: str | None = None
            if request.driver_fresh:
                commit_sha = self._seal(request, tree, candidate, step_dir)
            self._advance(request, candidate, registration)
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
    ) -> tuple[CheckRedResult, _Registration | None]:
        out_file = step_dir / "acceptance.xml"
        output, tampered = self._run_acceptance_suite(request, out_file, tree)
        save_output(step_dir / "acceptance.log", output)
        if tampered:
            reason = describe_tampering(tampered)
            if not request.driver_fresh:
                self._reject(request, step_dir, _Rejection("L0-estado", reason), final=True)
                raise DriverError(f"{reason} Ocurrió durante la aceptación.")
            return self._reject(request, step_dir, _Rejection("L0-estado", reason)), None

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
            return self._reject_or_raise(request, step_dir, rejection), None

        ids = [_scenario_str(sid) for sid in request.all_scenario_ids]
        try:
            mapped = map_test_cases_to_scenarios(
                control_res.test_cases, ids, request.link, request.scenario_files
            )
        except DriverError as exc:
            rejection = _Rejection("mapeo", str(exc), control_res.test_cases, output=raw_output)
            return self._reject_or_raise(request, step_dir, rejection), None

        extra = unmapped_cases(control_res.test_cases, mapped)
        if request.driver_fresh and extra:
            names = ", ".join(case.name for case in extra)
            reason = f"El driver tiene tests que no son de ningún escenario: {names}"
            rejection = _Rejection("sin_escenario", reason, extra, output=raw_output)
            return self._reject_or_raise(request, step_dir, rejection), None

        # Git says what was green, and for which specification: the checkpoint has no say.
        classes = self.history.classify({sid: request.scenario_hashes.get(sid, "") for sid in ids})
        verdict = judge_red(mapped, classes, request.oracle, driver_fresh=request.driver_fresh)
        if verdict.ok and verdict.respecified:
            # Only a person knows whether the specification changed in wording or in substance.
            reason = _respecified_reason(verdict.respecified)
            self._reject(
                request, step_dir, _Rejection("especificacion_cambiada", reason), final=True
            )
            raise DriverError(reason)
        judged = _verdict_rejection(request, verdict, mapped, control_res.test_cases, raw_output)
        if judged is not None:
            return self._reject_or_raise(request, step_dir, judged), None
        registration = _Registration(
            reopened=tuple(sid for sid in verdict.register if classes[sid] == "reopened"),
            collateral=tuple(sid for sid in verdict.register if classes[sid] == "new"),
            new_pending=tuple(sid for sid in verdict.pending if classes[sid] == "new"),
        )
        scenario_status = {sid: "pending" if sid in verdict.pending else "green" for sid in ids}
        return CheckRedResult(ok=True, scenario_status=scenario_status), registration

    def _check_impostors(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rev: str,
        impostors: Impostors,
        new_pending: Sequence[str],
    ) -> _Rejection | None:
        """Prove each new test uses what the interface returns: with constants, it must fail.

        Two opposite constants, because a legitimate test may pass with one of them. A run that
        crashed, didn't map or kept a stub proves nothing, and doesn't count.
        """
        runs: list[dict[str, str]] = []
        for index, replacement in enumerate(impostors.replacements, start=1):
            run_dir = step_dir / f"impostor-{index}"
            run_dir.mkdir()
            with verification_tree(self.vcs, rev, request.project_root) as clone:
                self._impersonate(clone, request.oracle, impostors.pattern, replacement)
                output, tampered = self._run_acceptance_suite(
                    request, run_dir / "acceptance.xml", clone
                )
            save_output(run_dir / "acceptance.log", output)
            if tampered:
                return _Rejection("L0-estado", describe_tampering(tampered))
            env_problem = self.environment.ensure(request.run_id)
            if env_problem:
                return _Rejection("L0-entorno", env_problem)
            results, problem = self._impostor_results(request, run_dir, output)
            if problem is not None:
                reason = f"No se pudo construir el impostor {replacement!r}: {problem}"
                return _Rejection("impostor", reason, output=format_output(output))
            runs.append(results)
        survivors = impostor_survivors(list(new_pending), runs)
        if not survivors:
            return None
        constants = ", ".join(repr(r) for r in impostors.replacements)
        return _Rejection(
            "impostor",
            f"Los tests de {', '.join(survivors)} pasan aunque la interfaz devuelva siempre "
            f"una constante ({constants}): ignoran el valor que devuelve la interfaz.",
        )

    def _impersonate(self, clone: Path, oracle: RedOracle, pattern: str, replacement: str) -> None:
        """Replace the stubs in the code the agent may write, never in the driver."""
        for root in oracle.writable:
            for path in sorted((clone / root).rglob("*")):
                rel = path.relative_to(clone).as_posix()
                if not path.is_file() or path.is_symlink() or _inside(rel, oracle.driver):
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                changed, count = impostor_source(text, pattern, replacement)
                if count:
                    path.write_text(changed, encoding="utf-8")

    def _impostor_results(
        self, request: CheckRedRequest, run_dir: Path, output: CommandOutput
    ) -> tuple[dict[str, str], str | None]:
        report = run_dir / "acceptance.xml"
        content = report.read_text(encoding="utf-8", errors="replace") if report.is_file() else ""
        cases = self.reader.read(
            output=content or output.stdout,
            exit_code=output.returncode,
            duration_ms=output.duration_ms,
            allow_empty=False,
        ).test_cases
        crash = suite_crash_reason(output.returncode, request.acceptance_failure_exit_codes, cases)
        try:
            mapped = map_test_cases_to_scenarios(
                cases,
                [_scenario_str(s) for s in request.all_scenario_ids],
                request.link,
                request.scenario_files,
            )
        except DriverError as exc:
            return {}, impostor_problem(crash, str(exc), cases)
        problem = impostor_problem(crash, None, cases)
        return {sid: str(case.status) for sid, case in mapped.items()}, problem

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
            raise DriverError(f"El driver sellado no es válido.\n{result.last_error}")
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
            new_event(
                self.clock,
                "lictor.driver.rejected.v1",
                request.run_id,
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

    def _advance(
        self, request: CheckRedRequest, candidate: Candidate | None, registration: _Registration
    ) -> None:
        """Move the branch once: to the driver, and to what registers on top of it.

        A sealed driver was proved when it was sealed, so a new scenario its test now passes is a
        collateral green. Either way the commit changes no file and the next red check, on a resume
        too, tests it as a green.
        """
        target = candidate
        names = (*registration.reopened, *registration.collateral)
        if names:
            head = self.vcs.get_head_sha()
            base = candidate or Candidate(head, self.vcs.get_tree("HEAD"), head)
            trailers: dict[str, str | list[str]] = {"Scenario": list(names)}
            if registration.collateral:
                trailers["Lictor-Collateral"] = base.sha
            trailers["Lictor-Run"] = request.run_id
            registered = self.vcs.commit_tree(
                base.tree,
                message=f"test({', '.join(names)}): {registration.subject}",
                trailers=trailers,
                parent=base.sha,
                empty=True,
            )
            target = Candidate(registered.sha, registered.tree, base.parent)
            for sid in registration.reopened:
                self._event(request, "lictor.scenario.reregistered.v1", sid, registered.sha)
            for sid in registration.collateral:
                self._event(
                    request, "lictor.scenario.green.v1", sid, registered.sha, collateral=True
                )
        if target is not None:
            self.vcs.advance_head(target.sha, expected=target.parent)

    def _event(
        self, request: CheckRedRequest, kind: str, sid: str, commit: str, **data: object
    ) -> None:
        self.event_log.append(
            new_event(
                self.clock,
                kind,
                request.run_id,
                scenario_id=sid,
                data={"commit": commit, **data},
            )
        )

    def _seal(
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
        commit_sha = candidate.sha if candidate is not None else self.vcs.get_head_sha()

        event = new_event(
            self.clock,
            "lictor.driver.sealed.v1",
            request.run_id,
            data={
                "commit": commit_sha,
                "files": seal_data.get("files", {}),
                "evidence": step_evidence(step_dir, request.project_root),
            },
        )
        self.event_log.append(event)
        return commit_sha


def _verdict_rejection(
    request: CheckRedRequest,
    verdict: RedVerdict,
    mapped: Mapping[str, TestCaseResult],
    all_cases: Sequence[TestCaseResult],
    output: str,
) -> _Rejection | None:
    """Say why the verdict rejects a driver its agent may still fix."""
    if not verdict.ok:
        reason = "\n".join(verdict.problems)
        return _Rejection("regla_del_rojo", reason, verdict.cases, all_cases, output)
    if verdict.unproven:
        # Most likely a test that checks nothing, which the agent can fix.
        reason = _unproven_reason(request, verdict.unproven, mapped)
        cases = tuple(mapped[sid] for sid in verdict.unproven)
        return _Rejection("sin_prueba", reason, cases, all_cases, output)
    return None


def _unproven_reason(
    request: CheckRedRequest, scenarios: Sequence[str], mapped: Mapping[str, TestCaseResult]
) -> str:
    lines = [
        f"El test {mapped[sid].name} del escenario {sid} pasa con un driver nuevo, sin llegar a "
        "la interfaz: tiene que comprobar el resultado que pide su escenario y fallar mientras "
        "la interfaz no esté implementada."
        for sid in scenarios
    ]
    if request.driver_attempts >= request.max_driver_attempts:
        lines.append(_REGISTER_HINT)  # the last attempt: the person who reads the stop decides
    return "\n".join(lines)


def _respecified_reason(scenarios: Sequence[str]) -> str:
    names = ", ".join(scenarios)
    return (
        f"La especificación de {names} cambió y su test pasa con un driver nuevo: nada distingue "
        "una implementación que ya cumple la nueva de un test que no detecta el cambio. Si la "
        "implementación ya la cumple, regístralos y relanza: "
        'git commit --allow-empty -m "<mensaje>" --trailer "Scenario: <id>". '
        "Si no, su test no detecta el cambio: corrígelo."
    )


def _scenario_str(sid: ScenarioId | str) -> str:
    return str(sid.value if isinstance(sid, ScenarioId) else sid)


def _inside(path: str, roots: Sequence[str]) -> bool:
    return any(path == r.strip("/") or path.startswith(f"{r.strip('/')}/") for r in roots)
