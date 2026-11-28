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
from collections.abc import Callable, Collection, Iterator, Sequence
from pathlib import Path
from typing import NoReturn

from lictor.application.ports import Vcs
from lictor.application.seal import hash_file, hash_tree
from lictor.domain.errors import CleanupError, EnvironmentUnavailableError, RunInProgressError
from lictor.domain.model import CommandOutput
from lictor.domain.rules import retired_to_prune, runs_to_prune
from lictor.domain.run_standing import EntryKind

DRIVER_OWNER = "driver"
# What `doctor` and `check` leave, apart from any run's and replaced by the next one.
DOCTOR_DIR = "doctor"
CHECK_DIR = "check"
RUN_LOCK = "run.lock"
PRIVATE_MODE = 0o700
# The run's verification environment, inside its worktree: `{env_dir}` of the profile's commands.
VERIFY_ENV_DIR = "verify-env"
# `doctor`'s environment, kept from one `doctor` to the next; no run nor `check` uses it (G5).
DOCTOR_ENV_DIR = "doctor-env"


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


RETIRED_DIR = "retired"
# What a run leaves that a person reads after it: the checkpoint and the seal only served a resume.
RETIRED_EVIDENCE = ("journal.jsonl", "report.md", "runs")


def archive_run_evidence(
    worktree: Path, main_root: Path, name: str, keep: int, *, replace: bool = False
) -> Path | None:
    """Copy a run's journal, report and evidence to the checkout, where they outlive its worktree.

    The copy is written beside its place and renamed into it, so a failure halfway leaves nothing
    that passes for evidence. With `replace` (every exit of a run, S21 of 0.25.0) a newer copy
    takes the place of the one there; without it (`retire`) one already there is kept, since a
    retire that stopped halfway kept it whole. Afterwards only the `keep` most recent stay.
    Returns the directory, or None when the worktree has nothing to keep. Raises OSError when it
    can't copy: `retire` then refuses to remove the worktree.
    """
    source = worktree / ".lictor"
    present = [item for item in RETIRED_EVIDENCE if (source / item).exists()]
    if not present:
        return None
    retired = own_dir(main_root) / RETIRED_DIR
    retired.mkdir(exist_ok=True)
    target = retired / name
    if target.is_dir() and not replace:
        # A retire that stopped halfway already kept it whole: what is left now may be less.
        return target
    _copy_into_place(source, present, retired / f".{name}.partial", target)
    entries = [p.name for p in retired.iterdir() if p.is_dir() and not p.name.startswith(".")]
    for old in retired_to_prune(entries, keep):
        shutil.rmtree(retired / old, ignore_errors=True)
    return target


def _copy_into_place(source: Path, items: Sequence[str], partial: Path, target: Path) -> None:
    """Copy `items` of `source` beside `target` and rename the copy into it, whole or not at all."""
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir()
    for item in items:
        if (source / item).is_dir():
            shutil.copytree(source / item, partial / item, symlinks=True)
        else:
            shutil.copy2(source / item, partial / item)
    if target.is_dir():
        # The older copy goes aside first: whatever stops here, one whole copy stays.
        old = target.with_name(f".{target.name}.old")
        shutil.rmtree(old, ignore_errors=True)
        target.rename(old)
        partial.rename(target)
        shutil.rmtree(old, ignore_errors=True)
        return
    partial.rename(target)


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
    """Remove what a killed run left behind, which nothing else would ever clean.

    A courtesy: what can't go, as a file an orphaned process still writes, stays for the next one.
    """
    root = _scratch_root(project_root)
    if not os.path.lexists(root):
        return
    _check_private(root)
    with contextlib.suppress(CleanupError):
        remove_tree(root)


def remove_tree(root: Path) -> None:
    """Remove a tree of Lictor's own, even where a tool left a directory without write permission.

    Go extracts its module cache read-only, and a directory without write permission can't lose
    its entries: `git worktree remove` failed on it and left the worktree half removed (G7 of
    0.23.2). Write permission goes back only to `root` and the directories inside it, never to its
    parent, and each path is tried again once; `rmtree` never follows a link out, and a link in
    place of `root` is removed, not followed. Raises
    CleanupError, naming the path, when something still can't be removed.
    """
    top = Path(os.path.abspath(root))
    tried: set[str] = set()

    def retry(func: Callable[..., object], path: str, exc: BaseException) -> None:
        if not isinstance(exc, PermissionError) or path in tried:
            raise _cleanup_error(top, path, exc) from exc
        tried.add(path)
        parent = Path(os.path.abspath(path)).parent
        try:
            for directory in (parent, Path(path)):
                if directory == top or directory.is_relative_to(top):
                    _make_writable(directory)
            if func in (os.rmdir, os.unlink):
                func(path)
            else:  # a directory it couldn't open or list: now it can, or it fails again
                shutil.rmtree(path, onexc=retry)
        except OSError as again:
            raise _cleanup_error(top, path, again) from again

    if os.path.islink(top):
        # A link in place of the directory is only the link: what it points to isn't Lictor's.
        os.unlink(top)
    elif os.path.lexists(top):
        shutil.rmtree(top, onexc=retry)


def _make_writable(directory: Path) -> None:
    """Give a real directory, not a link, its owner's full permissions back."""
    info = os.lstat(directory)
    if stat.S_ISDIR(info.st_mode):
        os.chmod(directory, stat.S_IMODE(info.st_mode) | stat.S_IRWXU)


def _cleanup_error(top: Path, path: str, exc: BaseException) -> CleanupError:
    reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
    return CleanupError(
        f"no se ha podido borrar {path} ({reason}), dentro de {top}, que es de Lictor. Mira qué "
        "lo retiene (un proceso que siga vivo, un fichero inmutable) y vuelve a lanzar."
    )


LICTOR_GITIGNORE = "*\n"


def own_dir(project_root: Path) -> Path:
    """Return `.lictor/`, created if missing, with a `.gitignore` that makes git ignore all of it.

    Everything in it is Lictor's, never the person's: without it, a project whose `.gitignore`
    doesn't name `.lictor/` saw `run` warn that its own lock was left uncommitted, the worktree's
    clean tree failed on the checkpoint, and `git worktree remove` refused the worktree (F53).
    """
    lictor_dir = project_root / ".lictor"
    lictor_dir.mkdir(parents=True, exist_ok=True)
    ignore = lictor_dir / ".gitignore"
    if not ignore.is_file():
        ignore.write_text(LICTOR_GITIGNORE, encoding="utf-8")
    return lictor_dir


@contextlib.contextmanager
def holding_project(project_root: Path) -> Iterator[bool]:
    """Try to hold the project, and say whether it was free; never wait for it.

    Without it a second `lictor run` swept the copy and the clone of the first one midway. The
    kernel drops the lock with the process, so a kill leaves nothing to clean, and the descriptor
    isn't inherited, so a command left orphaned doesn't keep it. The file is not state of the run:
    neither `IntegritySnapshot` nor the anchor read it.
    """
    lictor_dir = own_dir(project_root)
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


def lock_held(project_root: Path) -> bool:
    """Tell whether some process holds the project's lock, creating nothing to find out.

    Without the lock file nobody holds it. Trying it takes it for an instant, and only another
    process's hold makes it fail: `flock` belongs to the open file, so the caller must not hold it
    through another descriptor of its own.
    """
    try:
        fd = os.open(project_root / ".lictor" / RUN_LOCK, os.O_RDWR | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def tree_entries_on_disk(root: Path) -> list[tuple[str, EntryKind]]:
    """Every path under `root`, relative to it, with its kind; links are listed, never followed."""
    found: list[tuple[str, EntryKind]] = []
    for directory, dirs, files in os.walk(root):
        base = Path(directory)
        for name in [*dirs, *files]:
            path = base / name
            kind: EntryKind = "link" if path.is_symlink() else "dir" if path.is_dir() else "file"
            found.append((path.relative_to(root).as_posix(), kind))
    return found


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


@contextlib.contextmanager
def agent_files_dir(
    project_root: Path, sources: Sequence[Path], extra: Sequence[tuple[Path, str]] = ()
) -> Iterator[Path | None]:
    """Yield a copy of the profile's files for one agent call, outside the project and the copy.

    The agent measures with them (ADR-0013) without reaching the verification environment, whose
    path it never learns. None without files: then no rule or command names them. `extra` adds
    files under a name of their own, as the green's coverage report for a refactor (F71), only if
    they are there: a measure without them still counts, without comparing.
    """
    if not sources:
        yield None
        return
    with scratch_dir(project_root, "files-") as tmp:
        for source in sources:
            shutil.copyfile(source, tmp / source.name)
        for source, name in extra:
            if source.is_file() and not source.is_symlink():
                shutil.copyfile(source, tmp / name)
        yield tmp


def fill_report_paths(parts: Sequence[tuple[str, Path]]) -> list[str]:
    """Compose a command of its parts, by tokens, each `{out}` with the report of its own part.

    Every report goes first: one already there proves nothing. Parts are split one by one, never
    joined as text, so neither can change how the other splits (ADR-0008, point 4).
    """
    tokens: list[str] = []
    for command, report in parts:
        report.unlink(missing_ok=True)
        tokens.extend(token.replace("{out}", str(report)) for token in shlex.split(command))
    return tokens


def write_text(path: Path, text: str) -> None:
    """Write evidence that may hold whatever bytes a command printed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def format_output(output: CommandOutput) -> str:
    return f"{output.stdout}\n{output.stderr}".strip()


def save_output(path: Path, command: Sequence[str], output: CommandOutput) -> None:
    """Keep what ran, as its tokens were given, with its exit code and both streams (ADR-0009)."""
    write_text(
        path,
        f"Comando: {shlex.join(command)}\n"
        f"Código de salida: {output.returncode}\n\n"
        f"--- stdout ---\n{output.stdout}\n--- stderr ---\n{output.stderr}\n",
    )


def describe_crash(crash: str, output: str) -> str:
    """Put the reason on the first line, where it survives any condensing, and the output after."""
    return f"{crash}\n{output}".strip()


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
