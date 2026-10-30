"""CheckProject use case for verifying project controls and acceptance."""

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from lictor.application.ports import CommandRunner, ResultReader
from lictor.domain.model import ControlResult
from lictor.domain.rules import condense_error


@dataclass(frozen=True)
class CheckControlSpec:
    """Specification for a verification control."""

    id: str
    level: str
    run: str
    report: str = "exit-code"
    mode: str = "block"
    allow_empty: bool = False
    timeout_s: int = 600


@dataclass(frozen=True)
class CheckAcceptanceSpec:
    """Specification for acceptance tests."""

    run: str
    report: str = "junit"
    timeout_s: int = 600


@dataclass(frozen=True)
class CheckProjectRequest:
    """Request payload for CheckProject use case."""

    project_root: Path
    controls: Sequence[CheckControlSpec]
    acceptance: CheckAcceptanceSpec | None = None
    levels: Sequence[str] | None = None
    error_max_chars: int = 4000


@dataclass(frozen=True)
class CheckProjectResult:
    """Result of CheckProject use case."""

    ok: bool
    failed_at: str | None = None
    control_results: dict[str, ControlResult] = field(default_factory=dict)
    condensed_error: str | None = None


@dataclass(frozen=True)
class _RunStepParams:
    project_root: Path
    command_template: str
    report_format: str
    timeout_s: int
    allow_empty: bool
    step_id: str


class CheckProject:
    """Use case to run controls and acceptance suite without AI agent or graph."""

    def __init__(
        self,
        runner: CommandRunner,
        readers: Mapping[str, ResultReader],
    ) -> None:
        self.runner = runner
        self.readers = readers

    def execute(self, req: CheckProjectRequest) -> CheckProjectResult:
        """Run all matching controls and acceptance tests."""
        requested_levels = (
            {lvl.strip().upper() for lvl in req.levels} if req.levels is not None else None
        )

        filtered_controls = [
            c
            for c in req.controls
            if requested_levels is None or c.level.strip().upper() in requested_levels
        ]

        control_results: dict[str, ControlResult] = {}
        for ctrl in filtered_controls:
            c_res = self._run_command(
                _RunStepParams(
                    project_root=req.project_root,
                    command_template=ctrl.run,
                    report_format=ctrl.report,
                    timeout_s=ctrl.timeout_s,
                    allow_empty=ctrl.allow_empty,
                    step_id=ctrl.id,
                )
            )
            control_results[ctrl.id] = c_res
            if not c_res.ok and ctrl.mode != "measure":
                condensed = condense_error(
                    failed_at=ctrl.id,
                    raw_error=c_res.error or "Control falló",
                    test_cases=c_res.test_cases,
                    max_chars=req.error_max_chars,
                )
                return CheckProjectResult(
                    ok=False,
                    failed_at=ctrl.id,
                    control_results=control_results,
                    condensed_error=condensed,
                )

        should_run_acceptance = req.acceptance is not None and (
            requested_levels is None or "L3" in requested_levels or "ACCEPTANCE" in requested_levels
        )

        if should_run_acceptance and req.acceptance is not None:
            acc_res = self._run_command(
                _RunStepParams(
                    project_root=req.project_root,
                    command_template=req.acceptance.run,
                    report_format=req.acceptance.report,
                    timeout_s=req.acceptance.timeout_s,
                    allow_empty=False,
                    step_id="acceptance",
                )
            )
            control_results["acceptance"] = acc_res
            if not acc_res.ok:
                condensed = condense_error(
                    failed_at="acceptance",
                    raw_error=acc_res.error or "Aceptación falló",
                    test_cases=acc_res.test_cases,
                    max_chars=req.error_max_chars,
                )
                return CheckProjectResult(
                    ok=False,
                    failed_at="acceptance",
                    control_results=control_results,
                    condensed_error=condensed,
                )

        return CheckProjectResult(ok=True, control_results=control_results)

    def _run_command(self, params: _RunStepParams) -> ControlResult:
        runs_dir = params.project_root / ".lictor" / "runs" / "check"
        runs_dir.mkdir(parents=True, exist_ok=True)
        out_file = runs_dir / f"{params.step_id}.xml"

        tokens = shlex.split(params.command_template)
        cmd_args = [tok.replace("{out}", str(out_file)) for tok in tokens]

        cmd_out = self.runner.run(cmd=cmd_args, cwd=params.project_root, timeout_s=params.timeout_s)

        reader = self.readers.get(params.report_format)
        if reader is None:
            return ControlResult(ok=False, error=f"No reader for format: {params.report_format}")

        if params.report_format == "junit":
            content = str(out_file) if "{out}" in params.command_template else cmd_out.stdout
        else:
            content = f"{cmd_out.stdout}\n{cmd_out.stderr}".strip()

        return reader.read(
            output=content,
            exit_code=cmd_out.returncode,
            duration_ms=cmd_out.duration_ms,
            allow_empty=params.allow_empty,
        )
