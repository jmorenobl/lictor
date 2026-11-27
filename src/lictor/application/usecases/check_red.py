"""CheckRed use case for verifying acceptance test driver against red rule."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from lictor.application.events import new_event
from lictor.application.ports import AuditSink, ReportReader, SpecSource, Vcs
from lictor.application.run_files import (
    DRIVER_OWNER,
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
from lictor.application.spec_history import SpecHistory
from lictor.application.usecases.verify_attempt import IntegritySnapshot, describe_tampering
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.driver_measures import (
    DriverMeasures,
    interface_measures,
    measures_record,
    red_check_phase,
    red_check_timing,
    red_measures,
    stub_measures,
)
from lictor.domain.errors import DriverError
from lictor.domain.gherkin_proof import GherkinFacts, gherkin_facts, static_facts, to_change
from lictor.domain.interface_proof import (
    ALTERED_STEPS,
    InterfaceImpostor,
    InterfaceProof,
    interface_proofs,
    person_notes,
    unproved_reason,
    unreadable,
    weighed_proof,
    weighs_the_gherkin,
    without_impostor_reason,
)
from lictor.domain.model import Candidate, CommandOutput, ScenarioId, TestCaseResult
from lictor.domain.reports import JUnitReport
from lictor.domain.rules import (
    NOT_IMPLEMENTED,
    Impostors,
    MapLink,
    RedOracle,
    RedVerdict,
    StubConstant,
    condense_error,
    describe_suppressions,
    impostor_problem,
    impostor_source,
    judge_red,
    map_test_cases_to_scenarios,
    mapping_failure_reason,
    present_scenarios,
    registration_subject,
    render_full_error,
    stub_lines,
    suppression_matches,
    unmapped_cases,
)
from lictor.domain.stub_proof import (
    STUB_LIMITS_NOTE,
    StubProof,
    stub_proof,
    stub_reason,
    stub_verdict,
)
from lictor.domain.verdicts import Crashed, Execution, report_cases, trust

DRIVER_COMMIT_MESSAGE = "chore(lictor): driver de aceptación"

_REGISTER_HINT = (
    "Corrige el test para que compruebe el resultado. Solo si el código ya cumplía el escenario "
    "antes de esta ejecución, regístralo tú y relanza: `lictor register {ids}`. Al adoptar Lictor "
    "en un proyecto con código, hazlo antes de la primera ejecución."
)

# What no retry of the driver can change: the impostor can't alter what the interface returns, or
# a run of it told nothing. Neither says the test is wrong, so this doesn't either.
_UNDECIDED_HINT = (
    "Otro intento del driver no lo cambiaría. Si el código ya cumplía estos escenarios, "
    "regístralos tú y relanza: `lictor register {ids}`. Si no, revisa antes su test: nada ha "
    "probado que compruebe el resultado."
)

# The same, when some test only proved it reads its scenario's value, or the code raised for a value
# the impostor altered: such a test may compare the value with what a Given stored, so nothing
# invites to register it before reading it (ADR-0016).
_READ_THEM_HINT = (
    "Lee antes esos tests: nada ha probado que comparen lo que devuelve el código con el valor "
    "del escenario."
)
_NO_RETRY = "Otro intento del driver no lo cambiaría."

PROOF_TRAILER = "Lictor-Proof"
INTERFACE_PROOF = "interfaz"  # the trailer's value: the impostor of the interface proved it

_OWN_GATES = ("L0-estado", "L0-supresiones")  # rejections the agent reads by their gate

RejectionKind = Literal[
    "L0-estado",
    "L0-supresiones",
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
    acceptance_timeout_s: int = 600
    error_max_chars: int = 4000
    acceptance_failure_exit_codes: Sequence[int] = (1,)
    tree: str | None = None
    scenario_hashes: Mapping[str, str] = field(default_factory=dict)
    scenario_files: Mapping[str, str] = field(default_factory=dict)  # relative to the project
    oracle: RedOracle = field(default_factory=RedOracle)
    link: MapLink = "name"
    impostors: Impostors | None = None
    interface_impostor: InterfaceImpostor | None = None
    suppressions: Sequence[str] = ()
    banned_patterns: Sequence[str] = ()


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
    pending_kinds: Mapping[str, str] = field(default_factory=dict)  # how each pending one failed
    pending_names: Mapping[str, str] = field(default_factory=dict)  # the test of each, to name it
    # New, passing with a fresh driver: the impostor of the interface may prove them (ADR-0011).
    unproven: Mapping[str, TestCaseResult] = field(default_factory=dict)
    already_met: tuple[str, ...] = ()  # those it proved: the code already met them
    red_cases: tuple[TestCaseResult, ...] = ()  # every case of the red, for the full error
    red_output: str = ""
    stub_impostors: int | None = None  # stubs the impostors replaced; None if they didn't run
    # Accepted without a proof: the interface found nothing it could alter (ADR-0012).
    stub_unchecked: tuple[str, ...] = ()
    measures: DriverMeasures | None = None  # what the stricter rules would say; a fresh driver's

    @property
    def subject(self) -> str:
        if self.already_met and not (self.reopened or self.collateral):
            return "ya se cumplía"
        if not self.reopened:
            return "verde colateral"
        if not self.collateral:
            return "volver a registrar sin agente"
        return "registrar sin agente"


@dataclass(frozen=True)
class _InterfaceRuns:
    """What the impostor of the interface proved, and what each of its runs reported."""

    proofs: dict[str, InterfaceProof]
    control: Mapping[str, TestCaseResult]  # empty when the run told nothing
    modes: Mapping[str, Mapping[str, TestCaseResult]]
    gherkin: Mapping[str, GherkinFacts] = field(default_factory=dict)  # ADR-0016
    unweighed: Mapping[str, InterfaceProof] = field(default_factory=dict)  # the interface's alone


@dataclass(frozen=True)
class _Proving:
    """One proof by the impostors: what is judged, over which candidate, and where its evidence
    goes (`interfaz-<modo>`, `gherkin-<modo>-<n>`; `stubs-` before them over the stubs)."""

    request: CheckRedRequest
    step_dir: Path
    rev: str
    impostor: InterfaceImpostor
    judged: "_ToProve"

    def evidence(self, impostor: str, run: str) -> Path:
        prefix = impostor if self.judged.stubs_at is None else f"stubs-{impostor}"
        return self.step_dir / f"{prefix}-{run}"


@dataclass(frozen=True)
class _Told:
    """What one run of the impostors is told: the interface's mode, whether it traces the
    driver's calls, and what the impostor of the Gherkin does (None: it isn't told)."""

    mode: str
    trace: bool = False
    gherkin: Mapping[str, object] | None = None


@dataclass(frozen=True)
class _Ran:
    """Where one run of the impostors left its report, and how it ended."""

    run_dir: Path
    output: CommandOutput
    mode: str


@dataclass(frozen=True)
class _ToProve:
    """What the impostor of the interface judges: these tests, over the stubs at a constant or,
    without one, over the code as it is."""

    scenario_ids: Sequence[str]
    stubs_at: StubConstant | None = None


# Each run of the stubs' impostors: the constant, and what it reported of each scenario.
_StubRuns = list[tuple[str, Mapping[str, TestCaseResult]]]


@dataclass(frozen=True)
class CheckRedResult:
    """Result of CheckRed use case."""

    ok: bool
    scenario_status: dict[str, str] = field(default_factory=dict)
    last_error: str | None = None
    commit_sha: str | None = None
    failed_at: str | None = None


class CheckRed:
    """Use case to verify driver against red rule, seal, and commit."""

    def __init__(
        self,
        environment: VerificationEnvironment,
        reader: ReportReader[JUnitReport],
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
        # Each run of the acceptance in the red check in progress, with its phase (F52).
        self._runs: list[tuple[str, int]] = []
        self._started = self.clock.now()
        self._step_name = ""

    def execute(self, request: CheckRedRequest) -> CheckRedResult:
        """Run acceptance tests, check red rule, and seal driver if fresh and valid."""
        prefix = attempt_prefix(
            request.project_root, request.run_id, DRIVER_OWNER, request.driver_attempts
        )
        step_dir = new_step_dir(prefix.with_name(f"{prefix.name}-red"))
        self._runs = []
        self._started = self.clock.now()
        self._step_name = step_dir.name

        env_problem = self.environment.ensure(request.run_id)
        if env_problem:
            return self._reject_or_raise(request, step_dir, _Rejection("L0-entorno", env_problem))

        suppressed = self._suppressed(request)
        if suppressed is not None:
            return self._reject_or_raise(request, step_dir, suppressed)

        candidate = self._create_candidate(request)
        rev = candidate.sha if candidate else "HEAD"
        with verification_tree(self.vcs, rev, request.project_root) as tree:
            result, registration = self._check_tree(request, step_dir, tree)
            if not result.ok or registration is None:
                return result

            judged = self._judge_beyond_the_red(request, step_dir, rev, tree, registration)
            if isinstance(judged, CheckRedResult):
                return judged
            registration = judged

            commit_sha: str | None = None
            if request.driver_fresh:
                commit_sha = self._seal(request, tree, candidate, step_dir, registration)
            self._advance(request, candidate, registration)
            if not request.driver_fresh:
                self._checked(request, step_dir, result)
        return replace(result, commit_sha=commit_sha)

    def _judge_beyond_the_red(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rev: str,
        tree: Path,
        registration: _Registration,
    ) -> _Registration | CheckRedResult:
        """Judge what the red alone can't: the stubs' impostors, and the interface's (ADR-0011).

        What the stricter rules would say of what they ran is recorded first, whatever follows:
        it matters most in a driver rejected for what they found.
        """
        rejection: _Rejection | None = None
        # Project code just ran, and it can reach the environment as well.
        env_problem = self.environment.ensure(request.run_id)
        if env_problem:
            rejection = _Rejection("L0-entorno", env_problem)
        if rejection is None:
            rejection, registration = self._stubs_step(request, step_dir, rev, tree, registration)
        proofs: dict[str, InterfaceProof] | None = None
        if rejection is None:
            rejection, registration, proofs = self._interface_step(
                request, step_dir, rev, registration
            )
        if registration.measures is not None:
            self._measured(request, step_dir, registration.measures)
        if rejection is not None:
            return self._reject_or_raise(request, step_dir, rejection)
        if registration.unproven:
            proved = self._prove_or_reject(request, step_dir, registration, proofs)
            if isinstance(proved, CheckRedResult):
                return proved
            registration = replace(registration, already_met=proved)
        return registration

    def _stubs_step(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rev: str,
        tree: Path,
        registration: _Registration,
    ) -> tuple[_Rejection | None, _Registration]:
        """Run the stubs' impostors over a fresh driver's new pending tests, and measure them."""
        if not (request.driver_fresh and request.impostors and registration.new_pending):
            return None, registration
        rejection, stubs, runs = self._check_impostors(request, step_dir, rev, tree, registration)
        if rejection is not None or not runs or request.impostors is None:
            return rejection, replace(registration, stub_impostors=stubs)
        judged = self._judge_stubs(request, step_dir, rev, registration, runs)
        if isinstance(judged, _Rejection):
            return judged, replace(registration, stub_impostors=stubs)
        proofs, interface, unchecked = judged
        measures = registration.measures
        if measures is not None:
            found = stub_measures(
                registration.new_pending, runs, request.oracle.driver, proofs, interface
            )
            measures = replace(measures, stubs=found)
        registration = replace(
            registration, stub_impostors=stubs, measures=measures, stub_unchecked=unchecked
        )
        return self._stub_rejection(request, registration, proofs, interface), registration

    def _judge_stubs(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rev: str,
        registration: _Registration,
        runs: _StubRuns,
    ) -> tuple[dict[str, StubProof], dict[str, InterfaceProof], tuple[str, ...]] | _Rejection:
        """Apply C to each new pending test, and send to the interface what doesn't depend on the
        stubs: over the tree with the stubs at their first constant, leaving the stubs unwrapped."""
        constants = request.impostors.replacements if request.impostors else ()
        proofs = {
            sid: stub_proof(
                {name: cases.get(sid) for name, cases in runs}, constants, request.oracle.driver
            )
            for sid in registration.new_pending
        }
        to_interface = [sid for sid, p in proofs.items() if p.outcome == "a_la_interfaz"]
        interface: dict[str, InterfaceProof] = {}
        face = request.interface_impostor
        if to_interface and face is not None:
            ran = self._prove_interface(
                request, step_dir, rev, face, _ToProve(to_interface, stubs_at=constants[0])
            )
            if isinstance(ran, _Rejection):
                return ran
            interface = ran.proofs
        unchecked = tuple(
            sid
            for sid, proof in proofs.items()
            if stub_verdict(proof, interface.get(sid)) == "sin_comprobar"
        )
        return proofs, interface, unchecked

    def _stub_rejection(
        self,
        request: CheckRedRequest,
        registration: _Registration,
        proofs: Mapping[str, StubProof],
        interface: Mapping[str, InterfaceProof],
    ) -> _Rejection | None:
        """Reject the driver for every test C rejected, each with what the agent can change."""
        rejected = [
            sid
            for sid, proof in proofs.items()
            if stub_verdict(proof, interface.get(sid)) == "rechazado"
        ]
        if not rejected:
            return None
        lines = [
            stub_reason(
                sid,
                registration.pending_names.get(sid, sid),
                proofs[sid],
                interface.get(sid),
                request.oracle.writable,
            )
            for sid in rejected
        ]
        if request.driver_attempts >= request.max_driver_attempts:
            lines.append(STUB_LIMITS_NOTE)
        return _Rejection("impostor", "\n".join(lines))

    def _interface_step(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rev: str,
        registration: _Registration,
    ) -> tuple[_Rejection | None, _Registration, dict[str, InterfaceProof] | None]:
        """Run the impostor of the interface over the new tests that pass, and measure them.

        Without one in the profile, or nothing to prove, there are no proofs.
        """
        impostor = request.interface_impostor
        if not registration.unproven or impostor is None:
            return None, registration, None
        ran = self._prove_interface(
            request, step_dir, rev, impostor, _ToProve(list(registration.unproven))
        )
        if isinstance(ran, _Rejection):
            return ran, registration, None
        measures = registration.measures
        if measures is not None:
            found = interface_measures(
                ran.proofs, ran.control, ran.modes, ran.unweighed, ran.gherkin
            )
            measures = replace(measures, interface=found)
        return None, replace(registration, measures=measures), ran.proofs

    def _measured(self, request: CheckRedRequest, step_dir: Path, measures: DriverMeasures) -> None:
        """Record what the stricter rules would say of a fresh driver; they decide nothing yet.

        It names its step without hashing it: the rejection or the seal that follows still writes
        there, and hashes it then.
        """
        self.event_log.append(
            new_event(
                self.clock,
                "lictor.driver.measured.v1",
                request.run_id,
                data={
                    "attempt": request.driver_attempts,
                    "step": relative_to_project(step_dir, request.project_root),
                    **measures_record(measures),
                    **self._timing(),
                },
            )
        )

    def _checked(self, request: CheckRedRequest, step_dir: Path, result: CheckRedResult) -> None:
        """Record a sealed driver that passed, so its evidence is in the journal like any other."""
        self.event_log.append(
            new_event(
                self.clock,
                "lictor.driver.checked.v1",
                request.run_id,
                data={
                    "attempt": request.driver_attempts,
                    "scenario_status": dict(result.scenario_status),
                    "evidence": step_evidence(step_dir, request.project_root),
                    **self._timing(),
                },
            )
        )

    def _suppressed(self, request: CheckRedRequest) -> _Rejection | None:
        """Refuse what a fresh driver adds that L0-supresiones refuses to the implementer (F12).

        The driver is sealed with the oracle, so whatever it leaves stays for every attempt: a
        `sys.path` put in front of the dependencies undoes the profile's defence for all of them.
        A sealed driver isn't judged again: it passed when it was sealed.
        """
        if not request.driver_fresh or request.tree is None:
            return None
        if not request.suppressions and not request.banned_patterns:
            return None
        found = suppression_matches(
            self.vcs.added_lines(request.tree), request.suppressions, request.banned_patterns
        )
        return _Rejection("L0-supresiones", describe_suppressions(found)) if found else None

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
        command, output, tampered = self._run_acceptance_suite(request, out_file, tree)
        save_output(step_dir / "acceptance.log", command, output)
        if tampered:
            reason = describe_tampering(tampered)
            if not request.driver_fresh:
                self._reject(request, step_dir, _Rejection("L0-estado", reason), final=True)
                raise DriverError(f"{reason} Ocurrió durante la aceptación.")
            return self._reject(request, step_dir, _Rejection("L0-estado", reason)), None

        junit = self.reader.read(out_file)
        raw_output = format_output(output)
        execution = Execution.of(output, request.acceptance_timeout_s)
        trusted = trust(junit, execution, request.acceptance_failure_exit_codes)
        if isinstance(trusted, Crashed):
            rejection = _Rejection(
                "suite_caida",
                describe_crash(trusted.reason, raw_output),
                report_cases(junit),
                output=raw_output,
            )
            return self._reject_or_raise(request, step_dir, rejection), None
        cases = trusted.cases

        ids = [_scenario_str(sid) for sid in request.all_scenario_ids]
        try:
            mapped = map_test_cases_to_scenarios(cases, ids, request.link, request.scenario_files)
        except DriverError as exc:
            reason = mapping_failure_reason(
                str(exc),
                ids,
                present_scenarios(cases, ids, request.link),
                sealed=not request.driver_fresh,
                output=raw_output,
            )
            rejection = _Rejection("mapeo", reason, cases, output=raw_output)
            return self._reject_or_raise(request, step_dir, rejection), None

        extra = unmapped_cases(cases, mapped)
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
        judged = _verdict_rejection(request, verdict, mapped, cases, raw_output)
        if judged is not None:
            return self._reject_or_raise(request, step_dir, judged), None
        new_pending = tuple(sid for sid in verdict.pending if classes[sid] == "new")
        registration = _Registration(
            reopened=tuple(sid for sid in verdict.register if classes[sid] == "reopened"),
            collateral=tuple(sid for sid in verdict.register if classes[sid] == "new"),
            new_pending=new_pending,
            pending_kinds={sid: mapped[sid].failure_kind for sid in new_pending},
            pending_names={sid: mapped[sid].name for sid in new_pending},
            unproven={sid: mapped[sid] for sid in verdict.unproven},
            red_cases=tuple(cases),
            red_output=raw_output,
            measures=DriverMeasures(red_measures(verdict.pending, mapped, request.oracle.driver))
            if request.driver_fresh
            else None,
        )
        scenario_status = {sid: "pending" if sid in verdict.pending else "green" for sid in ids}
        return CheckRedResult(ok=True, scenario_status=scenario_status), registration

    def _check_impostors(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rev: str,
        tree: Path,
        registration: _Registration,
    ) -> tuple[_Rejection | None, int, _StubRuns]:
        """Prove each new test uses what the interface returns: with constants, it must fail.

        Without a stub to replace there is nothing to run, and the count says so instead of
        passing it (ADR-0011, point 7). It also gives back what each run reported.
        """
        impostors = request.impostors
        if impostors is None:
            return None, 0, []
        stubs = self._impersonate(tree, request.oracle, impostors.pattern, None)
        if stubs == 0:
            return _without_stubs(registration), 0, []
        rejection, runs = self._run_stub_impostors(
            request, step_dir, rev, impostors, registration.new_pending
        )
        return rejection, stubs, runs

    def _run_stub_impostors(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rev: str,
        impostors: Impostors,
        new_pending: Sequence[str],
    ) -> tuple[_Rejection | None, _StubRuns]:
        """Run the acceptance with each constant in place of the stubs (ADR-0012).

        A run that crashed or didn't map tells nothing and counts for no test; if every one did,
        the driver is rejected. A run that kept a stub is the driver's to fix, as before.
        """
        runs: _StubRuns = []
        problems: list[str] = []
        for index, constant in enumerate(impostors.replacements, start=1):
            run_dir = step_dir / f"impostor-{index}"
            run_dir.mkdir()
            with verification_tree(self.vcs, rev, request.project_root) as clone:
                self._impersonate(clone, request.oracle, impostors.pattern, constant.code)
                command, output, tampered = self._run_acceptance_suite(
                    request, run_dir / "acceptance.xml", clone
                )
            save_output(run_dir / "acceptance.log", command, output)
            if tampered:
                return _Rejection("L0-estado", describe_tampering(tampered)), runs
            env_problem = self.environment.ensure(request.run_id)
            if env_problem:
                return _Rejection("L0-entorno", env_problem), runs
            results, problem, kept = self._impostor_results(request, run_dir, output)
            if kept is not None:
                reason = f"No se pudo construir el impostor {constant.name!r}: {kept}"
                return _Rejection("impostor", reason, output=format_output(output)), runs
            if problem is not None:
                problems.append(f"con {constant.name}: {problem}")
                continue
            runs.append((constant.name, results))
        if not runs:
            reason = "Ninguna ejecución con los stubs sustituidos dio resultado: " + "; ".join(
                problems
            )
            return _Rejection("impostor", reason), runs
        return None, runs

    def _prove_or_reject(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        registration: _Registration,
        proofs: dict[str, InterfaceProof] | None,
    ) -> tuple[str, ...] | CheckRedResult:
        """Register the new scenarios that pass if the impostor proved them all, or reject the
        driver for those it couldn't (ADR-0011); without an impostor, `proofs` is None.

        Nothing is registered unless every one is proved: once sealed, the next red check would
        register an unproved one as a collateral green.
        """
        unproven = registration.unproven
        failed = [sid for sid in unproven if proofs is None or not proofs[sid].proved]
        if not failed:
            return tuple(unproven)
        told = _unproved_text(request, unproven, failed, proofs)
        cases = tuple(unproven[sid] for sid in failed)
        if told.final:
            # No retry of the driver changes what the impostor can tell: a person decides.
            reason = "\n".join([*told.lines, *told.note, told.hint])
            rejection = _Rejection(
                "sin_prueba", reason, cases, registration.red_cases, registration.red_output
            )
            self._reject(request, step_dir, rejection, final=True)
            raise DriverError(reason)
        lines = list(told.lines)
        if request.driver_attempts >= request.max_driver_attempts:
            lines.extend([*told.note, told.hint])
        rejection = _Rejection(
            "sin_prueba", "\n".join(lines), cases, registration.red_cases, registration.red_output
        )
        return self._reject_or_raise(request, step_dir, rejection)

    def _prove_interface(
        self,
        request: CheckRedRequest,
        step_dir: Path,
        rev: str,
        impostor: InterfaceImpostor,
        judged: "_ToProve",
    ) -> _InterfaceRuns | _Rejection:
        """Run the acceptance once per mode of the impostor, each in a clean clone of `rev`.

        With `stubs_at`, each clone has its stubs at that constant, and the impostor is told
        their lines so that it leaves them unwrapped (ADR-0012). The control traces the driver's
        calls and, with the impostor of the Gherkin, numbers what each Then receives: what the
        interface can't prove is then weighed with the scenario's values (ADR-0016).
        """
        proving = _Proving(request, step_dir, rev, impostor, judged)
        runs = self._interface_runs(proving)
        if isinstance(runs, _Rejection):
            return runs
        control_run = runs.pop(impostor.control)
        proofs = interface_proofs(judged.scenario_ids, control_run, runs, request.oracle.driver)
        control_cases = {} if isinstance(control_run, str) else control_run
        modes = {mode: {} if isinstance(run, str) else run for mode, run in runs.items()}
        if impostor.gherkin is None or not control_cases:
            return _InterfaceRuns(proofs, control_cases, modes)
        facts = self._weigh_the_gherkin(proving, proofs, control_cases)
        if isinstance(facts, _Rejection):
            return facts
        weighed = {
            sid: weighed_proof(proof, facts.get(sid), control_cases.get(sid))
            for sid, proof in proofs.items()
        }
        return _InterfaceRuns(weighed, control_cases, modes, facts, proofs)

    def _interface_runs(
        self, proving: "_Proving"
    ) -> dict[str, Mapping[str, TestCaseResult] | str] | _Rejection:
        """The control, which traces and tells the Gherkin's impostor to count, and each mode."""
        impostor = proving.impostor
        ids = [_scenario_str(s) for s in proving.request.all_scenario_ids]
        runs: dict[str, Mapping[str, TestCaseResult] | str] = {}
        for mode in (impostor.control, *impostor.modes):
            control = mode == impostor.control
            told = _Told(mode, trace=control, gherkin={"mode": mode} if control else None)
            ran = self._impostor_run(proving, proving.evidence("interfaz", mode), told)
            if isinstance(ran, _Rejection):
                return ran
            runs[mode] = self._interface_results(proving.request, ran, ids)
        return runs

    def _weigh_the_gherkin(
        self,
        proving: "_Proving",
        proofs: Mapping[str, InterfaceProof],
        control: Mapping[str, TestCaseResult],
    ) -> dict[str, GherkinFacts] | _Rejection:
        """Change each value the Then steps of an unproved test read from the Gherkin, alone and
        once per mode, over those tests only; a second control tells what changes by itself.

        A test with a value its Then never reads needs no run: that is a fact of its code.
        """
        ids = proving.judged.scenario_ids
        facts = {sid: static_facts(control[sid]) for sid in ids if sid in control}
        targets = _gherkin_targets(facts, proofs, control)
        if not targets:
            return facts
        face = proving.impostor
        where = proving.evidence("gherkin", f"{face.control}-bis")
        again = self._gherkin_run(proving, where, {"mode": face.control}, targets)
        if isinstance(again, _Rejection):
            return again
        changed = self._changed_values(proving, targets, control)
        if isinstance(changed, _Rejection):
            return changed
        for sid in targets:
            facts[sid] = gherkin_facts(
                control[sid],
                again.get(sid),
                {key: run.get(sid) for key, run in changed.items()},
                face.modes,
                proving.request.oracle.driver,
            )
        return facts

    def _changed_values(
        self, proving: "_Proving", targets: Sequence[str], control: Mapping[str, TestCaseResult]
    ) -> dict[tuple[str, int], Mapping[str, TestCaseResult]] | _Rejection:
        """One run per value any target can change and per mode, over the targets alone."""
        changed: dict[tuple[str, int], Mapping[str, TestCaseResult]] = {}
        numbers = sorted({n for sid in targets for n in to_change(control[sid])})
        for number, mode in ((n, m) for n in numbers for m in proving.impostor.modes):
            where = proving.evidence("gherkin", f"{mode}-{number}")
            ran = self._gherkin_run(proving, where, {"mode": mode, "target": number}, targets)
            if isinstance(ran, _Rejection):
                return ran
            changed[(mode, number)] = ran
        return changed

    def _gherkin_run(
        self,
        proving: "_Proving",
        run_dir: Path,
        gherkin: Mapping[str, object],
        targets: Sequence[str],
    ) -> Mapping[str, TestCaseResult] | _Rejection:
        """One run of `targets` alone, with the interface in control, tracing, and the Gherkin
        told `gherkin`; a run that told nothing counts for none of them."""
        told = _Told(
            proving.impostor.control, trace=True, gherkin={**gherkin, "targets": list(targets)}
        )
        ran = self._impostor_run(proving, run_dir, told)
        if isinstance(ran, _Rejection):
            return ran
        results = self._interface_results(proving.request, ran, targets)
        return {} if isinstance(results, str) else results

    def _impostor_run(
        self, proving: "_Proving", run_dir: Path, told: "_Told"
    ) -> "_Ran | _Rejection":
        """Run the acceptance in a clean clone of the candidate, with the impostors told `told`."""
        request, impostor = proving.request, proving.impostor
        run_dir.mkdir()
        with verification_tree(self.vcs, proving.rev, request.project_root) as clone:
            stubs = self._stubs_at(clone, request, proving.judged.stubs_at)
            face = {
                "mode": told.mode,
                "writable": [str(clone / root.strip("/")) for root in request.oracle.writable],
                "driver": [str(clone / root.strip("/")) for root in request.oracle.driver],
                "steps": list(ALTERED_STEPS),
                "stubs": {str(clone / rel): lines for rel, lines in stubs.items()},
                "trace": told.trace,
                "root": str(clone),
            }
            env = {impostor.variable: json.dumps(face)}
            if told.gherkin is not None and impostor.gherkin is not None:
                env[impostor.gherkin] = json.dumps(told.gherkin)
            command, output, tampered = self._run_acceptance_suite(
                request, run_dir / "acceptance.xml", clone, env
            )
        save_output(run_dir / "acceptance.log", command, output)
        if tampered:
            return _Rejection("L0-estado", describe_tampering(tampered))
        env_problem = self.environment.ensure(request.run_id)
        if env_problem:
            return _Rejection("L0-entorno", env_problem)
        return _Ran(run_dir, output, told.mode)

    def _interface_results(
        self, request: CheckRedRequest, ran: "_Ran", ids: Sequence[str]
    ) -> Mapping[str, TestCaseResult] | str:
        """What one run of the impostor reported of each of `ids`, or why it told nothing."""
        junit = self.reader.read(ran.run_dir / "acceptance.xml")
        execution = Execution.of(ran.output, request.acceptance_timeout_s)
        trusted = trust(junit, execution, request.acceptance_failure_exit_codes)
        mode = ran.mode
        if isinstance(trusted, Crashed):
            return f"la aceptación con el modo {mode} del impostor no dejó un informe válido: " + (
                trusted.reason
            )
        try:
            return map_test_cases_to_scenarios(
                trusted.cases, list(ids), request.link, request.scenario_files
            )
        except DriverError as exc:
            return f"la aceptación con el modo {mode} del impostor no se pudo emparejar: {exc}"

    def _impersonate(
        self, clone: Path, oracle: RedOracle, pattern: str, replacement: str | None
    ) -> int:
        """Replace the stubs in the code the agent may write, never in the driver.

        Return how many lines matched; with no replacement, only count them.
        """
        total = 0
        for path, _ in _writable_files(clone, oracle):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            changed, count = impostor_source(text, pattern, replacement or "")
            total += count
            if count and replacement is not None:
                path.write_text(changed, encoding="utf-8")
        return total

    def _stubs_at(
        self, clone: Path, request: CheckRedRequest, constant: StubConstant | None
    ) -> dict[str, list[int]]:
        """Put the stubs at `constant`, and say which lines they are, by path in the project."""
        if constant is None or request.impostors is None:
            return {}
        pattern = request.impostors.pattern
        found: dict[str, list[int]] = {}
        for path, rel in _writable_files(clone, request.oracle):
            try:
                lines = stub_lines(path.read_text(encoding="utf-8"), pattern)
            except UnicodeDecodeError:
                continue
            if lines:
                found[rel] = lines
        self._impersonate(clone, request.oracle, pattern, constant.code)
        return found

    def _impostor_results(
        self, request: CheckRedRequest, run_dir: Path, output: CommandOutput
    ) -> tuple[Mapping[str, TestCaseResult], str | None, str | None]:
        """What one run of a constant reported, why it tells nothing, and a stub it kept."""
        junit = self.reader.read(run_dir / "acceptance.xml")
        cases = report_cases(junit)
        execution = Execution.of(output, request.acceptance_timeout_s)
        trusted = trust(junit, execution, request.acceptance_failure_exit_codes)
        crash = trusted.reason if isinstance(trusted, Crashed) else None
        if crash is not None:
            return {}, crash, None
        kept = impostor_problem(None, None, cases)
        try:
            mapped = map_test_cases_to_scenarios(
                cases,
                [_scenario_str(s) for s in request.all_scenario_ids],
                request.link,
                request.scenario_files,
            )
        except DriverError as exc:
            return {}, str(exc), kept
        return mapped, None, kept

    def _run_acceptance_suite(
        self,
        request: CheckRedRequest,
        out_file: Path,
        tree: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> tuple[list[str], CommandOutput, list[str]]:
        cmd_args = fill_report_paths([(request.acceptance_run, out_file)])

        lictor_dir = request.project_root / ".lictor"
        snapshot = IntegritySnapshot(lictor_dir, request.project_root, self.vcs)
        output = self.environment.run(cmd_args, tree, request.acceptance_timeout_s, extra_env)
        snapshot.verify_and_restore()
        phase = red_check_phase(out_file.parent.name, self._step_name)
        self._runs.append((phase, output.duration_ms))
        return cmd_args, output, snapshot.changes

    def _timing(self) -> dict[str, object]:
        """How long the red check has taken so far, and each of its phases."""
        elapsed = self.clock.now() - self._started
        return red_check_timing(self._runs, int(elapsed.total_seconds() * 1000))

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
            failed_at=rejection.kind if rejection.kind in _OWN_GATES else "red_check",
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
                    **self._timing(),
                    "evidence": step_evidence(step_dir, request.project_root),
                },
            )
        )
        return CheckRedResult(ok=False, last_error=condensed, failed_at=rejection.kind)

    def _advance(
        self, request: CheckRedRequest, candidate: Candidate | None, registration: _Registration
    ) -> None:
        """Move the branch once: to the driver, and to what registers on top of it.

        A sealed driver was proved when it was sealed, so a new scenario its test now passes is a
        collateral green. Either way the commit changes no file and the next red check, on a resume
        too, tests it as a green.
        """
        target = candidate
        names = (*registration.reopened, *registration.collateral, *registration.already_met)
        if names:
            head = self.vcs.get_head_sha()
            base = candidate or Candidate(head, self.vcs.get_tree("HEAD"), head)
            trailers: dict[str, str | list[str]] = {"Scenario": list(names)}
            if registration.collateral or registration.already_met:
                trailers["Lictor-Collateral"] = base.sha
            if registration.already_met:
                trailers[PROOF_TRAILER] = INTERFACE_PROOF
            trailers["Lictor-Run"] = request.run_id
            registered = self.vcs.commit_tree(
                base.tree,
                message=registration_subject(names, registration.subject),
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
            for sid in registration.already_met:
                self._event(
                    request,
                    "lictor.scenario.green.v1",
                    sid,
                    registered.sha,
                    collateral=True,
                    proof="interface",
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
        registration: _Registration,
    ) -> str:
        """Seal what was verified, not what the working tree holds now.

        The event says how many stubs the impostors replaced (none ran when it is 0) and which
        new scenarios the impostor of the interface proved the code already met.
        """
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
                "stub_impostors": registration.stub_impostors,
                "stub_unchecked": list(registration.stub_unchecked),
                "already_met": list(registration.already_met),
                "evidence": step_evidence(step_dir, request.project_root),
                **self._timing(),
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
    return None


def _without_stubs(registration: _Registration) -> _Rejection | None:
    """With no stub to replace, the impostors can't run: say so of a test that fails by one."""
    kept = [
        sid
        for sid in registration.new_pending
        if registration.pending_kinds.get(sid) == NOT_IMPLEMENTED
    ]
    if not kept:
        return None
    return _Rejection(
        "impostor",
        f"Los tests de {', '.join(kept)} fallan por NotImplementedError, pero no hay ningún stub "
        "que sustituir: escribe cada `raise NotImplementedError` en una sola línea",
    )


def _respecified_reason(scenarios: Sequence[str]) -> str:
    names = ", ".join(scenarios)
    ids = " ".join(scenarios)
    return (
        f"La especificación de {names} cambió y su test pasa con un driver nuevo: nada distingue "
        "una implementación que ya cumple la nueva de un test que no detecta el cambio. Si la "
        f"implementación ya la cumple, regístralos tú y relanza: `lictor register {ids}`. "
        "Si no, su test no detecta el cambio: corrígelo."
    )


@dataclass(frozen=True)
class _UnprovedText:
    """What a rejection for unproved tests says: to the agent (`lines`), and to the person once
    no retry is left (`note`, why the impostor couldn't judge some, and `hint`, what to do)."""

    lines: tuple[str, ...]
    note: tuple[str, ...]
    hint: str
    final: bool  # no retry of the driver changes what the impostor can tell


def _unproved_text(
    request: CheckRedRequest,
    unproven: Mapping[str, TestCaseResult],
    failed: Sequence[str],
    proofs: Mapping[str, InterfaceProof] | None,
) -> _UnprovedText:
    ids = " ".join(failed)
    if proofs is None:
        lines = tuple(without_impostor_reason(sid, unproven[sid].name) for sid in failed)
        return _UnprovedText(lines, (), _REGISTER_HINT.format(ids=ids), final=False)
    judged = {sid: proofs[sid] for sid in failed}
    lines = tuple(
        unproved_reason(sid, unproven[sid].name, proof, request.oracle.writable)
        for sid, proof in judged.items()
    )
    final = not any(proof.fixable for proof in judged.values())
    hint = _person_hint(judged, ids, final=final)
    return _UnprovedText(lines, tuple(person_notes(judged)), hint, final)


def _person_hint(judged: Mapping[str, InterfaceProof], ids: str, *, final: bool) -> str:
    """What the person reads last: to register what the code already met, unless some test only
    proved it reads its scenario's value, which a test comparing it with a Given's looks like."""
    if unreadable(judged):
        return f"{_NO_RETRY} {_READ_THEM_HINT}" if final else _READ_THEM_HINT
    return (_UNDECIDED_HINT if final else _REGISTER_HINT).format(ids=ids)


def _gherkin_targets(
    facts: Mapping[str, GherkinFacts],
    proofs: Mapping[str, InterfaceProof],
    control: Mapping[str, TestCaseResult],
) -> list[str]:
    """The tests the scenario's values can still decide: unproved, every value read in their code,
    and some value the impostor of the Gherkin can change."""
    return [
        sid
        for sid, fact in facts.items()
        if weighs_the_gherkin(proofs[sid]) and not fact.unread and to_change(control[sid])
    ]


def _scenario_str(sid: ScenarioId | str) -> str:
    return str(sid.value if isinstance(sid, ScenarioId) else sid)


def _writable_files(tree: Path, oracle: RedOracle) -> list[tuple[Path, str]]:
    """The files the agent may write, outside the driver, with their path in the project."""
    found: list[tuple[Path, str]] = []
    for root in oracle.writable:
        for path in sorted((tree / root).rglob("*")):
            rel = path.relative_to(tree).as_posix()
            if path.is_file() and not path.is_symlink() and not _inside(rel, oracle.driver):
                found.append((path, rel))
    return found


def _inside(path: str, roots: Sequence[str]) -> bool:
    return any(path == r.strip("/") or path.startswith(f"{r.strip('/')}/") for r in roots)
