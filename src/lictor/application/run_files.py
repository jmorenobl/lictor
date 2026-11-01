"""Where a run keeps its evidence, so that no attempt can overwrite another's.

Under `.lictor/runs/<run_id>/<owner>/`, where the owner is a scenario or `driver`, each attempt
leaves `NNN-implement.json` or `NNN-driver.json` and one directory per verification. Names are
never reused: a retry, a resume or a repeated verification gets an `-rN` suffix.
"""

import contextlib
import shlex
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


@contextlib.contextmanager
def verification_tree(vcs: Vcs, rev: str) -> Iterator[Path]:
    """Yield a clean clone of `rev`, outside the project, gone afterwards."""
    with tempfile.TemporaryDirectory(prefix="lictor-verify-", ignore_cleanup_errors=True) as tmp:
        tree = Path(tmp) / "tree"
        vcs.export_tree(rev, tree)
        yield tree


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
