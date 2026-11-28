"""Unit tests for the checks every command makes before spending anything."""

import contextlib
import dataclasses
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock

import pytest

from lictor.application.api_baseline import ApiBaseline
from lictor.application.ports import Executor, ReportReaders, Vcs
from lictor.application.skill import install_skill_stub
from lictor.application.start_survey import StartSurvey
from lictor.application.usecases import preconditions
from lictor.application.usecases.preconditions import (
    CheckPreconditions,
    PreconditionCheck,
    PreconditionsReport,
    PreconditionsRequest,
)
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.api import ApiBase
from lictor.domain.blind_spots import StartRules
from lictor.domain.controls import CommandControl, ControlMode, ReaderControl
from lictor.domain.conventions import TreeEntry
from lictor.domain.errors import EnvironmentUnavailableError, InternalLictorError
from lictor.domain.model import CommandOutput, Health, PreconditionProblem
from lictor.domain.proof_level import ProofDefences
from lictor.domain.refactor_scope import CodePaths
from lictor.domain.reports import ApiReport, ApiSymbol
from lictor.domain.rules import NO_COMMITS
from lictor.domain.survey import (
    LaunchedProgram,
    ProjectMetadata,
    SurveyedCommand,
    SurveyedTests,
    SurveyReport,
)

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

    def __init__(
        self,
        tmp_path: Path,
        bin_dir: Path,
        commands: dict[str, str],
        check_stage_commands: dict[str, str] | None = None,
        check_stage_ids: list[str] | None = None,
    ) -> None:
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
            self.vcs,
            self.executor,
            self.environment,
            tmp_path,
            commands,
            check_stage_ids=(
                check_stage_ids if check_stage_ids is not None else list(check_stage_commands or {})
            ),
            check_stage_commands=check_stage_commands or {},
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


def _with_check_stage(tmp_path: Path, bin_dir: Path) -> _Parts:
    """A run that verifies with `herramienta` and leaves `mutation` and `audit` to `check`."""
    return _Parts(
        tmp_path,
        bin_dir,
        {"del control 'a'": "herramienta"},
        check_stage_commands={"mutation": "no-instalado run", "audit": "herramienta audit"},
    )


def test_a_run_doesnt_look_for_the_programs_of_the_check_controls(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_check_stage(tmp_path, bin_dir)

    report = parts.use_case.execute(RUN)

    assert report.problems == []
    assert [check.name for check in report.checks] == NAMES


def test_a_resume_doesnt_look_for_the_programs_of_the_check_controls(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_check_stage(tmp_path, bin_dir)

    resume = PreconditionsRequest(run_id="r1", clean_tree=False, environment="none")

    assert parts.check(resume) == []


def test_doctor_looks_for_every_program_and_warns_of_the_check_controls(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_check_stage(tmp_path, bin_dir)

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", check_stage=True))

    # A run would start without it: only `lictor check` fails, so it warns and stops nothing.
    assert report.problems == []
    commands = next(c for c in report.checks if c.name == "Comandos de verificación")
    assert commands.warnings == (
        "No se encuentra 'no-instalado', con el que empieza el comando del control "
        "'mutation' ('no-instalado run'). Instálalo o corrige el comando en lictor.json. "
        "Un run no lo necesita: solo lo ejecuta `lictor check`.",
    )
    warned = next(c for c in report.checks if c.name == "Momento de los controles")
    assert warned.problems == ()
    assert warned.warnings == (
        "Controles que solo corren en `lictor check`: mutation y audit. Un run no los "
        "ejecuta: solo protegen lo que integras si tu CI ejecuta `lictor check`.",
    )


def test_doctor_warns_of_a_reader_only_in_check_and_looks_for_no_program_of_it(
    tmp_path: Path, bin_dir: Path
) -> None:
    """ADR-0008: a reader runs nothing, but a run still leaves it to `lictor check`."""
    parts = _Parts(
        tmp_path, bin_dir, {"del control 'a'": "herramienta"}, check_stage_ids=["coverage"]
    )

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", check_stage=True))

    assert report.problems == []
    warned = next(c for c in report.checks if c.name == "Momento de los controles")
    assert "solo corre en `lictor check`: coverage." in warned.warnings[0]
    commands = next(c for c in report.checks if c.name == "Comandos de verificación")
    assert commands.warnings == ()


def test_doctor_still_fails_on_an_empty_command_of_a_check_control(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _Parts(
        tmp_path, bin_dir, {"del control 'a'": "herramienta"}, check_stage_commands={"audit": " "}
    )

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", check_stage=True))

    assert [p.message for p in report.problems] == ["El comando del control 'audit' está vacío"]


def test_doctor_says_nothing_of_the_stage_when_every_control_runs_in_each_attempt(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _parts(tmp_path, bin_dir)

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", check_stage=True))

    assert [check.name for check in report.checks] == NAMES


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
    """F30: with no executor or environment to build, `doctor` still tells everything else.

    Without a configuration there are no `check` controls to warn of (ADR-0007).
    """
    vcs = MagicMock(spec=Vcs)
    vcs.has_commits.return_value = True
    vcs.status.return_value = []
    vcs.committer_identity.return_value = "Ana <ana@example.com>"
    use_case = CheckPreconditions(vcs, None, None, tmp_path)

    report = use_case.execute(
        PreconditionsRequest(
            run_id="r1", environment="throwaway", config_problem="no carga", check_stage=True
        )
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


SURVEYED = SurveyReport(
    code_dir="src",
    modules=("pkg",),
    commands=(
        SurveyedCommand("a", True, tests=True, paths=("tests/unit",)),
        SurveyedCommand("lint", True, program=LaunchedProgram("linter", None, False)),
    ),
    tests=(SurveyedTests("tests/other/test_x.py", 2),),
)
CONTRACT = [
    *NAMES[:-1],
    "Entorno de verificación",
    preconditions.DEPENDENCIES,
    preconditions.BLIND_SPOTS,
    preconditions.HEAD_CONTROLS,
]


def _with_contract(
    tmp_path: Path, bin_dir: Path, surveyed: SurveyReport | str | None = SURVEYED
) -> _Parts:
    parts = _parts(tmp_path, bin_dir)
    parts.environment.installed_plugins = ("otro",)
    parts.use_case.needs_plugins = {"bdd.plugin": "añádelo"}
    parts.use_case.start_rules = StartRules(("src/", "tests/unit/"), ("tests/acceptance/",))
    if surveyed is not None:
        survey = MagicMock(spec=StartSurvey)
        survey.execute.return_value = surveyed
        parts.use_case.survey = survey
    return parts


def test_without_a_survey_dir_the_start_contract_isnt_checked(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_contract(tmp_path, bin_dir)

    report = parts.use_case.execute(RUN)

    assert [check.name for check in report.checks] == NAMES
    assert report.blind_spots is None


def test_the_start_contract_is_checked_on_the_environment_just_built(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_contract(tmp_path, bin_dir)
    request = PreconditionsRequest(run_id="r1", survey_dir=tmp_path / "start")

    report = parts.use_case.execute(request)

    assert [check.name for check in report.checks] == CONTRACT
    dependencies, spots = report.checks[-3:-1]
    assert [p.kind for p in dependencies.problems] == ["config", "config"]
    assert "bdd.plugin" in dependencies.problems[0].message
    assert "'linter'" in dependencies.problems[1].message
    assert spots.problems == ()
    assert "tests/other/test_x.py" in spots.warnings[0]
    assert report.blind_spots is not None
    assert parts.use_case.survey is not None
    survey_execute = parts.use_case.survey.execute
    assert isinstance(survey_execute, MagicMock)
    survey_execute.assert_called_once_with(
        parts.environment, parts.vcs, tmp_path, tmp_path / "start"
    )


def test_doctor_surveys_inside_the_throwaway_before_it_is_gone(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_contract(tmp_path, bin_dir)
    built = MagicMock(spec=VerificationEnvironment)
    built.installed_plugins = ("bdd.plugin",)
    alive: list[bool] = []

    @contextlib.contextmanager
    def throwaway(rev: str, log_dir: Path) -> Iterator[MagicMock]:
        alive.append(True)
        yield built
        alive.append(False)

    parts.use_case.aside = MagicMock(spec=VerificationEnvironment)
    parts.use_case.aside.throwaway.side_effect = throwaway
    assert parts.use_case.survey is not None
    survey_execute = parts.use_case.survey.execute
    assert isinstance(survey_execute, MagicMock)
    survey_execute.side_effect = lambda env, *_: alive[-1] and SURVEYED

    report = parts.use_case.execute(
        PreconditionsRequest(
            run_id="d1",
            environment="throwaway",
            log_dir=tmp_path / "doctor",
            survey_dir=tmp_path / "doctor" / "survey",
        )
    )

    dependencies = report.checks[-3]
    assert [p.message for p in dependencies.problems if "bdd.plugin" in p.message] == []
    assert survey_execute.call_args.args[0] is built
    assert report.blind_spots is not None


@pytest.mark.parametrize(
    ("metadata", "detail", "warned"),
    [
        (ProjectMetadata("pkg", "1.0"), "metadata del proyecto pkg 1.0", False),
        (ProjectMetadata(problem="la versión sale de git"), None, True),
    ],
)
def test_dependencies_tell_the_projects_metadata_and_a_run_keeps_the_warning(
    tmp_path: Path, bin_dir: Path, metadata: ProjectMetadata, detail: str | None, warned: bool
) -> None:
    parts = _with_contract(tmp_path, bin_dir, dataclasses.replace(SURVEYED, metadata=metadata))

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))

    dependencies = report.checks[-3]
    assert dependencies.detail == detail
    told = [w for w in dependencies.warnings if "la versión sale de git" in w]
    assert bool(told) is warned
    assert report.dependency_warnings == dependencies.warnings


def test_a_survey_that_fails_only_warns(tmp_path: Path, bin_dir: Path) -> None:
    parts = _with_contract(tmp_path, bin_dir, "el análisis no dejó informe")

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))

    dependencies, spots = report.checks[-3:-1]
    assert ["bdd.plugin" in p.message for p in dependencies.problems] == [True]
    assert spots.problems == ()
    assert spots.warnings == (
        "no se pudo leer lo que ve cada control: el análisis no dejó informe",
    )
    assert report.blind_spots is None


def test_a_profile_without_a_survey_checks_only_the_plugins(tmp_path: Path, bin_dir: Path) -> None:
    parts = _with_contract(tmp_path, bin_dir, None)

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))

    dependencies, spots = report.checks[-3:-1]
    assert len(dependencies.problems) == 1
    assert spots.skipped == preconditions.NO_SURVEY


def test_a_survey_without_the_projects_code_is_unchecked(tmp_path: Path, bin_dir: Path) -> None:
    empty = SurveyReport(code_dir="src", modules=(), commands=())
    parts = _with_contract(tmp_path, bin_dir, empty)

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))

    assert report.checks[-2].skipped == "no comprobado: no hay código del proyecto en src/"


def test_without_an_environment_the_start_contract_says_why(tmp_path: Path, bin_dir: Path) -> None:
    parts = _with_contract(tmp_path, bin_dir)
    request = PreconditionsRequest(
        run_id="d1", environment="none", skip_reason="hay un run en curso", survey_dir=tmp_path
    )

    report = parts.use_case.execute(request)

    assert [c.skipped for c in report.checks[-4:]] == ["hay un run en curso"] * 4
    parts.environment.ensure.side_effect = EnvironmentUnavailableError("prepare falló")
    failed = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))
    assert [c.skipped for c in failed.checks[-3:]] == [
        "no comprobado: el entorno no se pudo construir"
    ] * 3


def test_without_a_configuration_the_start_contract_is_unchecked(tmp_path: Path) -> None:
    vcs = MagicMock(spec=Vcs)
    vcs.has_commits.return_value = True
    vcs.status.return_value = []
    use_case = CheckPreconditions(vcs, None, None, tmp_path)

    report = use_case.execute(PreconditionsRequest(run_id="d1", survey_dir=tmp_path))

    assert [c.skipped for c in report.checks[-3:]] == [preconditions.UNCONFIGURED] * 3


def _blocking(control_id: str, mode: str = "block") -> CommandControl:
    return CommandControl(
        id=control_id, level="L1", run=f"herramienta {control_id}", report="exit-code", mode=mode
    )


def _with_head_controls(
    tmp_path: Path, bin_dir: Path, codes: dict[str, int]
) -> tuple[_Parts, PreconditionsReport]:
    parts = _with_contract(tmp_path, bin_dir)
    parts.use_case.head_controls = [_blocking(cid) for cid in codes] + [_blocking("m", "measure")]
    parts.use_case.readers = MagicMock(spec=ReportReaders)
    parts.environment.ensure.return_value = None

    def run(cmd: Sequence[str], cwd: Path, timeout_s: int, *_: object) -> CommandOutput:
        return CommandOutput(returncode=codes.get(cmd[1], 0), stdout="", stderr="")

    parts.environment.run.side_effect = run
    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path / "s"))
    return parts, report


def test_a_blocking_control_that_fails_on_head_is_the_projects_to_fix(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts, report = _with_head_controls(tmp_path, bin_dir, {"lint": 0, "format": 1, "unit": 2})

    head = report.checks[-1]
    assert head.name == preconditions.HEAD_CONTROLS
    assert [p.kind for p in head.problems] == ["config", "config"]
    assert "El control 'format' no pasa en HEAD" in head.problems[0].message
    assert "El control 'unit' no pasa en HEAD" in head.problems[1].message
    assert "en lictor.json" in head.problems[0].message
    ran = [call.args[0][1] for call in parts.environment.run.call_args_list]
    assert "m" not in ran, "a control that only measures blocks nothing"
    assert (tmp_path / "s" / "controles").is_dir()


def test_controls_that_pass_on_head_leave_nothing_to_fix(tmp_path: Path, bin_dir: Path) -> None:
    _, report = _with_head_controls(tmp_path, bin_dir, {"lint": 0})

    assert report.checks[-1].problems == ()
    assert report.checks[-1].skipped is None


def test_without_blocking_controls_nothing_runs_on_head(tmp_path: Path, bin_dir: Path) -> None:
    _, report = _with_head_controls(tmp_path, bin_dir, {})

    assert report.checks[-1].skipped == "ningún control bloquea"


def test_a_control_whose_program_cant_start_isnt_run_on_head(tmp_path: Path, bin_dir: Path) -> None:
    # «Comandos de verificación» already says it, as the machine's: running it would fail again
    # and make it the project's, turning a 40 into a 10.
    parts = _with_contract(tmp_path, bin_dir)
    missing = CommandControl(id="falta", level="L1", run="no-existe-xyz", report="exit-code")
    reader = ReaderControl(id="cobertura", level="L4", reads="falta", adds="--cov", mode="block")
    parts.use_case.head_controls = [_blocking("lint"), missing, reader]
    parts.use_case.readers = MagicMock(spec=ReportReaders)
    parts.environment.ensure.return_value = None
    parts.environment.run.return_value = CommandOutput(returncode=0, stdout="", stderr="")

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path / "s"))

    head = report.checks[-1]
    assert head.problems == ()
    assert head.warnings == (
        "No se ejecutan en HEAD 'falta', 'cobertura': su programa no arranca, como dice "
        "«Comandos de verificación».",
    )
    assert [call.args[0] for call in parts.environment.run.call_args_list] == [
        ["herramienta", "lint"]
    ]


def test_with_no_blocking_control_able_to_start_head_is_unchecked(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_contract(tmp_path, bin_dir)
    missing = CommandControl(id="falta", level="L1", run="no-existe-xyz", report="exit-code")
    parts.use_case.head_controls = [missing]
    parts.use_case.readers = MagicMock(spec=ReportReaders)
    parts.environment.ensure.return_value = None

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path / "s"))

    assert report.checks[-1].skipped == preconditions.NONE_STARTS
    parts.environment.run.assert_not_called()


def test_a_control_that_changes_the_environment_on_head_is_the_machines(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_contract(tmp_path, bin_dir)
    parts.use_case.head_controls = [_blocking("lint")]
    parts.use_case.readers = MagicMock(spec=ReportReaders)
    parts.environment.run.return_value = CommandOutput(returncode=0, stdout="", stderr="")
    parts.environment.ensure.side_effect = [None, "el entorno cambió"]

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path / "s"))

    assert report.checks[-1].problems == (PreconditionProblem("environment", "el entorno cambió"),)


_HEAD = "h" * 40
_FORK = "f" * 40


def _symbols(*names: str) -> ApiReport:
    return ApiReport(tuple(ApiSymbol(n, "function", n, "src/p.py", 1) for n in names))


@dataclass(frozen=True)
class _Apis:
    """What HEAD measures, what where it left the base measures, and that commit."""

    base: ApiReport | str
    head: ApiReport | str
    fork: str | None = _FORK


_HEAD_LOST_G = _Apis(base=_symbols("p.f", "p.g"), head=_symbols("p.f"))


def _against_base(
    tmp_path: Path, bin_dir: Path, apis: _Apis = _HEAD_LOST_G, mode: ControlMode = "block"
) -> PreconditionsReport:
    """`api.base` set, with an API control in `mode`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    parts = _with_contract(tmp_path, bin_dir)
    parts.use_case.head_controls = [
        CommandControl(id="api", level="L1", run="herramienta api", report="api", mode=mode)
    ]
    parts.use_case.readers = MagicMock(spec=ReportReaders)
    parts.environment.ensure.return_value = None
    parts.environment.run.return_value = CommandOutput(returncode=0, stdout="", stderr="")
    parts.vcs.get_head_sha.return_value = _HEAD
    parts.vcs.resolve_commit.return_value = "r" * 40
    parts.vcs.merge_base.return_value = apis.fork
    api = MagicMock(spec=ApiBaseline)
    api.ref = "origin/main"
    measures = {_FORK: apis.base, _HEAD: apis.head}
    api.base.side_effect = lambda sha, *_: ApiBase(sha, measures[sha])
    parts.use_case.api = api
    return parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path / "s"))


def test_with_api_base_head_is_compared_where_it_left_it(tmp_path: Path, bin_dir: Path) -> None:
    report = _against_base(tmp_path, bin_dir, replace(_HEAD_LOST_G, head=_symbols("p.f", "p.g")))

    assert report.checks[-1].name == preconditions.API_BASE
    assert report.checks[-1].problems == ()
    assert report.api_start is not None
    assert report.api_start.base == _FORK
    assert report.api_start.broken == ()


def test_a_break_head_already_has_fails_every_attempt_when_api_blocks(
    tmp_path: Path, bin_dir: Path
) -> None:
    report = _against_base(tmp_path, bin_dir)

    (problem,) = report.checks[-1].problems
    assert problem.kind == "config"
    assert "HEAD ya rompe 1 nombre de la API pública respecto a «origin/main»" in problem.message
    assert "- `p.g`" in problem.message
    assert "api.accepted" in problem.message


def test_a_break_head_already_has_only_warns_when_api_measures(
    tmp_path: Path, bin_dir: Path
) -> None:
    report = _against_base(tmp_path, bin_dir, mode="measure")

    assert report.checks[-1].problems == ()
    assert "El run solo avisará de lo que rompa de nuevo." in report.checks[-1].warnings[0]
    assert report.api_start is not None
    assert [b.name for b in report.api_start.broken] == ["p.g"]


def test_a_base_with_no_common_ancestor_is_the_projects_to_fix(
    tmp_path: Path, bin_dir: Path
) -> None:
    report = _against_base(tmp_path, bin_dir, replace(_HEAD_LOST_G, fork=None))

    (problem,) = report.checks[-1].problems
    assert problem.kind == "config"
    assert "git fetch --deepen" in problem.message
    assert report.api_start is None


def test_a_base_that_cant_be_measured_blocks_only_where_api_blocks(
    tmp_path: Path, bin_dir: Path
) -> None:
    unmeasured = replace(_HEAD_LOST_G, base="la medida no dejó informe")
    blocking = _against_base(tmp_path / "b", bin_dir, unmeasured)
    measuring = _against_base(tmp_path / "m", bin_dir, unmeasured, mode="measure")

    assert "la medida no dejó informe" in blocking.checks[-1].problems[0].message
    assert measuring.checks[-1].problems == ()
    assert "la medida no dejó informe" in measuring.checks[-1].warnings[0]


_BARE = ProofDefences(
    link="name",
    red_kinds=False,
    stub_rule_c=False,
    declared_stubs=False,
    interface_impostor=False,
    gherkin=False,
    env_hashed=False,
    suppressions=False,
    blocking_controls=False,
    survey=False,
    plugin_controls=(("cov", "cov.plugin"),),
)


def _by_name(report: PreconditionsReport) -> dict[str, PreconditionCheck]:
    return {check.name: check for check in report.checks}


def test_the_level_of_proof_is_told_last_as_facts_that_stop_nothing(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_contract(tmp_path, bin_dir, None)
    parts.use_case.needs_plugins = {}
    parts.use_case.proof = _BARE
    parts.use_case.env_prepared = False

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))

    checks = _by_name(report)
    level = checks[preconditions.PROOF_LEVEL]
    assert report.checks[-1] is level
    assert level.detail is None
    assert level.problems == ()
    assert level.warnings[0] == "básico"
    assert level.warnings[1].startswith("No se comprueba que el test compare nada")
    assert "cov no corre: falta cov.plugin" in level.warnings
    assert report.proof is not None
    assert report.proof.level == "basico"
    assert checks["Entorno de verificación"].detail == preconditions.UNPREPARED
    assert checks[preconditions.DEPENDENCIES].skipped == preconditions.NO_NEEDS
    assert report.blind_spots is not None
    assert report.blind_spots.skipped == preconditions.NO_SURVEY


def test_without_the_environment_the_controls_of_a_plugin_are_unchecked(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_contract(tmp_path, bin_dir)
    parts.use_case.proof = _BARE
    parts.environment.ensure.side_effect = EnvironmentUnavailableError("prepare falló")

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))

    level = _by_name(report)[preconditions.PROOF_LEVEL]
    assert any(w.startswith("No se comprobó qué controles se saltan") for w in level.warnings)
    assert not any("cov no corre" in w for w in level.warnings)


def test_a_run_without_a_survey_dir_says_no_level(tmp_path: Path, bin_dir: Path) -> None:
    parts = _with_contract(tmp_path, bin_dir)
    parts.use_case.proof = _BARE

    report = parts.use_case.execute(RUN)

    assert report.proof is None
    assert preconditions.PROOF_LEVEL not in _by_name(report)


def test_what_every_attempt_runs_is_granted_and_only_an_opt_out_is_warned_of(
    tmp_path: Path, bin_dir: Path
) -> None:
    """S17: the acceptance and each blocking control are granted to the agent by an exact rule;
    only a control with `"agent": false` is left out, and `doctor` says so (D10)."""
    parts = _with_contract(tmp_path, bin_dir)
    parts.use_case.proof = _BARE
    parts.use_case.acceptance_run = "gotestsum --junitfile {out} -- ./acceptance/..."
    parts.use_case.agent_tools = ["Bash(go test:*)"]

    granted = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))

    assert preconditions.AGENT_COMMANDS not in _by_name(granted)
    parts.use_case.head_controls = [
        CommandControl(
            id="lint", level="L1", run="golangci-lint run", report="exit-code", grants_agent=False
        )
    ]
    left_out = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))
    told = _by_name(left_out)[preconditions.AGENT_COMMANDS]
    assert told.problems == ()
    assert told.warnings == (
        "agent_tools no deja al agente ejecutar el control lint (`golangci-lint run`): no podrá "
        "comprobarlo antes de acabar. Añade a agent_tools una regla Bash(<prefijo>:*) que lo "
        "permita.",
    )


def test_a_proved_level_with_nothing_missing_passes_saying_it(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _with_contract(tmp_path, bin_dir)
    parts.use_case.proof = dataclasses.replace(
        _BARE,
        link="scenario",
        red_kinds=True,
        stub_rule_c=True,
        declared_stubs=False,
        interface_impostor=True,
        gherkin=True,
        env_hashed=True,
        suppressions=True,
        blocking_controls=True,
        survey=True,
        plugin_controls=(),
    )

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))

    level = _by_name(report)[preconditions.PROOF_LEVEL]
    assert level.detail == "resultado probado"
    assert level.warnings == ()


def test_an_exclusion_that_leaves_no_code_of_head_is_the_projects(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _parts(tmp_path, bin_dir)
    parts.vcs.tree_entries.return_value = [TreeEntry("iva/iva.go", "100644")]
    parts.use_case.code_paths = CodePaths(("iva/",), ("*.go",))

    report = parts.use_case.execute(RUN)

    [problem] = [p for p in report.problems if p.kind == "config"]
    assert problem.message.startswith("code_exclude (*.go) excluye todos los ficheros")
    parts.use_case.code_paths = CodePaths(("iva/",), ("*_test.go",))
    assert [c.name for c in parts.use_case.execute(RUN).checks].count("Rutas de código") == 1
    parts.use_case.code_paths = CodePaths(("iva/",))
    assert "Rutas de código" not in [c.name for c in parts.use_case.execute(RUN).checks]


def test_without_code_in_head_the_exclusion_is_left_to_the_first_refactor(
    tmp_path: Path, bin_dir: Path
) -> None:
    parts = _parts(tmp_path, bin_dir)
    parts.vcs.tree_entries.return_value = [TreeEntry("features/nif.feature", "100644")]
    parts.use_case.code_paths = CodePaths(("iva/",), ("*.go",))

    report = parts.use_case.execute(RUN)

    [check] = [c for c in report.checks if c.name == "Rutas de código"]
    assert check.skipped == preconditions.NO_CODE_YET
    assert report.problems == []


def test_doctor_warns_of_an_installed_plugin_that_changes_the_order_of_the_tests(
    tmp_path: Path, bin_dir: Path
) -> None:
    """S11: `pytest-randomly` shuffled every command of the verification just by being there."""
    parts = _with_contract(tmp_path, bin_dir)
    parts.environment.installed_plugins = ("bdd.plugin", "pytest_randomly")
    parts.use_case.plugins_noted = {"pytest_randomly": "baraja el orden"}

    report = parts.use_case.execute(PreconditionsRequest(run_id="r1", survey_dir=tmp_path))

    told = _by_name(report)[preconditions.DEPENDENCIES]
    assert any(w.startswith("El plugin pytest_randomly está instalado") for w in told.warnings)
