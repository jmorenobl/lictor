"""Phase 4 (M5, A3): a real SIGKILL at every node, before, during and after its work.

Lictor runs in a process of its own and dies by the signal, not by a simulated exception. What it
leaves is judged by relaunching it in another process and checking the commits with `false_greens`.
"""

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pytest

from lictor.application.run_files import _scratch_root
from lictor.application.run_index import read_run_index
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    GOOD_3,
    MODULE_PATH,
    events_of,
    false_greens,
    git,
    green_ids,
    quarantined,
    require,
    run_worktree,
    write,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = Path(__file__).with_name("kill_runner.py")
REACH_TIMEOUT_S = 120
RESUME_TIMEOUT_S = 180
MAX_RELAUNCHES = 4
PAUSED = 50
MATRIX_ENV = "LICTOR_KILL_MATRIX"

ALL_GREEN = ["IVA-001", "IVA-002", "IVA-003"]
MOMENTS = ("antes", "durante", "despues")
MODE_BY_NODE = {"quarantine": "cuarentena", "pause": "pausa"}
EXPECTED = {
    "verde": (0, ALL_GREEN, []),
    "pausa": (0, ALL_GREEN, []),
    "cuarentena": (30, ALL_GREEN[:2], ["IVA-003"]),
}
# `pick_next` only reads the state, and returning from `pause` is the resume itself.
NOT_A_MOMENT = {"pick_next:durante", "pause:despues"}
NODES = (
    "preflight",
    "load_spec",
    "build_driver",
    "red_check",
    "pick_next",
    "implement",
    "verify",
    "commit",
    "quarantine",
    "pause",
    "report",
)
POINTS = [
    "worktree:durante",
    *(
        point
        for node in NODES
        for moment in MOMENTS
        if (point := f"{node}:{moment}") not in NOT_A_MOMENT
    ),
]


@dataclass(frozen=True)
class Cell:
    """A row of the matrix: where it died and how it ended."""

    point: str
    mode: str
    relaunches: int
    exit_code: int
    greens: list[str]
    quarantined: list[str]


def _command(root: Path, tmp_path: Path, mode: str, *stall: str) -> list[str]:
    flags = tmp_path / "marcas"
    flags.mkdir(exist_ok=True)
    args = [str(root), str(tmp_path / "perfiles"), mode, str(flags), *stall]
    return [sys.executable, "-B", str(RUNNER), *args]


def _environment(*, in_place: bool = False, branch: str = "") -> dict[str, str]:
    paths = [str(REPO_ROOT / "src"), str(REPO_ROOT)]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths), "LICTOR_KILL_BRANCH": branch}
    if in_place:
        env["LICTOR_KILL_IN_PLACE"] = "1"
    return env


def _stop_at(
    command: list[str], point: str, marker: Path, env: dict[str, str] | None = None
) -> subprocess.Popen[bytes]:
    """Launch a run and return once it stands still at `point`."""
    process = subprocess.Popen(
        command, env=env or _environment(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    deadline = time.monotonic() + REACH_TIMEOUT_S
    while not marker.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            output = process.stdout.read().decode() if process.stdout else ""
            process.kill()
            raise AssertionError(f"el proceso no llegó a {point}: {output}")
        time.sleep(0.02)
    return process


def _kill_at(root: Path, mode: str, point: str, marker: Path, *, in_place: bool = False) -> None:
    """Kill a run at `point`; the marker lives in the case's own directory, beside its flags."""
    command = _command(root, marker.parent, mode, point, str(marker))
    process = _stop_at(command, point, marker, _environment(in_place=in_place))
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=30)
    require(
        process.returncode == -signal.SIGKILL, f"debía morir por la señal: {process.returncode}"
    )


def _relaunch(root: Path, tmp_path: Path, mode: str, *, in_place: bool = False) -> tuple[int, int]:
    """Run until it stops for good; a pause for quota is a stop like any other."""
    for relaunches in range(1, MAX_RELAUNCHES + 1):
        finished = subprocess.run(
            _command(root, tmp_path, mode),
            env=_environment(in_place=in_place),
            capture_output=True,
            text=True,
            timeout=RESUME_TIMEOUT_S,
            check=False,
        )
        if finished.returncode != PAUSED:
            assert finished.returncode in (0, 30), finished.stdout + finished.stderr
            return finished.returncode, relaunches
    raise AssertionError("no terminó tras varias reanudaciones")


def _record(cell: Cell) -> None:
    path = os.environ.get(MATRIX_ENV)
    if path:
        with Path(path).open("a", encoding="utf-8") as matrix:
            matrix.write(json.dumps(asdict(cell)) + "\n")


def _control_killed_midway(marker: Path) -> dict[str, str]:
    """A real command in progress: it outlives Lictor, as the kill leaves it orphaned."""
    script = (
        f"test -e {shlex.quote(str(marker))} || {{ touch {shlex.quote(str(marker))}; sleep 3; }}"
    )
    return {
        "id": "lento",
        "level": "L2",
        "run": f"sh -c {shlex.quote(script)}",
        "report": "exit-code",
    }


@pytest.mark.parametrize("point", POINTS)
def test_kill_9_at_every_node_loses_nothing_and_invents_nothing(
    make_project: ProjectFactory, tmp_path: Path, point: str
) -> None:
    node = point.split(":", maxsplit=1)[0]
    mode = MODE_BY_NODE.get(node, "verde")
    marker = tmp_path / "alcanzado"
    in_a_command = point == "verify:durante"
    root = make_project(extra_controls=[_control_killed_midway(marker)] if in_a_command else None)

    head = git(root, "rev-parse", "HEAD")

    _kill_at(root, mode, "" if in_a_command else point, marker)
    exit_code, relaunches = _relaunch(root, tmp_path, mode)

    runs = read_run_index(root)
    assert len(runs) == 1, "la reanudación debía encontrar la ejecución, no empezar otra"
    worktree = Path(runs[0].worktree)
    expected_code, expected_greens, expected_quarantine = EXPECTED[mode]
    assert exit_code == expected_code
    assert green_ids(worktree) == expected_greens, "ni commits dobles ni verdes perdidos"
    assert false_greens(worktree) == []
    assert sorted(set(quarantined(worktree))) == expected_quarantine
    assert not git(worktree, "status", "--short"), "no debe quedar nada a medias en el árbol"
    assert (worktree / ".lictor" / "report.md").is_file()
    assert git(root, "rev-parse", "HEAD") == head, "el checkout de la persona no se toca"
    assert not git(root, "status", "--short")
    _record(
        Cell(
            point,
            mode,
            relaunches,
            exit_code,
            green_ids(worktree),
            sorted(set(quarantined(worktree))),
        )
    )


def test_kill_9_while_the_baseline_registers_a_collateral_green(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """The branch moved inside the red check and the checkpoint never heard of it."""
    root = make_project()
    require(
        _relaunch(root, tmp_path, "cuarentena", in_place=True)[0] == 30,
        "IVA-003 debía quedar en cuarentena",
    )
    write(root, MODULE_PATH, GOOD_3)
    git(root, "commit", "-q", "-am", "feat: importe negativo a mano")
    point = "red_check:registro"

    _kill_at(root, "verde", point, tmp_path / "alcanzado", in_place=True)
    killed_head = git(root, "rev-parse", "HEAD").strip()
    require(
        "Lictor-Collateral" in git(root, "log", "-1", "--format=%B"),
        "debía morir con el registro ya en la rama",
    )
    exit_code, relaunches = _relaunch(root, tmp_path, "verde", in_place=True)

    assert exit_code == 0
    assert green_ids(root) == ALL_GREEN, "ni commits dobles ni verdes perdidos"
    assert git(root, "rev-parse", "HEAD").strip() == killed_head
    assert false_greens(root) == []
    assert not git(root, "status", "--short")
    assert not [e for e in events_of(root, "run.stopped") if e["data"]["exit_code"] == 10]
    _record(Cell(point, "linea-base", relaunches, exit_code, green_ids(root), []))


def test_a_lock_left_by_a_killed_git_stops_and_explains(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """Killing the whole group can take git with it: its lock stays, and nothing else is lost."""
    root = make_project()
    marker = tmp_path / "alcanzado"
    _kill_at(root, "verde", "commit:antes", marker, in_place=True)
    lock = root / ".git" / "index.lock"
    write(root, ".git/index.lock", "")

    stopped = subprocess.run(
        _command(root, tmp_path, "verde"),
        env=_environment(in_place=True),
        capture_output=True,
        text=True,
        timeout=RESUME_TIMEOUT_S,
        check=False,
    )

    assert stopped.returncode == 10
    assert "index.lock" in events_of(root, "run.stopped")[-1]["data"]["reason"]
    lock.unlink()
    assert _relaunch(root, tmp_path, "verde", in_place=True)[0] == 0
    assert green_ids(root) == ALL_GREEN


def test_a_second_run_is_refused_without_touching_the_first(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """Without a lock the second one swept the copy and the clone the first one was using."""
    root = make_project()
    marker = tmp_path / "alcanzado"
    release = tmp_path / "suelta"
    point = "implement:durante"
    command = _command(root, tmp_path, "verde", point, str(marker), str(release))
    first = _stop_at(command, point, marker)
    worktree = run_worktree(root, read_run_index(root)[0].branch)
    try:
        journal = (worktree / ".lictor" / "journal.jsonl").read_bytes()
        second = subprocess.run(
            _command(root, tmp_path, "verde"),
            env=_environment(),
            capture_output=True,
            text=True,
            timeout=RESUME_TIMEOUT_S,
            check=False,
        )

        assert second.returncode == 10, second.stdout + second.stderr
        assert "otra ejecución" in second.stderr
        assert "--branch" in second.stderr, "desde el checkout, dice cómo lanzar otra en paralelo"
        assert list(_scratch_root(worktree).glob("work-*")), "la copia del agente debía seguir ahí"
        assert (worktree / ".lictor" / "journal.jsonl").read_bytes() == journal
    finally:
        release.touch()
    output, _ = first.communicate(timeout=RESUME_TIMEOUT_S)

    assert first.returncode == 0, output.decode()
    assert green_ids(worktree) == ALL_GREEN
    assert false_greens(worktree) == []
    assert not events_of(worktree, "state.tampered")
