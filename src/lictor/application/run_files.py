"""Where a run keeps its evidence, so that no attempt can overwrite another's.

Under `.lictor/runs/<run_id>/<owner>/`, where the owner is a scenario or `driver`, each attempt
leaves `NNN-implement.json` or `NNN-driver.json` and one directory per verification. Names are
never reused: a retry, a resume or a repeated verification gets an `-rN` suffix.
"""

import contextlib
import hashlib
import shlex
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from lictor.application.ports import Vcs
from lictor.domain.model import CommandOutput, ControlResult

DRIVER_OWNER = "driver"


def attempt_prefix(project_root: Path, run_id: str, owner: str, attempt: int) -> Path:
    """Return the common prefix of everything an attempt leaves in `runs/`."""
    return project_root / ".lictor" / "runs" / run_id / owner / f"{attempt:03d}"


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


def new_step_dir(path: Path) -> Path:
    """Create a directory that no previous step has used."""
    step_dir = unused_path(path)
    step_dir.mkdir(parents=True)
    return step_dir


def _scratch_root(project_root: Path) -> Path:
    """Return where this project's temporary directories live, outside the project."""
    digest = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"lictor-{digest}"


@contextlib.contextmanager
def scratch_dir(project_root: Path, prefix: str) -> Iterator[Path]:
    """Yield an empty directory outside the project, gone afterwards."""
    root = _scratch_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=prefix, dir=root, ignore_cleanup_errors=True) as tmp:
        yield Path(tmp)


def sweep_scratch(project_root: Path) -> None:
    """Remove what a killed run left behind, which nothing else would ever clean."""
    shutil.rmtree(_scratch_root(project_root), ignore_errors=True)


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


def relative_to_project(path: Path, project_root: Path) -> str:
    """Return the path to record: relative to the project whenever it is inside."""
    with contextlib.suppress(ValueError):
        return path.relative_to(project_root).as_posix()
    return str(path)
