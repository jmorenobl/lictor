"""Unit tests for what `lictor check` verifies: HEAD, or the working tree as a commit."""

import contextlib
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.adapters.vcs.git_cli import GitCli
from lictor.application.ports import AuditSink, Clock, EventLog
from lictor.application.usecases.check_project import CheckControlSpec
from lictor.application.usecases.check_working_tree import (
    CheckWorkingTree,
    CheckWorkingTreeRequest,
)
from lictor.application.verification_env import VerificationEnvironment, VerificationEnvSpec
from lictor.domain.errors import EnvironmentUnavailableError
from lictor.domain.model import CommandOutput

GIT = shutil.which("git") or "git"
LINT = CheckControlSpec("lint", "L1", "lint src")
UNIT = CheckControlSpec("unit", "L2", "unit", when_exists="tests/unit")


def _git(root: Path, *args: str) -> str:
    done = subprocess.run([GIT, *args], cwd=root, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proyecto"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Ana")
    _git(root, "config", "user.email", "ana@example.com")
    (root / ".gitignore").write_text(".lictor/\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "tests" / "unit" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "inicial")
    return root


class _SeeingRunner(FakeRunner):
    """Records what the clone held when each command ran."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[dict[str, str]] = []

    def run(
        self,
        cmd: list[str] | tuple[str, ...],
        cwd: Path,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandOutput:
        files = sorted(p for p in cwd.rglob("*") if p.is_file() and ".git" not in p.parts)
        self.seen.append({p.relative_to(cwd).as_posix(): p.read_text() for p in files})
        return super().run(cmd, cwd, timeout_s, env)


def _use_case(project: Path, runner: FakeRunner) -> CheckWorkingTree:
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-11-04T10:00:00Z"
    audit = AuditSink(MagicMock(spec=EventLog), clock)
    vcs = GitCli(project)
    environment = VerificationEnvironment(vcs, runner, audit, project, VerificationEnvSpec())
    readers = {"exit-code": ExitCodeReader()}
    return CheckWorkingTree(vcs, environment, readers, project, project / ".lictor" / "check")


def _request(**changes: object) -> CheckWorkingTreeRequest:
    base = CheckWorkingTreeRequest(run_id="check-1", controls=[LINT, UNIT])
    return CheckWorkingTreeRequest(**{**base.__dict__, **changes})


def test_a_clean_tree_verifies_head_in_a_clone(project: Path) -> None:
    runner = _SeeingRunner()
    stale = project / ".lictor" / "check" / "viejo.log"
    stale.parent.mkdir(parents=True)
    stale.write_text("de otro check", encoding="utf-8")

    result = _use_case(project, runner).execute(_request())

    head = _git(project, "rev-parse", "HEAD")
    assert result.subject == f"HEAD {head[:12]}"
    assert result.check.ok is True
    assert all(call["cwd"] != project for call in runner.calls)
    assert runner.seen[0]["src/a.py"] == "a = 1\n"
    assert not stale.exists()
    assert (project / ".lictor" / "check" / "lint.log").is_file()


def test_a_clean_tree_needs_no_identity(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_COMMITTER_NAME", "")

    result = _use_case(project, FakeRunner()).execute(_request())

    assert result.check.ok is True


def test_what_isnt_committed_is_verified_as_a_commit_outside_the_branch(project: Path) -> None:
    head = _git(project, "rev-parse", "HEAD")
    (project / "src" / "a.py").write_text("a = 2\n", encoding="utf-8")
    (project / "src" / "b.py").write_text("b = 1\n", encoding="utf-8")
    (project / "ignorado.log").write_text("x", encoding="utf-8")
    (project / ".gitignore").write_text(".lictor/\n*.log\n", encoding="utf-8")
    shutil.rmtree(project / "tests")
    runner = _SeeingRunner()

    result = _use_case(project, runner).execute(_request())

    assert result.subject == f"árbol de trabajo (4 ficheros sin confirmar) sobre HEAD {head[:12]}"
    seen = runner.seen[0]
    assert (seen["src/a.py"], seen["src/b.py"]) == ("a = 2\n", "b = 1\n")
    assert "ignorado.log" not in seen
    # The unit tests were removed, so their control can't pass by being skipped.
    assert result.check.control_results["unit"].ok is False
    assert _git(project, "rev-parse", "HEAD") == head
    assert _git(project, "status", "--short") != ""


def test_a_candidate_needs_an_identity(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "src" / "a.py").write_text("a = 2\n", encoding="utf-8")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "")
    runner = FakeRunner()

    with pytest.raises(EnvironmentUnavailableError, match="identidad"):
        _use_case(project, runner).execute(_request())
    assert runner.calls == []


def test_a_given_commit_is_verified_as_it_is(project: Path) -> None:
    first = _git(project, "rev-parse", "HEAD")
    (project / "src" / "a.py").write_text("a = 3\n", encoding="utf-8")
    _git(project, "commit", "-qam", "dos")
    runner = _SeeingRunner()

    result = _use_case(project, runner).execute(_request(rev=first))

    assert result.subject == f"commit {first[:12]}"
    assert runner.seen[0]["src/a.py"] == "a = 1\n"


def test_an_environment_that_changes_under_the_commands_is_reported(project: Path) -> None:
    use_case = _use_case(project, FakeRunner())
    changed = MagicMock(spec=VerificationEnvironment)
    changed.ensure.side_effect = [None, "cambió el entorno"]
    changed.run.return_value = CommandOutput(0, "", "")

    @contextlib.contextmanager
    def throwaway(rev: str, log_dir: Path) -> Iterator[MagicMock]:
        assert rev == _git(project, "rev-parse", "HEAD")
        assert log_dir == project / ".lictor" / "check"
        yield changed

    use_case.environment = MagicMock(spec=VerificationEnvironment)
    use_case.environment.throwaway.side_effect = throwaway

    result = use_case.execute(_request(controls=[LINT]))

    assert result.tampered == "cambió el entorno"
    assert changed.ensure.call_count == 2
