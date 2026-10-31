"""The environment that verification runs in, built once per run and out of the cloned tree.

Project code runs with the user's permissions and can reach this directory, so its hash is
compared around every verification. Everything counts, bytecode included: a stale or planted
`.pyc` runs like any other file, and verifying generates none, as the profile's env must ensure.

The commands inherit nothing that could load code: `unset` removes it from what this process
received and `env` then fixes what must hold. `files` are written into the environment before
hashing it, so the tool can reach the project after starting rather than through its startup path.
"""

import json
import os
import shlex
import shutil
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path, PurePath
from typing import NoReturn

from lictor.application.ports import AuditSink, CommandRunner, Vcs
from lictor.application.run_files import save_output, unused_path, verification_tree
from lictor.application.seal import hash_tree
from lictor.domain.errors import EnvironmentUnavailableError
from lictor.domain.model import CommandOutput, Event

ENV_DIR_PLACEHOLDER = "{env_dir}"
_PREPARE_TIMEOUT_S = 1800
_MAX_ERROR_LINES = 20


@dataclass(frozen=True)
class VerificationEnvSpec:
    """What the profile declares: how to build the environment and how to use it."""

    prepare: str = ""
    env: Mapping[str, str] = field(default_factory=dict)
    unset: Sequence[str] = ()
    files: Mapping[str, str] = field(default_factory=dict)


class VerificationEnvironment:
    """Builds the verification environment and tells whether it is still what was built."""

    def __init__(
        self,
        vcs: Vcs,
        runner: CommandRunner,
        audit: AuditSink,
        project_root: Path,
        spec: VerificationEnvSpec,
    ) -> None:
        self.vcs = vcs
        self.runner = runner
        self.audit = audit
        self.project_root = project_root
        self.spec = spec
        self.env_dir = project_root / ".lictor" / "verify-env"
        self.manifest = project_root / ".lictor" / "verify_env.json"

    def command_env(self) -> dict[str, str | None]:
        """Return the variables every verification command runs with; `None` removes one."""
        removed: dict[str, str | None] = {
            name: None
            for name in os.environ
            if any(fnmatchcase(name, pattern) for pattern in self.spec.unset)
        }
        fixed = {
            name: value.replace(ENV_DIR_PLACEHOLDER, str(self.env_dir))
            for name, value in self.spec.env.items()
        }
        return removed | fixed

    def run(self, cmd: Sequence[str], cwd: Path, timeout_s: int) -> CommandOutput:
        """Run a verification command inside this environment."""
        return self.runner.run(cmd, cwd=cwd, timeout_s=timeout_s, env=self.command_env())

    def ensure(self, run_id: str) -> str | None:
        """Build the environment if this run has none, and return why it can't be trusted.

        After a mismatch it is rebuilt, so the next attempt finds it clean.
        """
        if not self.spec.prepare:
            return None
        recorded = self._read_manifest()
        if recorded is None or recorded.get("run_id") != run_id:
            self._build(run_id, "primera verificación")
            return None

        expected = str(recorded.get("hash"))
        actual = hash_tree(self.env_dir)
        if actual == expected:
            return None

        self._emit(run_id, "env.tampered", {"expected": expected, "actual": actual})
        self._build(run_id, "manipulado")
        return f"El entorno de verificación cambió: esperado {expected}, encontrado {actual}"

    def _read_manifest(self) -> dict[str, object] | None:
        try:
            recorded = json.loads(self.manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return recorded if isinstance(recorded, dict) else None

    def _build(self, run_id: str, reason: str) -> None:
        started = time.monotonic()
        self.manifest.unlink(missing_ok=True)
        shutil.rmtree(self.env_dir, ignore_errors=True)

        tokens = shlex.split(self.spec.prepare.replace(ENV_DIR_PLACEHOLDER, str(self.env_dir)))
        with verification_tree(self.vcs, "HEAD") as tree:
            output = self.runner.run(
                tokens, cwd=tree, timeout_s=_PREPARE_TIMEOUT_S, env=self.command_env()
            )

        log = unused_path(self.project_root / ".lictor" / "runs" / run_id / "env" / "build.log")
        save_output(log, output)
        if output.returncode != 0:
            tail = "\n".join(output.stderr.strip().splitlines()[-_MAX_ERROR_LINES:])
            self._fail(
                run_id, f"No se pudo preparar el entorno de verificación: {tail}", output.returncode
            )

        self._install_files(run_id)
        env_hash = hash_tree(self.env_dir)
        self.manifest.write_text(json.dumps({"run_id": run_id, "hash": env_hash}), encoding="utf-8")
        duration_ms = int((time.monotonic() - started) * 1000)
        self._emit(
            run_id, "env.built", {"hash": env_hash, "reason": reason, "duration_ms": duration_ms}
        )

    def _install_files(self, run_id: str) -> None:
        for pattern, content in self.spec.files.items():
            target = PurePath(pattern)
            parents = sorted(self.env_dir.glob(str(target.parent)))
            if len(parents) != 1:
                self._fail(
                    run_id,
                    f"El entorno de verificación no tiene un único destino para '{pattern}': "
                    f"hay {len(parents)}",
                )
            (parents[0] / target.name).write_text(content, encoding="utf-8")

    def _fail(self, run_id: str, message: str, exit_code: int | None = None) -> NoReturn:
        self._emit(run_id, "env.failed", {"exit_code": exit_code, "error": message})
        raise EnvironmentUnavailableError(message)

    def _emit(self, run_id: str, name: str, data: dict[str, object]) -> None:
        self.audit.event_log.append(
            Event(
                id=uuid.uuid4().hex,
                type=f"lictor.{name}.v1",
                source="lictor/0.1.0",
                time=self.audit.clock.now_iso(),
                runid=run_id,
                data=data,
            )
        )
