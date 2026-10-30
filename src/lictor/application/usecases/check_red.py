"""CheckRed use case for verifying acceptance test driver against red rule."""

import shlex
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from lictor.application.ports import (
    Clock,
    CommandRunner,
    EventLog,
    ResultReader,
    SpecSource,
    Vcs,
)
from lictor.application.usecases.verify_attempt import IntegritySnapshot
from lictor.domain.errors import DriverError
from lictor.domain.model import CommandOutput, ControlResult, Event, ScenarioId
from lictor.domain.rules import check_red_rule, condense_error, map_test_cases_to_scenarios


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
        runner: CommandRunner,
        reader: ResultReader,
        vcs: Vcs,
        spec_source: SpecSource,
        *,
        event_log: EventLog,
        clock: Clock,
    ) -> None:
        self.runner = runner
        self.reader = reader
        self.vcs = vcs
        self.spec_source = spec_source
        self.event_log = event_log
        self.clock = clock

    def execute(self, request: CheckRedRequest) -> CheckRedResult:
        """Run acceptance tests, check red rule, and seal driver if fresh and valid."""
        lictor_dir = request.project_root / ".lictor"
        runs_dir = lictor_dir / "runs" / request.run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        out_file = runs_dir / "acceptance.xml"

        output, integrity_ok = self._run_acceptance_suite(request, out_file, lictor_dir)
        if not integrity_ok:
            if not request.driver_fresh:
                raise DriverError(
                    "El estado en .lictor/ fue modificado indebidamente durante la aceptación"
                )
            condensed = condense_error(
                failed_at="L0-estado",
                raw_error="El estado en .lictor/ fue modificado indebidamente",
                max_chars=request.error_max_chars,
            )
            return CheckRedResult(ok=False, last_error=condensed)

        content = ""
        if request.acceptance_report == "junit" and out_file.is_file():
            content = out_file.read_text(encoding="utf-8", errors="replace")

        control_res = self.reader.read(
            output=content if content else output.stdout,
            exit_code=output.returncode,
            duration_ms=output.duration_ms,
            allow_empty=False,
        )

        try:
            mapped = map_test_cases_to_scenarios(
                control_res.test_cases,
                request.all_scenario_ids,
            )
        except DriverError as exc:
            if not request.driver_fresh:
                raise DriverError(f"Driver sellado inválido en línea base: {exc}") from exc
            if request.driver_attempts >= request.max_driver_attempts:
                raise DriverError(
                    f"Driver inválido tras agotar {request.max_driver_attempts} intentos: {exc}"
                ) from exc
            condensed = condense_error(
                failed_at="red_check",
                raw_error=str(exc),
                test_cases=control_res.test_cases,
                max_chars=request.error_max_chars,
            )
            return CheckRedResult(ok=False, last_error=condensed)

        scenario_results: dict[str, str] = {sid: str(tc.status) for sid, tc in mapped.items()}
        trailers = set(self.vcs.get_trailers("Scenario"))
        green_ids = [
            str(sid.value if isinstance(sid, ScenarioId) else sid)
            for sid in request.all_scenario_ids
            if str(sid.value if isinstance(sid, ScenarioId) else sid) in trailers
        ]

        if not check_red_rule(scenario_results, green_ids):
            return self._handle_red_rule_failure(request, output, control_res)

        commit_sha: str | None = None
        if request.driver_fresh:
            commit_sha = self._seal_and_commit(request)

        scenario_status = {
            str(sid.value if isinstance(sid, ScenarioId) else sid): (
                "green"
                if str(sid.value if isinstance(sid, ScenarioId) else sid) in green_ids
                else "pending"
            )
            for sid in request.all_scenario_ids
        }
        return CheckRedResult(
            ok=True,
            scenario_status=scenario_status,
            commit_sha=commit_sha,
        )

    def _run_acceptance_suite(
        self,
        request: CheckRedRequest,
        out_file: Path,
        lictor_dir: Path,
    ) -> tuple[CommandOutput, bool]:
        raw_cmd = request.acceptance_run
        parsed_tokens = shlex.split(raw_cmd)
        cmd_args = [tok.replace("{out}", str(out_file)) for tok in parsed_tokens]

        snapshot = IntegritySnapshot(lictor_dir, request.project_root)
        output = self.runner.run(
            cmd=cmd_args,
            cwd=request.project_root,
            timeout_s=request.acceptance_timeout_s,
        )
        return output, snapshot.verify_and_restore()

    def _seal_and_commit(self, request: CheckRedRequest) -> str:
        seal_data = self.spec_source.write_seal(
            project_root=request.project_root,
            run_id=request.run_id,
            driver_paths=request.driver_paths,
        )

        if (
            not self.vcs.status()
            and self.vcs.get_head_message() == "chore(lictor): driver de aceptación"
        ):
            commit_sha = self.vcs.get_head_sha()
        else:
            commit_sha = self.vcs.commit(message="chore(lictor): driver de aceptación")

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
            },
        )
        self.event_log.append(event)
        return commit_sha

    def _handle_red_rule_failure(
        self,
        request: CheckRedRequest,
        output: CommandOutput,
        control_res: ControlResult,
    ) -> CheckRedResult:
        if not request.driver_fresh:
            raise DriverError("El driver sellado falló la comprobación de línea base")

        if request.driver_attempts >= request.max_driver_attempts:
            raise DriverError(
                f"Driver falló la regla del rojo tras {request.driver_attempts} intentos"
            )

        condensed = condense_error(
            failed_at="red_check",
            raw_error=output.stdout + "\n" + output.stderr,
            test_cases=control_res.test_cases,
            max_chars=request.error_max_chars,
        )
        return CheckRedResult(ok=False, last_error=condensed)
