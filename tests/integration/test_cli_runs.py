"""Which run `lictor run` picks from a person's checkout, and `retire`, `register` and `--branch`.

A real repository and real worktrees, but no graph: `_run_here`, where the run itself starts, is
replaced, and whether a run can go on is told through `_read_only_state`. The repro cases run the
same paths with real runs (`test_v0_3_0_own_worktree.py`, `test_v0_3_0_retire.py`,
`test_phase7_register.py`); these fail when the decision in `cli.py` breaks, in seconds.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from lictor import cli
from lictor.application.run_index import RunEntry, read_run_index, run_worktree
from lictor.cli import app
from lictor.domain.errors import ConfigError, RunInProgressError

runner = CliRunner()

FEATURE = (
    "Feature: IVA\n"
    "  @IVA-001\n"
    "  Scenario: General\n"
    "    Given un importe de 100\n"
    "    Then el total es 121\n"
)

CONFIG = {
    "profile": "python-uv@0.1",
    "backend": {"kind": "fake", "model": "fake-model-1"},
    "paths": {"writable": ["src/"], "driver": ["tests/"], "protected": ["lictor.json"]},
    "acceptance": {"run": "echo test {out}", "report": "exit-code"},
    "controls": [{"id": c, "enabled": False} for c in ("lint", "types", "unit", "coverage")],
    "agent_tools": [],
    "hints": "",
    "gitignore": [],
}


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _repository(root: Path) -> Path:
    root.mkdir()
    git(root, "init", "-b", "master")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A committed project whose configuration loads, with one scenario and no code."""
    root = _repository(tmp_path / "proyecto")
    (root / "lictor.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    (root / ".gitignore").write_text(".lictor/\n", encoding="utf-8")
    (root / "features").mkdir()
    (root / "features" / "iva.feature").write_text(FEATURE, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "chore: proyecto")
    return root


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, str | None]]:
    """Where each run would have started, instead of starting it."""
    calls: list[tuple[Path, str | None]] = []

    def run_here(
        resolved_cwd: Path, force_new: bool, progress: Any = None, run_id: str | None = None
    ) -> int:
        assert not force_new, "a new worktree never discards a run of its own"
        calls.append((resolved_cwd, run_id))
        return 0

    monkeypatch.setattr(cli, "_run_here", run_here)
    return calls


def _states(monkeypatch: pytest.MonkeyPatch, **by_worktree: Any) -> None:
    """Give each run's checkpoint, by its worktree's name; the rest never started."""

    def read(project_root: Path, run_id: str) -> Any:
        state = by_worktree.get(project_root.name)
        if isinstance(state, Exception):
            raise state
        return state

    monkeypatch.setattr(cli, "_read_only_state", read)


FINISHED = SimpleNamespace(values={"exit_code": 0}, next=())
PAUSED = SimpleNamespace(values={"exit_code": None, "current": "IVA-001"}, next=("pause",))


def _run(root: Path, *args: str) -> Any:
    return runner.invoke(app, ["run", "--quiet", *args, "--cwd", str(root)])


def _branches(root: Path) -> list[str]:
    return git(root, "branch", "--list", "--format=%(refname:short)").splitlines()


def test_a_new_run_gets_its_own_worktree_and_branch_from_head(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    result = _run(project)

    assert result.exit_code == 0, result.output
    [entry] = read_run_index(project)
    assert entry.branch == f"lictor/{entry.run_id}"
    assert entry.worktree == str(run_worktree(project, entry.branch))
    assert started == [(Path(entry.worktree), entry.run_id)]
    assert git(Path(entry.worktree), "rev-parse", "HEAD") == git(project, "rev-parse", "HEAD")
    assert f"Ejecución en la rama {entry.branch}, en {entry.worktree}" in result.stderr
    assert _branches(project) == ["lictor/" + entry.run_id, "master"]


def test_a_run_that_never_started_is_resumed_in_its_worktree(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    assert _run(project, "--branch", "iva").exit_code == 0

    result = _run(project)

    assert result.exit_code == 0, result.output
    [entry] = read_run_index(project)
    assert entry.branch == "lictor/iva"
    assert started == [(Path(entry.worktree), entry.run_id)] * 2


def test_a_paused_run_is_resumed_and_a_finished_one_is_not(
    project: Path, started: list[tuple[Path, str | None]], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(project, "--branch", "uno").exit_code == 0
    assert _run(project, "--branch", "dos", "--new").exit_code == 0
    _states(monkeypatch, uno=FINISHED, dos=PAUSED)

    assert _run(project).exit_code == 0

    assert started[-1][0].name == "dos"
    assert len(read_run_index(project)) == 2


def test_several_runs_that_can_go_on_are_never_guessed_between(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    assert _run(project, "--branch", "uno").exit_code == 0
    assert _run(project, "--branch", "dos", "--new").exit_code == 0

    result = _run(project)

    assert result.exit_code == 10
    assert "hay varias ejecuciones que se pueden reanudar: lictor/dos, lictor/uno" in result.stderr
    assert len(started) == 2

    assert _run(project, "--branch", "uno").exit_code == 0
    assert started[-1][0].name == "uno"


def test_new_starts_beside_a_run_that_can_go_on(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    assert _run(project, "--branch", "uno").exit_code == 0

    assert _run(project, "--new").exit_code == 0

    first, second = read_run_index(project)
    assert first.branch == "lictor/uno"
    assert second.branch == f"lictor/{second.run_id}"
    assert started[-1] == (Path(second.worktree), second.run_id)


def test_a_run_whose_worktree_is_gone_is_not_resumed(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    assert _run(project, "--branch", "uno").exit_code == 0
    [gone] = read_run_index(project)
    git(project, "worktree", "remove", gone.worktree)

    assert _run(project).exit_code == 0

    assert started[-1][0] != Path(gone.worktree)


def test_a_checkpoint_that_cant_be_read_still_counts_as_a_run_to_resume(
    project: Path, started: list[tuple[Path, str | None]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resume says what is wrong, with the run's own checks."""
    assert _run(project, "--branch", "uno").exit_code == 0
    _states(monkeypatch, uno=ConfigError("lictor.json no carga"))

    assert _run(project).exit_code == 0

    assert [call[0].name for call in started] == ["uno", "uno"]


@pytest.mark.parametrize(
    ("state", "goes_on"),
    [
        (None, True),
        (SimpleNamespace(values={}, next=()), True),
        (PAUSED, True),
        (SimpleNamespace(values={"exit_code": 10}, next=("pause",)), True),
        (FINISHED, False),
    ],
)
def test_can_go_on_until_the_checkpoint_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: Any, goes_on: bool
) -> None:
    worktree = tmp_path / "uno"
    worktree.mkdir()
    _states(monkeypatch, uno=state)

    assert cli._can_go_on(RunEntry("lictor/uno", str(worktree), "r1")) is goes_on
    assert cli._can_go_on(RunEntry("lictor/uno", str(tmp_path / "otro"), "r1")) is False


def test_a_bad_branch_name_is_refused_before_anything_is_created(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    result = _run(project, "--branch", "a/b")

    assert result.exit_code == 10
    assert "«a/b» no vale como nombre de rama" in result.stderr
    assert read_run_index(project) == []
    assert started == []


def test_a_repository_without_commits_has_nothing_to_start_from(
    tmp_path: Path, started: list[tuple[Path, str | None]]
) -> None:
    root = _repository(tmp_path / "vacio")

    result = _run(root)

    assert result.exit_code == 10
    assert "todavía no tiene ningún commit" in result.stderr
    assert read_run_index(root) == []


def test_an_existing_branch_that_isnt_a_run_is_never_reused(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    git(project, "branch", "lictor/iva")

    result = _run(project, "--branch", "iva")

    assert result.exit_code == 10
    assert "ya existe la rama lictor/iva" in result.stderr
    assert read_run_index(project) == []
    assert started == []


def test_what_isnt_committed_stays_in_the_checkout_and_is_said(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    (project / "notas.txt").write_text("a medias\n", encoding="utf-8")

    result = _run(project, "--branch", "iva")

    assert result.exit_code == 0, result.output
    assert "Aviso: La ejecución parte del último commit" in result.stderr
    assert "notas.txt" in result.stderr
    [entry] = read_run_index(project)
    assert not (Path(entry.worktree) / "notas.txt").exists()
    assert (project / "notas.txt").is_file()


def test_a_run_held_by_another_process_says_how_to_start_one_beside_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def held(*_args: Any, **_kwargs: Any) -> int:
        raise RunInProgressError("Hay otra ejecución en curso.")

    monkeypatch.setattr(cli, "_run_here", held)

    result = _run(project)

    assert result.exit_code == 10
    assert "Hay otra ejecución en curso. Para lanzar otra en paralelo" in result.stderr
    assert "`lictor run --branch <nombre>`" in result.stderr


def _linked_worktree(project: Path) -> Path:
    worktree = project.parent / "a-mano"
    git(project, "worktree", "add", "-q", "-b", "a-mano", str(worktree))
    return worktree


def test_inside_a_linked_worktree_the_run_works_right_there(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    worktree = _linked_worktree(project)

    assert _run(worktree).exit_code == 0

    assert started == [(worktree, None)]
    assert read_run_index(project) == []


def test_inside_a_linked_worktree_branch_is_refused(
    project: Path, started: list[tuple[Path, str | None]]
) -> None:
    worktree = _linked_worktree(project)

    result = _run(worktree, "--branch", "iva")

    assert result.exit_code == 10
    assert "ya estás en un worktree enlazado, en la rama a-mano" in result.stderr
    assert started == []


@pytest.fixture
def run_uno(project: Path, started: list[tuple[Path, str | None]]) -> RunEntry:
    """A run created from the checkout on `lictor/uno`, whose run never started."""
    assert _run(project, "--branch", "uno").exit_code == 0
    [entry] = read_run_index(project)
    return entry


@pytest.mark.parametrize("command", ["status", "report", "retire"])
def test_branch_commands_refuse_a_run_this_checkout_didnt_start(
    project: Path, run_uno: RunEntry, command: str
) -> None:
    args = [command, "otra"] if command == "retire" else [command, "--branch", "otra"]

    result = runner.invoke(app, [*args, "--cwd", str(project)])

    assert result.exit_code == 10
    assert "ninguna ejecución en la rama lictor/otra. Las que tiene: lictor/uno." in result.stderr


@pytest.mark.parametrize("command", ["status", "report", "retire"])
def test_branch_commands_refuse_inside_a_linked_worktree(project: Path, command: str) -> None:
    worktree = _linked_worktree(project)
    args = [command, "uno"] if command == "retire" else [command, "--branch", "uno"]

    result = runner.invoke(app, [*args, "--cwd", str(worktree)])

    assert result.exit_code == 10
    assert "esto es un worktree enlazado" in result.stderr


def test_branch_commands_refuse_a_bad_name_and_say_none_when_there_is_none(
    project: Path,
) -> None:
    bad = runner.invoke(app, ["status", "--branch", "..", "--cwd", str(project)])
    unknown = runner.invoke(app, ["retire", "uno", "--cwd", str(project)])

    assert bad.exit_code == 10
    assert "no vale como nombre de rama" in bad.stderr
    assert unknown.exit_code == 10
    assert "Las que tiene: ninguna." in unknown.stderr


def test_status_from_the_checkout_tells_each_run_by_its_branch(
    project: Path, run_uno: RunEntry
) -> None:
    assert _run(project, "--branch", "dos", "--new").exit_code == 0
    gone = run_worktree(project, "lictor/tres")
    with (project / ".lictor" / "worktrees.json").open("r+", encoding="utf-8") as index:
        payload = json.load(index)
        payload["runs"].append({"branch": "lictor/tres", "worktree": str(gone), "run_id": "r3"})
        index.seek(0)
        json.dump(payload, index)

    text = runner.invoke(app, ["status", "--cwd", str(project)])
    raw = runner.invoke(app, ["status", "--json", "--cwd", str(project)])
    one = runner.invoke(app, ["status", "--json", "--branch", "dos", "--cwd", str(project)])

    assert text.exit_code == 0, text.output
    headers = [line for line in text.stdout.splitlines() if line.startswith("Rama ")]
    assert headers[0] == f"Rama lictor/uno · {run_uno.worktree}"
    assert headers[1].startswith("Rama lictor/dos · ")
    assert len(headers) == 2, "a run whose worktree is gone isn't shown"
    assert "\n\nRama lictor/dos" in text.stdout
    runs = json.loads(raw.stdout)["runs"]
    assert [(run["branch"], run["run_id"]) for run in runs[:1]] == [("lictor/uno", run_uno.run_id)]
    assert [run["branch"] for run in runs] == ["lictor/uno", "lictor/dos"]
    assert [run["branch"] for run in json.loads(one.stdout)["runs"]] == ["lictor/dos"]


def test_status_without_any_run_says_so(project: Path) -> None:
    result = runner.invoke(app, ["status", "--cwd", str(project)])

    assert result.exit_code == 0
    assert result.stdout == "No hay ninguna ejecución registrada en el proyecto.\n"


def test_report_of_a_branch_is_written_in_its_worktree(project: Path, run_uno: RunEntry) -> None:
    result = runner.invoke(app, ["report", "--branch", "uno", "--cwd", str(project)])

    assert result.exit_code == 0, result.output
    report = Path(run_uno.worktree) / ".lictor" / "report.md"
    assert result.stdout == f"Informe generado en: {report}\n"
    assert report.is_file()
    assert not (project / ".lictor" / "report.md").exists()


def _retire(project: Path, *args: str) -> Any:
    return runner.invoke(app, ["retire", *args, "--cwd", str(project)])


def test_retire_refuses_a_run_that_can_still_go_on(project: Path, run_uno: RunEntry) -> None:
    result = _retire(project, "uno")

    assert result.exit_code == 10
    assert "se puede reanudar" in result.stderr
    assert "`lictor run --branch uno`" in result.stderr
    assert Path(run_uno.worktree).is_dir()
    assert read_run_index(project) == [run_uno]


def test_retire_refuses_a_worktree_with_uncommitted_changes(
    project: Path, run_uno: RunEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _states(monkeypatch, uno=FINISHED)
    (Path(run_uno.worktree) / "notas.txt").write_text("x\n", encoding="utf-8")

    result = _retire(project, "uno")

    assert result.exit_code == 10
    assert f"el worktree {run_uno.worktree} tiene cambios sin confirmar: notas.txt" in result.stderr
    assert Path(run_uno.worktree, "notas.txt").is_file()
    assert read_run_index(project) == [run_uno]


def test_retire_removes_a_finished_runs_worktree_and_keeps_its_branch(
    project: Path, run_uno: RunEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _states(monkeypatch, uno=FINISHED)

    result = _retire(project, "uno")

    assert result.exit_code == 0, result.output
    assert not Path(run_uno.worktree).exists()
    assert read_run_index(project) == []
    assert "lictor/uno" in _branches(project)
    assert f"Worktree retirado: {run_uno.worktree}" in result.stdout
    assert "`git branch -d lictor/uno`" in result.stdout


def test_retire_deletes_an_integrated_branch_when_asked(
    project: Path, run_uno: RunEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _states(monkeypatch, uno=FINISHED)

    result = _retire(project, "uno", "--delete-branch")

    assert result.exit_code == 0, result.output
    assert "lictor/uno" not in _branches(project)
    assert "Rama lictor/uno borrada." in result.stdout


def test_retire_keeps_a_branch_that_isnt_integrated_even_when_asked(
    project: Path, run_uno: RunEntry, monkeypatch: pytest.MonkeyPatch
) -> None:
    _states(monkeypatch, uno=FINISHED)
    worktree = Path(run_uno.worktree)
    git(worktree, "commit", "-q", "--allow-empty", "-m", "test: sin integrar")

    result = _retire(project, "uno", "--delete-branch")

    assert result.exit_code == 10
    assert "Intégrala primero y bórrala con `git branch -d lictor/uno`" in result.stderr
    assert "lictor/uno" in _branches(project)
    assert not worktree.exists(), "the worktree goes; only the branch stays"
    assert read_run_index(project) == []


def _at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CliRunner` swaps `sys.stdin` on each call; `cli` only asks it whether it is a terminal."""
    terminal = SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(cli, "sys", terminal)


@pytest.mark.parametrize(("answer", "deleted"), [("y\n", True), ("n\n", False)])
def test_retire_at_a_terminal_asks_before_deleting_the_branch(
    project: Path,
    run_uno: RunEntry,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    deleted: bool,
) -> None:
    _states(monkeypatch, uno=FINISHED)
    _at_a_terminal(monkeypatch)

    result = runner.invoke(app, ["retire", "uno", "--cwd", str(project)], input=answer)

    assert result.exit_code == 0, result.output
    assert "¿Borro también la rama lictor/uno?" in result.stdout
    assert ("lictor/uno" not in _branches(project)) is deleted


def test_retire_of_a_run_whose_worktree_is_gone_only_drops_it_from_the_index(
    project: Path, run_uno: RunEntry
) -> None:
    git(project, "worktree", "remove", run_uno.worktree)

    result = _retire(project, "uno")

    assert result.exit_code == 0, result.output
    assert read_run_index(project) == []
    assert "Worktree retirado" not in result.stdout


def _register(project: Path, *args: str) -> Any:
    return runner.invoke(app, ["register", *args, "--cwd", str(project)])


def test_register_with_yes_commits_the_assertion_on_top_of_head(project: Path) -> None:
    head = git(project, "rev-parse", "HEAD")

    result = _register(project, "IVA-001", "--yes")

    assert result.exit_code == 0, result.output
    assert "- Afirmas que el código de HEAD ya cumple IVA-001." in result.stdout
    sha = git(project, "rev-parse", "HEAD")
    assert git(project, "rev-parse", "HEAD^") == head
    assert f"Registrados en {sha[:12]}: IVA-001." in result.stdout
    message = git(project, "log", "-1", "--format=%B")
    assert "Scenario: IVA-001" in message
    assert "Lictor-Run" not in message


def test_register_without_a_terminal_asks_for_yes_and_registers_nothing(project: Path) -> None:
    head = git(project, "rev-parse", "HEAD")

    result = _register(project, "IVA-001")

    assert result.exit_code == 10
    assert "- Afirmas que el código de HEAD ya cumple IVA-001." in result.stdout
    assert "Sin terminal para confirmarlo: vuelve a lanzarlo con --yes." in result.stderr
    assert "No se ha registrado nada." in result.stderr
    assert git(project, "rev-parse", "HEAD") == head


def test_register_at_a_terminal_registers_only_on_yes(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = git(project, "rev-parse", "HEAD")
    _at_a_terminal(monkeypatch)

    refused = runner.invoke(app, ["register", "IVA-001", "--cwd", str(project)], input="n\n")

    assert refused.exit_code == 10
    assert git(project, "rev-parse", "HEAD") == head

    agreed = runner.invoke(app, ["register", "IVA-001", "--cwd", str(project)], input="y\n")

    assert agreed.exit_code == 0, agreed.output
    assert git(project, "rev-parse", "HEAD^") == head


def test_register_lists_every_problem_and_registers_nothing(project: Path) -> None:
    head = git(project, "rev-parse", "HEAD")

    result = _register(project, "IVA-001", "IVA-999", "--yes")

    assert result.exit_code == 10
    assert "✗ " in result.stderr
    assert "IVA-999" in result.stderr
    assert "Afirmas" not in result.stdout
    assert git(project, "rev-parse", "HEAD") == head


def test_register_refuses_while_a_run_can_be_resumed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = git(project, "rev-parse", "HEAD")
    monkeypatch.setattr(cli, "_resumable_run", lambda _graph, _file: ("r1", PAUSED))

    result = _register(project, "IVA-001", "--yes")

    assert result.exit_code == 10
    assert "hay una ejecución a medias y registrar movería la rama que ancló" in result.stderr
    assert git(project, "rev-parse", "HEAD") == head
