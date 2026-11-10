"""The environment that verification runs in, built once per run and out of the cloned tree.

Project code runs with the user's permissions and can reach this directory, so its hash is
compared around every verification. Everything counts, bytecode included: a stale or planted
`.pyc` runs like any other file, and verifying generates none, as the profile's env must ensure.

The commands start from an empty environment: only the names in `inherit` pass, and `env` then
fixes what must hold, so whatever nobody listed stays out. Each one gets a new, empty home, so no
user configuration decides a control and nothing a previous command wrote there survives. `files`
are copied into the environment before hashing it, to the directory `files_dir_command` prints, so
the tool can reach the project after starting rather than through its startup path.

`prepare` needs what the commands must not have: network settings and credentials pass through
`prepare_inherit`, which the commands never see. It has an empty home as well, so what it does need
from the user's is explicit: `prepare_ask` asks the tool, with the caller's environment, for values
that would otherwise follow the home, and `prepare_home_files` points to files that live in it.

What the tool loads is decided once, when building: `plugins_command` lists what the environment
has installed, the list goes to the manifest and every later check asks again and compares. The
commands get it from memory and not from the manifest, because project code runs between them and
can reach that file.
"""

import contextlib
import json
import os
import re
import shlex
import shutil
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from lictor.application.events import new_event
from lictor.application.ports import AuditSink, CommandRunner, Vcs
from lictor.application.run_files import save_output, scratch_dir, unused_path, verification_tree
from lictor.application.seal import hash_tree
from lictor.domain.errors import EnvironmentUnavailableError
from lictor.domain.model import CommandOutput

ENV_DIR_PLACEHOLDER = "{env_dir}"
HOME_DIR_PLACEHOLDER = "{home_dir}"
_PREPARE_TIMEOUT_S = 1800
_QUESTION_TIMEOUT_S = 60
_MAX_ERROR_LINES = 20
_MODULE_NAME = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*")


@dataclass(frozen=True)
class VerificationEnvSpec:
    """What the profile declares: how to build the environment and how to use it."""

    prepare: str = ""
    prepare_inherit: Sequence[str] = ()
    prepare_ask: Mapping[str, str] = field(default_factory=dict)
    prepare_home_files: Mapping[str, str] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    inherit: Sequence[str] = ()
    files_dir_command: str = ""
    files: Sequence[Path] = ()
    plugin_variable: str = ""
    plugins: Sequence[str] = ()
    plugins_command: str = ""


@dataclass(frozen=True)
class EnvironmentRunner:
    """A `CommandRunner` whose commands run inside an environment, as verification runs them.

    The environment decides the variables: whatever a caller passes as `env` is not used.
    """

    environment: "VerificationEnvironment"

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutput:
        return self.environment.run(cmd, cwd, timeout_s)


@dataclass(frozen=True)
class EnvironmentPlace:
    """Where an environment lives, which commit it is built from, and where its build log goes.

    Without `log_dir`, the log is the run's, under `runs/<run_id>/env/`.
    """

    env_dir: Path
    manifest: Path
    rev: str = "HEAD"
    log_dir: Path | None = None


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
        self._installed_plugins: list[str] = []
        self._move_to(
            EnvironmentPlace(
                project_root / ".lictor" / "verify-env",
                project_root / ".lictor" / "verify_env.json",
            )
        )

    def _move_to(self, place: EnvironmentPlace) -> None:
        self.place = place
        self.env_dir = place.env_dir
        self.manifest = place.manifest

    @contextlib.contextmanager
    def throwaway(self, rev: str, log_dir: Path) -> Iterator["VerificationEnvironment"]:
        """Yield an environment for `rev`, apart from any run's and gone afterwards.

        For the commands that aren't a run, `doctor` and `check`: a paused run keeps its own, and
        `ensure` builds this one the first time. Its build log goes to `log_dir`.
        """
        with scratch_dir(self.project_root, "env-") as tmp:
            other = VerificationEnvironment(
                self.vcs, self.runner, self.audit, self.project_root, self.spec
            )
            other._move_to(EnvironmentPlace(tmp / "env", tmp / "env.json", rev, log_dir))
            yield other

    @property
    def installed_plugins(self) -> tuple[str, ...] | None:
        """The plugins the commands load, once built; None if the profile can't list them."""
        if not self.spec.plugins_command:
            return None
        return (*self.spec.plugins, *self._installed_plugins)

    def search_path(self) -> str:
        """Return where a verification command looks for its program, as `Popen` reads it."""
        env = self.command_env(Path(HOME_DIR_PLACEHOLDER))
        return os.pathsep.join(os.get_exec_path(env))

    def _fixed_env(self, home: Path) -> dict[str, str]:
        return {
            name: value.replace(ENV_DIR_PLACEHOLDER, str(self.env_dir)).replace(
                HOME_DIR_PLACEHOLDER, str(home)
            )
            for name, value in self.spec.env.items()
        }

    def command_env(self, home: Path) -> dict[str, str]:
        """Return the whole environment of a verification command living in `home`."""
        inherited = {name: os.environ[name] for name in self.spec.inherit if name in os.environ}
        fixed = self._fixed_env(home)
        plugins = [*self.spec.plugins, *self._installed_plugins]
        if plugins:
            fixed[self.spec.plugin_variable] = ",".join(plugins)
        return inherited | fixed

    def run(self, cmd: Sequence[str], cwd: Path, timeout_s: int | None) -> CommandOutput:
        """Run a verification command inside this environment, with a home nobody has used."""
        with scratch_dir(self.project_root, "home-") as home:
            return self.runner.run(cmd, cwd=cwd, timeout_s=timeout_s, env=self.command_env(home))

    def _prepare_env(self, run_id: str, home: Path) -> dict[str, str]:
        """Return what `prepare` runs with, which no verification command gets to see."""
        inherited = {
            name: os.environ[name] for name in self.spec.prepare_inherit if name in os.environ
        }
        user_home = Path.home()
        files = {
            name: str(user_home / rel_path)
            for name, rel_path in self.spec.prepare_home_files.items()
            if (user_home / rel_path).is_file()
        }
        answers = {
            name: self._ask_the_tool(run_id, name, command)
            for name, command in self.spec.prepare_ask.items()
        }
        return files | inherited | answers | self._fixed_env(home)

    def _ask_the_tool(self, run_id: str, name: str, command: str) -> str:
        """Ask with the caller's own environment: the answer depends on its home."""
        output = self.runner.run(
            shlex.split(command), cwd=self.project_root, timeout_s=_QUESTION_TIMEOUT_S
        )
        answer = output.stdout.strip()
        if output.returncode != 0 or not answer:
            self._fail(run_id, f"No se pudo averiguar {name}: {output.stderr}", output.returncode)
        return answer

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

        expected_hash = str(recorded.get("hash"))
        actual_hash = hash_tree(self.env_dir)
        if actual_hash != expected_hash:
            return self._tampered(run_id, expected_hash, actual_hash)

        # Only now: the question runs the environment's own interpreter.
        installed = self._list_plugins(run_id)
        if installed != recorded.get("plugins"):
            return self._tampered(run_id, recorded.get("plugins"), installed)
        self._installed_plugins = installed
        return None

    def _tampered(self, run_id: str, expected: object, actual: object) -> str:
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

        tokens = self._tokens(self.spec.prepare)
        with (
            verification_tree(self.vcs, self.place.rev, self.project_root) as tree,
            scratch_dir(self.project_root, "home-") as home,
        ):
            output = self.runner.run(
                tokens,
                cwd=tree,
                timeout_s=_PREPARE_TIMEOUT_S,
                env=self._prepare_env(run_id, home),
            )

        log_dir = self.place.log_dir or self.project_root / ".lictor" / "runs" / run_id / "env"
        log = unused_path(log_dir / "build.log")
        save_output(log, output)
        if output.returncode != 0:
            tail = "\n".join(output.stderr.strip().splitlines()[-_MAX_ERROR_LINES:])
            self._fail(
                run_id, f"No se pudo preparar el entorno de verificación: {tail}", output.returncode
            )

        self._install_files(run_id)
        plugins = self._list_plugins(run_id)
        env_hash = hash_tree(self.env_dir)
        self.manifest.write_text(
            json.dumps({"run_id": run_id, "hash": env_hash, "plugins": plugins}), encoding="utf-8"
        )
        self._installed_plugins = plugins
        duration_ms = int((time.monotonic() - started) * 1000)
        self._emit(
            run_id,
            "env.built",
            {
                "hash": env_hash,
                "reason": reason,
                "duration_ms": duration_ms,
                "plugins": plugins,
            },
        )

    def _tokens(self, command: str) -> list[str]:
        return shlex.split(command.replace(ENV_DIR_PLACEHOLDER, str(self.env_dir)))

    def _install_files(self, run_id: str) -> None:
        if not self.spec.files:
            return
        target = self._files_dir(run_id)
        for source in self.spec.files:
            shutil.copyfile(source, target / source.name)

    def _files_dir(self, run_id: str) -> Path:
        """Ask the environment where its files go: its layout depends on what built it."""
        answer = self._ask(
            run_id,
            self.spec.files_dir_command,
            "El entorno de verificación no dice dónde instalar sus ficheros",
        ).strip()
        target = Path(answer).resolve()
        if not answer or not target.is_dir() or not target.is_relative_to(self.env_dir.resolve()):
            self._fail(run_id, f"'{answer}' no es un directorio del entorno de verificación")
        return target

    def _list_plugins(self, run_id: str) -> list[str]:
        """Ask the environment what it has installed, without importing any of it."""
        if not self.spec.plugins_command:
            return []
        answer = self._ask(
            run_id,
            self.spec.plugins_command,
            "El entorno de verificación no dice qué plugins tiene instalados",
        )
        plugins = sorted({line.strip() for line in answer.splitlines() if line.strip()})
        refused = [name for name in plugins if not _MODULE_NAME.fullmatch(name)]
        if refused:
            names = ", ".join(refused)
            self._fail(run_id, f"Plugins instalados que no se pueden cargar por nombre: {names}")
        return plugins

    def _ask(self, run_id: str, command: str, failure: str) -> str:
        output = self.run(self._tokens(command), self.env_dir, _QUESTION_TIMEOUT_S)
        if output.returncode != 0:
            self._fail(run_id, f"{failure}: {output.stderr}", output.returncode)
        return output.stdout

    def _fail(self, run_id: str, message: str, exit_code: int | None = None) -> NoReturn:
        self._emit(run_id, "env.failed", {"exit_code": exit_code, "error": message})
        raise EnvironmentUnavailableError(message)

    def _emit(self, run_id: str, name: str, data: dict[str, object]) -> None:
        self.audit.event_log.append(
            new_event(
                self.audit.clock,
                f"lictor.{name}.v1",
                run_id,
                data=data,
            )
        )
