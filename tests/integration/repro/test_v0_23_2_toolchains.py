"""0.23.2 on Go with the `generic` profile: G7, G8 and G1 as the pass on kitlegal met them.

Go extracts its module cache without write permission; inside the worktree's verification
environment that made `git worktree remove` fail halfway (G7), and `retire` then took what was
left for a run that goes on (G8). A `t.Run` after the stub's call is one case at the red and two
with the code done, and by name both are the scenario (G1). The dependency comes from a module
proxy on disk, so the cache fills without a network.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lictor.application.run_index import read_run_index
from lictor.cli import app
from lictor.domain.model import AgentTask
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    git,
    green_ids,
    require,
    run_from_checkout,
    run_lictor,
    run_worktree,
    write,
)
from tests.integration.repro.toolchains import (
    GO_DRIVER_PATH,
    GO_HELPER,
    GO_IMPL,
    GO_STUB,
    go_driver,
    go_project,
    go_project_with_dependency,
    go_proxy,
)

pytestmark = pytest.mark.toolchain

RECIPE_FLAGS = "-mod=mod -modcacherw"
BEFORE_FLAGS = "-mod=mod"


def _driver(source: str) -> object:
    def driver(task: AgentTask, _attempt: int = 1) -> None:
        write(task.cwd, "nif/nif.go", GO_STUB)
        write(task.cwd, GO_DRIVER_PATH, source)

    return driver


def _impl(task: AgentTask, _scenario_id: str, _attempt: int = 1) -> None:
    write(task.cwd, "nif/nif.go", GO_IMPL)


def _agent(source: str | None = None) -> Agent:
    return Agent(driver=_driver(source or go_driver()), impl=_impl)  # type: ignore[arg-type]


def _read_only_dirs(cache: Path) -> list[Path]:
    return [
        Path(top) / name
        for top, dirs, _files in os.walk(cache)
        for name in dirs
        if not os.stat(Path(top) / name).st_mode & stat.S_IWUSR
    ]


def _finished_go_run(tmp_path: Path, flags: str) -> tuple[Path, Path]:
    root = go_project_with_dependency(tmp_path / "nif", go_proxy(tmp_path / "proxy"), flags=flags)
    require(run_from_checkout(root, _agent(), branch="nif") == Outcome(0), "el run debía acabar")
    require(green_ids(run_worktree(root, "lictor/nif")) == ["NIF-001", "NIF-002"], "dos verdes")
    return root, run_worktree(root, "lictor/nif")


def _retire(root: Path) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["retire", "nif", "--cwd", str(root)])
    return result.exit_code, result.output


@pytest.mark.skipif(os.geteuid() == 0, reason="root borra sin permiso de escritura")
def test_go_a_read_only_module_cache_doesnt_stop_retire(tmp_path: Path) -> None:
    """G7: without `-modcacherw`, as the recipe was before 0.23.2."""
    root, worktree = _finished_go_run(tmp_path, BEFORE_FLAGS)
    cache = worktree / ".lictor" / "verify-env" / "gomod"
    require(bool(_read_only_dirs(cache)), "Go debía dejar la caché sin permiso de escritura")

    code, output = _retire(root)

    assert code == 0, output
    assert not worktree.exists()
    assert str(worktree) not in git(root, "worktree", "list")
    assert read_run_index(root) == []


def test_go_with_the_recipe_the_module_cache_stays_writable(tmp_path: Path) -> None:
    root, worktree = _finished_go_run(tmp_path, RECIPE_FLAGS)
    cache = worktree / ".lictor" / "verify-env" / "gomod"
    require(cache.is_dir(), "la caché de módulos debía llenarse")

    assert _read_only_dirs(cache) == []
    code, output = _retire(root)
    assert code == 0, output
    assert not worktree.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root borra sin permiso de escritura")
def test_go_a_worktree_git_failed_to_remove_is_never_resumed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """G8: what 0.23.1's `retire` did, `git worktree remove` on a read-only cache.

    What git leaves depends on the order it reads each directory in, so only what holds for
    every shape is asserted: the run is never resumed, and `retire` never asks for one.
    """
    root, worktree = _finished_go_run(tmp_path, BEFORE_FLAGS)
    removed = subprocess.run(
        ["git", "worktree", "remove", str(worktree)], cwd=root, capture_output=True, check=False
    )
    require(removed.returncode != 0 and worktree.is_dir(), "git debía fallar a medias")
    agent = _agent()

    assert run_from_checkout(root, agent, branch="nif") == Outcome(10)

    assert agent.calls == []
    assert "lictor retire nif" in capsys.readouterr().err
    code, output = _retire(root)
    assert "se puede reanudar" not in output
    assert code in (0, 10), output
    if code == 10:
        assert "No se ha tocado nada" in output
    else:
        assert not worktree.exists()


# The first check calls the stub, which panics at the red: the subtest only exists with the code.
SUBTEST = (
    "func Test_NIF_001(t *testing.T) {\n\tdefer enEscenario(t)\n"
    '\tif !nif.Valido("12345678Z") {\n\t\tt.Fatal("quiero válido")\n\t}\n'
    '\tt.Run("otra fila", func(t *testing.T) {\n\t\tdefer enEscenario(t)\n'
    '\t\tif !nif.Valido("12345678Z") {\n\t\t\tt.Fatal("quiero válido")\n\t\t}\n\t})\n}\n'
)


def test_go_a_subtest_in_the_sealed_driver_stops_the_run_with_10(tmp_path: Path) -> None:
    """G1: the recipe asked for `defer enEscenario(t)` «inside every `t.Run`» too."""
    root = go_project(tmp_path / "nif")
    header, _, rest = go_driver().partition("func Test_NIF_001")
    second = rest[rest.index("func Test_NIF_002") :]
    source = f"{header}{SUBTEST}\n{second}"
    require(GO_HELPER in source, "el driver debía llevar su helper")

    agent = _agent(source)
    assert run_lictor(root, agent) == Outcome(10)

    assert agent.calls == ["driver", "NIF-001", "NIF-001"]
    (finished,) = events_of(root, "run.finished")
    reason = finished["data"]["reason"]
    assert "El driver sellado da 2 casos para el escenario NIF-001" in reason
    assert "Test_NIF_001/otra_fila" in reason
