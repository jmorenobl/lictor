"""Where a run keeps its evidence, so that no attempt can overwrite another's.

Under `.lictor/runs/<run_id>/<owner>/`, where the owner is a scenario or `driver`, each attempt
leaves `NNN-implement.json` or `NNN-driver.json`, the prompt it was given beside it
(`NNN-implement.prompt.md`), and one directory per verification. Names are
never reused: a retry, a resume or a repeated verification gets an `-rN` suffix.
"""

import contextlib
import fcntl
import hashlib
import os
import shlex
import shutil
import stat
import tempfile
from collections.abc import Callable, Collection, Iterator
from pathlib import Path
from typing import NoReturn

from lictor.application.ports import Vcs
from lictor.application.seal import hash_file, hash_tree
from lictor.domain.errors import EnvironmentUnavailableError, RunInProgressError
from lictor.domain.model import CommandOutput, ControlResult
from lictor.domain.rules import runs_to_prune

DRIVER_OWNER = "driver"
# What `doctor` and `check` leave, apart from any run's and replaced by the next one.
DOCTOR_DIR = "doctor"
CHECK_DIR = "check"
RUN_LOCK = "run.lock"
PRIVATE_MODE = 0o700


def attempt_prefix(project_root: Path, run_id: str, owner: str, attempt: int) -> Path:
    """Return the common prefix of everything an attempt leaves in `runs/`."""
    return project_root / ".lictor" / "runs" / run_id / owner / f"{attempt:03d}"


def aside_dir(project_root: Path, name: str) -> Path:
    """Return where a command that isn't a run keeps what it leaves, under `.lictor/`."""
    return project_root / ".lictor" / name


def unused_path(path: Path, start: int = 0) -> Path:
    """Return `path`, or its first `-rN` variant from `start` on that doesn't exist yet."""
    if start == 0 and not path.exists():
        return path
    retry = max(start, 1)
    while True:
        candidate = path.with_name(f"{path.stem}-r{retry}{path.suffix}")
        if not candidate.exists():
            return candidate
        retry += 1


def prune_runs(
    project_root: Path,
    keep: int,
    protected: Collection[str],
    record: Callable[[list[str]], None],
) -> list[str]:
    """Retire the evidence of the runs past the `keep` most recent, whole, and say which.

    `record` writes them down before anything is deleted: a kill halfway leaves the journal
    naming them, and the next sweep finishes the job. The journal keeps every event and the
    hash of every step, so the report can tell retired evidence from altered evidence.
    """
    runs_dir = project_root / ".lictor" / "runs"
    if not runs_dir.is_dir():
        return []
    names = [p.name for p in runs_dir.iterdir() if p.is_dir() and not p.is_symlink()]
    pruned = runs_to_prune(names, keep, protected)
    if pruned:
        record(pruned)
        for name in pruned:
            shutil.rmtree(runs_dir / name, ignore_errors=True)
    return pruned


def new_step_dir(path: Path) -> Path:
    """Create a directory that no previous step has used."""
    step_dir = unused_path(path)
    step_dir.mkdir(parents=True)
    return step_dir


def _scratch_root(project_root: Path) -> Path:
    """Return where this project's temporary directories live, outside the project."""
    digest = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"lictor-{digest}"


def _check_private(root: Path) -> None:
    """Refuse a directory someone else could have prepared or written into, and close the rest.

    Its name is predictable and the temporary directory may be shared, as `/tmp` is on Linux:
    another user could create it first and change the tree under verification. One of the current
    user's that nobody else could write into, as older versions left it (`0o755`), is only made
    private: nothing in it can come from someone else, and an unprivileged user can't give a file
    away. The checks and the `chmod` go through one descriptor, so the path can't change between.
    """
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as err:
        _why_unopenable(root, err)
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != os.getuid():
            _refuse(root, "pertenece a otro usuario")
        if mode & 0o022:
            _refuse(root, f"tiene escritura para el grupo u otros ({oct(mode)})")
        if mode != PRIVATE_MODE:
            os.fchmod(fd, PRIVATE_MODE)
    finally:
        os.close(fd)


def _why_unopenable(root: Path, err: OSError) -> NoReturn:
    with contextlib.suppress(OSError):
        mode = os.lstat(root).st_mode
        if stat.S_ISLNK(mode):
            _refuse(root, "es un enlace simbólico")
        if not stat.S_ISDIR(mode):
            _refuse(root, "no es un directorio")
    # A directory of one's own without permissions: nobody else is involved, only its owner.
    raise EnvironmentUnavailableError(
        f"el directorio temporal {root} no se puede abrir ({err.strerror}), así que Lictor no "
        "puede comprobar quién más lo usa. Si es tuyo, devuélvele los permisos con `chmod 700` "
        "o bórralo, y vuelve a lanzar; si no, apunta TMPDIR a un directorio solo tuyo."
    )


def _refuse(root: Path, problem: str) -> NoReturn:
    raise EnvironmentUnavailableError(
        f"el directorio temporal {root} {problem}, así que otro usuario podría cambiar lo que "
        "se verifica. Si es tuyo y nadie más lo usa, bórralo y vuelve a lanzar; si no, apunta "
        "TMPDIR a un directorio solo tuyo y vuelve a lanzar."
    )


def _private_root(project_root: Path) -> Path:
    root = _scratch_root(project_root)
    with contextlib.suppress(FileExistsError):
        root.mkdir(mode=PRIVATE_MODE)
    _check_private(root)
    return root


@contextlib.contextmanager
def scratch_dir(project_root: Path, prefix: str) -> Iterator[Path]:
    """Yield an empty directory outside the project, gone afterwards."""
    root = _private_root(project_root)
    with tempfile.TemporaryDirectory(prefix=prefix, dir=root, ignore_cleanup_errors=True) as tmp:
        yield Path(tmp)


def sweep_scratch(project_root: Path) -> None:
    """Remove what a killed run left behind, which nothing else would ever clean."""
    root = _scratch_root(project_root)
    if not os.path.lexists(root):
        return
    _check_private(root)
    shutil.rmtree(root, ignore_errors=True)


@contextlib.contextmanager
def holding_project(project_root: Path) -> Iterator[bool]:
    """Try to hold the project, and say whether it was free; never wait for it.

    Without it a second `lictor run` swept the copy and the clone of the first one midway. The
    kernel drops the lock with the process, so a kill leaves nothing to clean, and the descriptor
    isn't inherited, so a command left orphaned doesn't keep it. The file is not state of the run:
    neither `IntegritySnapshot` nor the anchor read it.
    """
    lictor_dir = project_root / ".lictor"
    lictor_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lictor_dir / RUN_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)


@contextlib.contextmanager
def exclusive_run(project_root: Path, command: str) -> Iterator[None]:
    """Hold the project for one command, or refuse at once if another one holds it."""
    with holding_project(project_root) as held:
        if not held:
            raise RunInProgressError(
                "otra ejecución de Lictor está en curso en este proyecto (tiene el cerrojo "
                f".lictor/{RUN_LOCK}). No se ha tocado nada: espera a que termine o detenla, y "
                f"vuelve a lanzar `{command}`."
            )
        yield


def prepare_scratch(project_root: Path) -> None:
    """Make sure the temporary directory is private, before anything needs it."""
    _private_root(project_root)


@contextlib.contextmanager
def verification_tree(vcs: Vcs, rev: str, project_root: Path) -> Iterator[Path]:
    """Yield a clean clone of `rev`, outside the project, gone afterwards."""
    with scratch_dir(project_root, "verify-") as tmp:
        tree = tmp / "tree"
        vcs.export_tree(rev, tree)
        yield tree


@contextlib.contextmanager
def agent_workspace(vcs: Vcs, project_root: Path) -> Iterator[Path]:
    """Yield a copy of HEAD for the agent, with no repository, no state and no project around it.

    Named after the project, as the prompt calls it. It is gone afterwards: its result is what
    `Vcs.capture` reads from it, and nothing of an attempt reaches the next one.
    """
    with scratch_dir(project_root, "work-") as tmp:
        workspace = tmp / project_root.name
        vcs.export_copy("HEAD", workspace)
        yield workspace


def fill_report_path(command: str, report: Path) -> list[str]:
    """Split a command and drop any report already at its `{out}`, which proves nothing."""
    report.unlink(missing_ok=True)
    return [token.replace("{out}", str(report)) for token in shlex.split(command)]


def write_text(path: Path, text: str) -> None:
    """Write evidence that may hold whatever bytes a command printed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def format_output(output: CommandOutput) -> str:
    return f"{output.stdout}\n{output.stderr}".strip()


def reader_input(report: str, run: str, out_file: Path, output: CommandOutput) -> str:
    """What a reader gets: the report the command wrote, or what it printed if it wrote none."""
    if report == "exit-code":
        return format_output(output)
    return str(out_file) if "{out}" in run else output.stdout


def save_output(path: Path, output: CommandOutput) -> None:
    """Keep the exit code and both streams of a command."""
    write_text(
        path,
        f"Código de salida: {output.returncode}\n\n"
        f"--- stdout ---\n{output.stdout}\n--- stderr ---\n{output.stderr}\n",
    )


def describe_crash(crash: str, control: ControlResult, output: str) -> str:
    """Put what the report itself says on the first line: it survives any condensing."""
    first_line = crash if control.test_cases or not control.error else f"{crash}: {control.error}"
    return f"{first_line}\n{output}".strip()


def save_prompt(raw_path: Path, prompt: str, project_root: Path) -> dict[str, str]:
    """Keep what the agent was asked beside what it did, before the call: the CLI gets it by argv.

    Its hash goes to the journal, like a step's, so a later change shows in the report.
    """
    path = raw_path.with_suffix(".prompt.md")
    write_text(path, prompt)
    return {"path": relative_to_project(path, project_root), "hash": hash_file(path)}


def step_evidence(step_dir: Path, project_root: Path) -> dict[str, str]:
    """Describe a finished step for the journal, which is protected where `runs/` isn't.

    `runs/` grows without limit and no decision reads it once a step is over, so it stays out of
    `IntegritySnapshot`. Its hash in the journal makes a later change visible in the report.
    """
    return {"path": relative_to_project(step_dir, project_root), "hash": hash_tree(step_dir)}


def relative_to_project(path: Path, project_root: Path) -> str:
    """Return the path to record: relative to the project whenever it is inside."""
    with contextlib.suppress(ValueError):
        return path.relative_to(project_root).as_posix()
    return str(path)
