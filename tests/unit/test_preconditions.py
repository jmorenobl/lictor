"""Unit tests for the checks every command makes before spending anything."""

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock

import pytest

from lictor.application.ports import Executor, Vcs
from lictor.application.skill import install_skill_stub
from lictor.application.usecases import preconditions
from lictor.application.usecases.preconditions import CheckPreconditions, PreconditionsRequest
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.errors import EnvironmentUnavailableError, InternalLictorError
from lictor.domain.model import Health, PreconditionProblem
from lictor.domain.rules import NO_COMMITS

HEALTHY = Health(ok=True, cli_version="2.1.0", auth_status="subscription")
NAMES = [
    "Repositorio con commits",
    "Árbol git limpio",
    "Identidad git",
    "Ejecutor",
    "Directorio temporal",
    "Comandos de verificación",
    "Entorno de verificación",
]
RUN = PreconditionsRequest(run_id="r1")


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    tool = path / "herramienta"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)
    return path


class _Parts:
    """The doubles of one case, so each test reaches the one it needs."""

    def __init__(self, tmp_path: Path, bin_dir: Path, commands: dict[str, str]) -> None:
        self.vcs = MagicMock(spec=Vcs)
        self.vcs.has_commits.return_value = True
        self.vcs.status.return_value = []
        self.vcs.committer_identity.return_value = "Ana <ana@example.com>"
        self.vcs.is_executable.return_value = False
        self.executor = MagicMock(spec=Executor)
        self.executor.health.return_value = HEALTHY
        self.environment = MagicMock(spec=VerificationEnvironment)
        self.environment.search_path.return_value = str(bin_dir)
        self.use_case = CheckPreconditions(
            self.vcs, self.executor, self.environment, tmp_path, commands
        )

    def check(self, request: PreconditionsRequest = RUN) -> list[PreconditionProblem]:
        return self.use_case.execute(request).problems


def _parts(tmp_path: Path, bin_dir: Path, commands: dict[str, str] | None = None) -> _Parts:
    return _Parts(tmp_path, bin_dir, commands or {"del control 'a'": "herramienta --x"})


def test_everything_in_order_and_the_runs_environment_is_built(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _parts(tmp_path, bin_dir)

    report = parts.use_case.execute(RUN)

    assert [check.name for check in report.checks] == NAMES
    assert report.problems == []
    assert report.cli_version == "2.1.0"
    parts.environment.ensure.assert_called_once_with("r1")


def test_a_dirty_tree_is_the_repositorys_problem_and_nothing_is_built(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _parts(tmp_path, bin_dir)
    parts.vcs.status.return_value = ["src/a.py"]

    report = parts.use_case.execute(RUN)

    assert [p.kind for p in report.problems] == ["repository"]
    assert "src/a.py" in report.problems[0].message
    assert report.checks[-1].skipped == "no se construye: antes hay que resolver lo anterior"
    parts.environment.ensure.assert_not_called()


def test_in_the_persons_checkout_a_dirty_tree_is_only_a_warning(
    tmp_path: Path, bin_dir: Path
) -> None:
    """`run` from there starts from the last commit, in a worktree of its own (ADR-0005)."""
    parts = _parts(tmp_path, bin_dir)
    parts.vcs.status.return_value = ["src/a.py"]

    report = parts.use_case.execute(
        PreconditionsRequest(run_id="r1", uncommitted_warns=True, environment="none")
    )

    assert report.problems == []
    tree = report.checks[1]
    assert tree.name == NAMES[1]
    assert len(tree.warnings) == 1
    assert "src/a.py" in tree.warnings[0]
    assert "no entra" in tree.warnings[0]


def test_a_resume_doesnt_look_at_the_tree(tmp_path: Path, bin_dir: Path) -> None:
    parts = _parts(tmp_path, bin_dir)
    parts.vcs.status.return_value = ["src/a.py"]

    report = parts.use_case.execute(
        PreconditionsRequest(run_id="r1", clean_tree=False, environment="none", skip_reason="x")
    )

    assert report.problems == []
    assert [check.name for check in report.checks] == [NAMES[0], *NAMES[2:]]
    parts.vcs.status.assert_not_called()


def test_no_identity_is_the_machines_problem_with_gits_reason(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _parts(tmp_path, bin_dir)
    parts.vcs.committer_identity.side_effect = EnvironmentUnavailableError("git dice que no")

    assert parts.check() == [PreconditionProblem("environment", "git dice que no")]


def test_an_executor_that_isnt_ready_says_why(tmp_path: Path, bin_dir: Path) -> None:
    parts = _parts(tmp_path, bin_dir)
    parts.executor.health.return_value = Health(
        ok=False, cli_version="", auth_status="none", message="sin sesión"
    )

    report = parts.use_case.execute(RUN)

    assert report.problems == [
        PreconditionProblem("environment", "el ejecutor no está disponible: sin sesión")
    ]
    assert report.cli_version is None


def test_without_a_message_the_executor_gives_its_auth_status(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _parts(tmp_path, bin_dir)
    parts.executor.health.return_value = Health(ok=False, cli_version="", auth_status="none")

    assert parts.check()[0].message == "el ejecutor no está disponible: none"


def test_a_temporary_directory_that_isnt_private_stops_the_build(
    tmp_path: Path, bin_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_root: Path) -> None:
        raise EnvironmentUnavailableError("de otro usuario")

    monkeypatch.setattr(preconditions, "prepare_scratch", refuse)
    parts = _parts(tmp_path, bin_dir)

    report = parts.use_case.execute(
        PreconditionsRequest(run_id="r1", environment="throwaway", log_dir=tmp_path)
    )

    assert report.problems == [PreconditionProblem("environment", "de otro usuario")]
    assert report.checks[-1].skipped == "no se puede construir sin directorio temporal"
    parts.environment.throwaway.assert_not_called()


def test_a_program_is_looked_up_on_the_path_the_commands_get(tmp_path: Path, bin_dir: Path) -> None:
    parts = _parts(
        tmp_path,
        bin_dir,
        {"del control 'a'": "herramienta", "del control 'b'": "otra-que-no-esta"},
    )

    problems = parts.check()

    assert len(problems) == 1
    assert "'otra-que-no-esta'" in problems[0].message


def test_a_relative_program_must_be_committed_and_executable(tmp_path: Path, bin_dir: Path) -> None:
    parts = _parts(tmp_path, bin_dir, {"del control 'a'": "./scripts/../scripts/run.sh"})
    parts.vcs.is_executable.return_value = True

    assert parts.check() == []
    parts.vcs.is_executable.assert_called_once_with("HEAD", "scripts/run.sh")


def test_an_absolute_program_is_looked_up_on_disk(tmp_path: Path, bin_dir: Path) -> None:
    plain = bin_dir / "sin-permiso"
    plain.write_text("x", encoding="utf-8")
    commands = {
        "del control 'a'": str(bin_dir / "herramienta"),
        "del control 'b'": str(plain),
        "del control 'c'": str(bin_dir),
    }
    parts = _parts(tmp_path, bin_dir, commands)

    problems = parts.check()

    assert [str(plain) in p.message or str(bin_dir) in p.message for p in problems] == [True, True]
    parts.vcs.is_executable.assert_not_called()


def test_doctor_builds_a_throwaway_even_after_other_problems(tmp_path: Path, bin_dir: Path) -> None:
    parts = _parts(tmp_path, bin_dir)
    parts.vcs.status.return_value = ["sucio.py"]
    built = MagicMock(spec=VerificationEnvironment)

    @contextlib.contextmanager
    def throwaway(rev: str, log_dir: Path) -> Iterator[MagicMock]:
        assert (rev, log_dir) == ("HEAD", tmp_path / "doctor")
        yield built

    parts.use_case.aside = MagicMock(spec=VerificationEnvironment)
    parts.use_case.aside.throwaway.side_effect = throwaway

    report = parts.use_case.execute(
        PreconditionsRequest(run_id="d1", environment="throwaway", log_dir=tmp_path / "doctor")
    )

    assert report.checks[-1].problems == ()
    assert report.checks[-1].skipped is None
    built.ensure.assert_called_once_with("d1")
    parts.environment.ensure.assert_not_called()
    parts.environment.throwaway.assert_not_called()


def test_a_throwaway_needs_somewhere_for_its_log(tmp_path: Path, bin_dir: Path) -> None:
    parts = _parts(tmp_path, bin_dir)

    with pytest.raises(InternalLictorError):
        parts.use_case.execute(PreconditionsRequest(run_id="d1", environment="throwaway"))


def test_without_an_environment_check_it_says_why(tmp_path: Path, bin_dir: Path) -> None:
    parts = _parts(tmp_path, bin_dir)

    report = parts.use_case.execute(
        PreconditionsRequest(run_id="d1", environment="none", skip_reason="ocupado")
    )

    assert report.checks[-1].skipped == "ocupado"
    parts.environment.ensure.assert_not_called()


def test_a_prepare_that_fails_is_the_machines_problem(tmp_path: Path, bin_dir: Path) -> None:
    parts = _parts(tmp_path, bin_dir)
    parts.environment.ensure.side_effect = EnvironmentUnavailableError("sin red")

    report = parts.use_case.execute(RUN)

    assert report.checks[-1].problems == (PreconditionProblem("environment", "sin red"),)


@pytest.mark.parametrize("environment", ["run", "throwaway"])
def test_no_commit_is_the_projects_problem_and_nothing_reads_head(
    tmp_path: Path, bin_dir: Path, environment: Literal["run", "throwaway"]
) -> None:
    parts = _parts(tmp_path, bin_dir, {"del control 'a'": "scripts/check.sh --x"})
    parts.vcs.has_commits.return_value = False
    request = PreconditionsRequest(run_id="r1", environment=environment, log_dir=tmp_path / "log")

    report = parts.use_case.execute(request)

    assert report.checks[0].problems == (PreconditionProblem("repository", NO_COMMITS),)
    # A relative program can't be judged without a HEAD; the missing commit already says why.
    assert report.problems == [PreconditionProblem("repository", NO_COMMITS)]
    assert report.checks[-1].skipped == "no se construye: no hay ningún commit"
    parts.vcs.is_executable.assert_not_called()
    parts.environment.ensure.assert_not_called()
    parts.environment.throwaway.assert_not_called()


def test_a_skill_stub_of_another_version_is_a_warning_only_doctor_asks_for(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _parts(tmp_path, bin_dir)
    install_skill_stub(tmp_path).write_text("---\nname: lictor\n---\nviejo\n", encoding="utf-8")

    assert "Skill de Lictor" not in [check.name for check in parts.use_case.execute(RUN).checks]
    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", skill=True))

    skill = next(check for check in report.checks if check.name == "Skill de Lictor")
    assert skill.problems == ()
    assert "ejecuta `lictor skill`" in skill.warnings[0]
    assert report.problems == []


def test_doctor_says_nothing_of_a_project_without_the_skill(tmp_path: Path, bin_dir: Path) -> None:
    report = _parts(tmp_path, bin_dir).use_case.execute(
        PreconditionsRequest(run_id="r1", skill=True)
    )

    assert "Skill de Lictor" not in [check.name for check in report.checks]


def test_without_a_config_what_needs_it_is_skipped_and_the_rest_is_checked(
    tmp_path: Path,
) -> None:
    """F30: with no executor or environment to build, `doctor` still tells everything else."""
    vcs = MagicMock(spec=Vcs)
    vcs.has_commits.return_value = True
    vcs.status.return_value = []
    vcs.committer_identity.return_value = "Ana <ana@example.com>"
    use_case = CheckPreconditions(vcs, None, None, tmp_path)

    report = use_case.execute(
        PreconditionsRequest(run_id="r1", environment="throwaway", config_problem="no carga")
    )

    assert [(c.name, c.skipped) for c in report.checks] == [
        ("Configuración", None),
        ("Repositorio con commits", None),
        ("Árbol git limpio", None),
        ("Identidad git", None),
        ("Ejecutor", "no comprobado: la configuración no carga"),
        ("Directorio temporal", None),
        ("Comandos de verificación", "no comprobado: la configuración no carga"),
        ("Entorno de verificación", "no comprobado: la configuración no carga"),
    ]
    assert [(p.kind, p.message) for p in report.problems] == [("config", "no carga")]
    assert report.cli_version is None
