"""Verify this repository as a run would: every control on a clean clone of HEAD.

The controls run in the verification environment built from the profile, with its exact variables,
and the hash of that environment is compared at the end. No agent takes part, which also makes this
the check that `lictor.json` still works outside the developer's own environment.

Usage: uv run python scripts/verify_self.py [control ...]
"""

import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor.adapters.config.loader import load_config, resolve_defences
from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.adapters.runner.subprocess_runner import SubprocessRunner
from lictor.adapters.store.clock import SystemClock
from lictor.adapters.store.jsonl_eventlog import JsonlEventLog
from lictor.adapters.vcs.git_cli import GitCli
from lictor.application.ports import AuditSink, ResultReader
from lictor.application.run_files import verification_tree
from lictor.application.usecases.check_project import (
    CheckControlSpec,
    CheckProject,
    CheckProjectRequest,
)
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.model import CommandOutput
from lictor.domain.rules import describe_score

REPO_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = "acceptance"


@dataclass(frozen=True)
class _EnvironmentRunner:
    """Gives `CheckProject` the same commands a run would execute."""

    environment: VerificationEnvironment

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutput:
        return self.environment.run(cmd, cwd, timeout_s or 0)


def _specs(selected: Sequence[str]) -> list[CheckControlSpec]:
    config = load_config(REPO_ROOT)
    specs = [
        CheckControlSpec(c.id, c.level, c.run, c.report, c.mode, c.empty_exit_codes, c.timeout_s)
        for c in config.controls
    ]
    acceptance = config.acceptance
    specs.append(
        CheckControlSpec(
            ACCEPTANCE, "L3", acceptance.run, acceptance.report, timeout_s=acceptance.timeout_s
        )
    )
    return [spec for spec in specs if not selected or spec.id in selected]


def main(selected: Sequence[str]) -> int:
    config = load_config(REPO_ROOT)
    vcs = GitCli(REPO_ROOT)
    audit = AuditSink(JsonlEventLog(REPO_ROOT / ".lictor" / "verify_self.jsonl"), SystemClock())
    spec = resolve_defences(config).verification
    environment = VerificationEnvironment(vcs, SubprocessRunner(), audit, REPO_ROOT, spec)
    readers: dict[str, ResultReader] = {
        "exit-code": ExitCodeReader(),
        "junit": JUnitReader(),
        "mutation": MutationReader(),
    }
    check = CheckProject(_EnvironmentRunner(environment), readers)

    run_id = f"self-{int(time.time())}"
    environment.ensure(run_id)
    failed: list[str] = []
    with verification_tree(vcs, "HEAD", REPO_ROOT) as tree:
        for control in _specs(selected):
            started = time.monotonic()
            result = check.execute(CheckProjectRequest(project_root=tree, controls=[control]))
            outcome = result.control_results[control.id]
            state = "ok" if outcome.ok else "FALLA"
            score = f": {describe_score(outcome.score)}" if outcome.score else ""
            elapsed = time.monotonic() - started
            print(f"{state:6}{control.id} ({elapsed:.0f} s){score}", flush=True)
            if not outcome.ok:
                print(result.condensed_error or outcome.error, flush=True)
                if control.mode != "measure":
                    failed.append(control.id)

    tampered = environment.ensure(run_id)
    if tampered:
        print(tampered)
        failed.append("L0-entorno")
    print(f"Fallaron: {', '.join(failed)}" if failed else "Todos los controles pasan")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
