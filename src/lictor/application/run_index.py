"""Where each run's worktree lives, and the index the main checkout keeps of them (ADR-0005).

The index only points: what decides whether a run can be resumed is the anchor in that run's own
checkpoint, inside its worktree. It isn't state of any run either, so neither `IntegritySnapshot`
nor the anchor read it, and a stale or rewritten entry can only send a resume to a worktree whose
own checks then refuse it.
"""

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from lictor.domain.errors import EnvironmentUnavailableError
from lictor.domain.run_branches import RUN_BRANCH_PREFIX

HOME_VARIABLE = "LICTOR_HOME"
INDEX_FILE = "worktrees.json"
_INDEX_VERSION = 1
_DIGEST_CHARS = 12


@dataclass(frozen=True)
class RunEntry:
    """A run started from this checkout: its branch, its worktree and its id."""

    branch: str
    worktree: str
    run_id: str


def lictor_home() -> Path:
    """Lictor's directory on this machine: `$LICTOR_HOME`, or `~/.lictor`.

    It is the machine's, not the project's, so it isn't in `lictor.json`. Never under the
    temporary directory: a restart would delete the worktrees, and with them every run that could
    still be resumed.
    """
    configured = os.environ.get(HOME_VARIABLE, "")
    home = (Path(configured).expanduser() if configured else Path.home() / ".lictor").resolve()
    temporary = Path(tempfile.gettempdir()).resolve()
    if home.is_relative_to(temporary):
        raise EnvironmentUnavailableError(
            f"El directorio de Lictor ({home}) está dentro del directorio temporal ({temporary}), "
            "y un reinicio borraría los worktrees de las ejecuciones. Apunta "
            f"{HOME_VARIABLE} a otro sitio y vuelve a lanzar."
        )
    return home


def run_worktree(main_root: Path, branch: str) -> Path:
    """`<home>/worktrees/<repo>-<hash of its path>/<branch without lictor/>`."""
    digest = hashlib.sha256(str(main_root).encode()).hexdigest()[:_DIGEST_CHARS]
    name = branch.removeprefix(RUN_BRANCH_PREFIX)
    return lictor_home() / "worktrees" / f"{main_root.name}-{digest}" / name


def _index_path(main_root: Path) -> Path:
    return main_root / ".lictor" / INDEX_FILE


def read_run_index(main_root: Path) -> list[RunEntry]:
    """Read the runs started from this checkout, oldest first; a damaged index points nowhere."""
    try:
        raw = json.loads(_index_path(main_root).read_text(encoding="utf-8"))
        return [RunEntry(**entry) for entry in raw["runs"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def add_to_run_index(main_root: Path, entry: RunEntry) -> None:
    """Record a run before its worktree exists, so a kill in between leaves it findable."""
    runs = [run for run in read_run_index(main_root) if run.branch != entry.branch]
    _write_index(main_root, [*runs, entry])


def _write_index(main_root: Path, runs: list[RunEntry]) -> None:
    path = _index_path(main_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"{INDEX_FILE}.tmp")
    payload = {"version": _INDEX_VERSION, "runs": [asdict(run) for run in runs]}
    partial.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def remove_from_run_index(main_root: Path, branch: str) -> None:
    """Forget a run whose worktree was retired."""
    runs = [run for run in read_run_index(main_root) if run.branch != branch]
    _write_index(main_root, runs)
