from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from lictor.adapters.runner.fake import FakeRunner
from lictor.application.usecases.check_project import (
    CheckAcceptanceSpec,
    CheckProject,
    CheckProjectRequest,
    CheckProjectResult,
)
from lictor.domain.controls import CommandControl, Control, ReaderControl
from lictor.domain.errors import ConfigError
from lictor.domain.model import CommandOutput
from lictor.domain.reports import CoverageReport
from lictor.domain.results import Read, from_failed_run
from lictor.domain.stages import CHECK
from tests.readers import FlagRunner, Leaves, ReportingRunner, all_readers, coverage_json
from tests.results import ids, seen


def test_check_project_all_controls_and_acceptance_pass(tmp_path: Path) -> None:
    runner = ReportingRunner()
    runner.set_response("echo lint", CommandOutput(returncode=0, stdout="ok", stderr=""))
    junit_xml = '<testsuites><testsuite name="s"><testcase name="test_1"/></testsuite></testsuites>'
    runner.set_report("echo test", CommandOutput(returncode=0, stdout="", stderr=""), junit_xml)
    readers = all_readers()
    use_case = CheckProject(runner=runner, readers=readers)

    controls = [
        CommandControl(id="lint", level="L1", run="echo lint", report="exit-code"),
    ]
    acceptance = CheckAcceptanceSpec(run="echo test {out}")

    req = CheckProjectRequest(
        project_root=tmp_path,
        controls=controls,
        acceptance=acceptance,
        installed_plugins=(),
    )
    res = use_case.execute(req)

    assert res.ok is True
    assert res.failed_at is None
    assert "lint" in ids(res)
    assert seen(res, "lint").ok is True
    assert "acceptance" in ids(res)
    assert seen(res, "acceptance").ok is True
    log = tmp_path / ".lictor" / "check" / "acceptance.log"
    assert log.read_text(encoding="utf-8").startswith("Comando: echo test ")


def test_check_project_blocking_control_fails_and_stops_early(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("echo fail", CommandOutput(returncode=1, stdout="lint error", stderr=""))
    runner.set_response("echo pass", CommandOutput(returncode=0, stdout="ok", stderr=""))
    readers = all_readers()
    use_case = CheckProject(runner=runner, readers=readers)

    controls = [
        CommandControl(id="lint", level="L1", run="echo fail", report="exit-code", mode="block"),
        CommandControl(id="types", level="L1", run="echo pass", report="exit-code", mode="block"),
    ]

    req = CheckProjectRequest(project_root=tmp_path, controls=controls, installed_plugins=())
    res = use_case.execute(req)

    assert res.ok is False
    assert res.failed_at == "lint"
    assert "types" not in ids(res)
    assert "lint error" in (res.condensed_error or "")


def test_check_project_measure_mode_does_not_block(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response(
        "echo mut", CommandOutput(returncode=1, stdout="mutant survived", stderr="")
    )
    runner.set_response("echo types", CommandOutput(returncode=0, stdout="types ok", stderr=""))
    readers = all_readers()
    use_case = CheckProject(runner=runner, readers=readers)

    controls = [
        CommandControl(
            id="mutation", level="L5", run="echo mut", report="exit-code", mode="measure"
        ),
        CommandControl(id="types", level="L1", run="echo types", report="exit-code", mode="block"),
    ]

    req = CheckProjectRequest(project_root=tmp_path, controls=controls, installed_plugins=())
    res = use_case.execute(req)

    assert res.ok is True
    assert res.failed_at is None
    assert seen(res, "mutation").ok is False
    assert seen(res, "types").ok is True


def test_check_project_level_filter(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("echo l1", CommandOutput(returncode=0, stdout="l1 ok", stderr=""))
    readers = all_readers()
    use_case = CheckProject(runner=runner, readers=readers)

    controls = [
        CommandControl(id="l1_ctrl", level="L1", run="echo l1", report="exit-code"),
        CommandControl(id="l4_ctrl", level="L4", run="echo l4", report="exit-code"),
    ]
    acceptance = CheckAcceptanceSpec(run="echo acc")

    req = CheckProjectRequest(
        project_root=tmp_path,
        controls=controls,
        acceptance=acceptance,
        levels=["L1"],
        installed_plugins=(),
    )
    res = use_case.execute(req)

    assert res.ok is True
    assert "l1_ctrl" in ids(res)
    assert "l4_ctrl" not in ids(res)
    assert "acceptance" not in ids(res)


def _project(tmp_path: Path, stdout: str, code: int) -> tuple[CheckProject, CheckProjectRequest]:
    runner = FakeRunner()
    runner.set_response("suite", CommandOutput(returncode=code, stdout=stdout, stderr="fallo"))
    readers = all_readers()
    use_case = CheckProject(runner=runner, readers=readers)
    control = CommandControl(id="mut", level="L5", run="suite", report="exit-code", mode="measure")
    junit = CommandControl(id="unit", level="L2", run="suite --out {out}", report="junit")
    return use_case, CheckProjectRequest(
        project_root=tmp_path, controls=[control, junit], installed_plugins=()
    )


def test_check_project_does_not_reuse_an_old_report(tmp_path: Path) -> None:
    use_case, request = _project(tmp_path, "", 0)
    runs = tmp_path / ".lictor" / "check"
    runs.mkdir(parents=True)
    old = '<testsuite><testcase name="test_viejo"/></testsuite>'
    (runs / "unit.xml").write_text(old, encoding="utf-8")

    result = use_case.execute(request)

    assert seen(result, "unit").ok is False
    assert not (runs / "unit.xml").exists()


def test_check_project_records_the_output_of_a_measure_control(tmp_path: Path) -> None:
    use_case, request = _project(tmp_path, "salida de medida", 7)

    result = use_case.execute(request)

    measured = seen(result, "mut")
    assert (measured.ok, measured.exit_code, measured.mode) == (False, 7, "measure")
    assert measured.output_path is not None
    assert "salida de medida" in (tmp_path / measured.output_path).read_text(encoding="utf-8")


def test_check_project_skips_a_control_whose_path_the_working_tree_lacks(tmp_path: Path) -> None:
    runner = FakeRunner()
    use_case = CheckProject(runner=runner, readers=all_readers())
    unit = CommandControl(
        id="unit",
        level="L2",
        run="pytest tests/unit --junitxml {out}",
        report="junit",
        when_exists="tests/unit",
    )

    res = use_case.execute(
        CheckProjectRequest(project_root=tmp_path, controls=[unit], installed_plugins=())
    )

    assert res.ok is True
    assert runner.calls == []
    assert "tests/unit" in (seen(res, "unit").skipped or "")


def test_check_project_runs_a_control_whose_path_the_working_tree_has(tmp_path: Path) -> None:
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    runner = FakeRunner()
    runner.set_response("lint", CommandOutput(returncode=0, stdout="", stderr=""))
    use_case = CheckProject(runner=runner, readers=all_readers())
    lint = CommandControl(
        id="lint", level="L1", run="lint tests/unit", report="exit-code", when_exists="tests/unit"
    )

    res = use_case.execute(
        CheckProjectRequest(project_root=tmp_path, controls=[lint], installed_plugins=())
    )

    assert res.ok is True
    assert len(runner.calls) == 1
    assert seen(res, "lint").skipped is None


@pytest.mark.parametrize(
    ("installed", "runs"),
    [(("xdist.plugin",), False), (("pytest_cov.plugin",), True), ((), False)],
)
def test_check_project_skips_a_control_whose_plugin_the_environment_lacks(
    tmp_path: Path, installed: tuple[str, ...], runs: bool
) -> None:
    runner = FakeRunner()
    runner.set_response("cov", CommandOutput(returncode=0, stdout="", stderr=""))
    use_case = CheckProject(runner=runner, readers=all_readers())
    cov = CommandControl(
        id="coverage",
        level="L2",
        run="cov",
        mode="measure",
        when_plugin="pytest_cov.plugin",
        report="exit-code",
    )

    res = use_case.execute(
        CheckProjectRequest(project_root=tmp_path, controls=[cov], installed_plugins=installed)
    )

    assert res.ok is True
    assert len(runner.calls) == int(runs)
    skipped = seen(res, "coverage").skipped
    assert (skipped is None) == runs
    assert runs or "pytest_cov.plugin" in (skipped or "")


OK = CommandOutput(returncode=0, stdout="", stderr="")
BAD = CommandOutput(returncode=1, stdout="mal", stderr="")
JUNIT_OK = '<testsuite><testcase name="test_1"/></testsuite>'


def _three(runner: FakeRunner) -> tuple[CheckProject, list[CommandControl], CheckAcceptanceSpec]:
    use_case = CheckProject(runner, all_readers())
    controls = [
        CommandControl("lint", "L1", "lint", report="exit-code"),
        CommandControl("types", "L1", "types", report="exit-code"),
        CommandControl("unit", "L2", "unit", report="exit-code"),
    ]
    return use_case, controls, CheckAcceptanceSpec("acc --junitxml {out}", "junit")


def test_keep_going_runs_everything_and_reports_the_first_failure(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("lint", BAD)
    runner.set_response("types", BAD)
    runner.set_response("unit", OK)
    runner.set_response("acc", CommandOutput(0, stdout=JUNIT_OK, stderr=""))
    use_case, controls, acceptance = _three(runner)

    res = use_case.execute(
        CheckProjectRequest(tmp_path, controls, acceptance, keep_going=True, installed_plugins=())
    )

    assert res.ok is False
    assert res.failed_at == "lint"
    assert ids(res) == ["lint", "types", "unit", "acceptance"]
    assert "Falló lint" in (res.condensed_error or "")


def test_without_keep_going_acceptance_doesnt_run_after_a_failure(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("lint", BAD)
    use_case, controls, acceptance = _three(runner)

    res = use_case.execute(
        CheckProjectRequest(tmp_path, controls, acceptance, installed_plugins=())
    )

    assert ids(res) == ["lint"]
    assert len(runner.calls) == 1


def test_a_failing_acceptance_is_reported_after_passing_controls(tmp_path: Path) -> None:
    runner = FakeRunner()
    for name in ("lint", "types", "unit"):
        runner.set_response(name, OK)
    failing = '<testsuite><testcase name="t"><failure message="no"/></testcase></testsuite>'
    runner.set_response("acc", CommandOutput(1, stdout=failing, stderr=""))
    use_case, controls, acceptance = _three(runner)

    res = use_case.execute(
        CheckProjectRequest(tmp_path, controls, acceptance, installed_plugins=())
    )

    assert (res.ok, res.failed_at) == (False, "acceptance")


def test_controls_are_chosen_by_id_and_level_together(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("lint", OK)
    runner.set_response("unit", OK)
    use_case, controls, acceptance = _three(runner)

    by_id = use_case.execute(
        CheckProjectRequest(
            tmp_path, controls, acceptance, control_ids=["unit", "lint"], installed_plugins=()
        )
    )
    both = use_case.execute(
        CheckProjectRequest(
            tmp_path,
            controls,
            acceptance,
            levels=["L1"],
            control_ids=["unit", "lint"],
            installed_plugins=(),
        )
    )

    assert ids(by_id) == ["lint", "unit"]
    assert ids(both) == ["lint"]


def test_acceptance_is_chosen_by_its_id(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("acc", CommandOutput(0, stdout=JUNIT_OK, stderr=""))
    use_case, controls, acceptance = _three(runner)

    res = use_case.execute(
        CheckProjectRequest(
            tmp_path, controls, acceptance, control_ids=["acceptance"], installed_plugins=()
        )
    )

    assert ids(res) == ["acceptance"]


def test_an_id_that_names_nothing_is_refused(tmp_path: Path) -> None:
    use_case, controls, acceptance = _three(FakeRunner())

    with pytest.raises(ConfigError, match="No hay ningún control mutacion"):
        use_case.execute(
            CheckProjectRequest(
                tmp_path, controls, acceptance, control_ids=["mutacion"], installed_plugins=()
            )
        )


def test_commands_run_in_the_tree_and_leave_evidence_apart(tmp_path: Path) -> None:
    tree, evidence = tmp_path / "clon", tmp_path / "proyecto" / ".lictor" / "check"
    tree.mkdir()
    runner = FakeRunner()
    runner.set_response("lint", BAD)
    use_case, controls, _ = _three(runner)

    res = use_case.execute(
        CheckProjectRequest(
            tmp_path / "proyecto",
            controls[:1],
            tree=tree,
            evidence_dir=evidence,
            installed_plugins=(),
        )
    )

    assert runner.calls[0]["cwd"] == tree
    assert (evidence / "lint.log").read_text(encoding="utf-8").startswith("Comando: ")
    assert seen(res, "lint").output_path == ".lictor/check/lint.log"


def test_a_control_whose_path_the_checked_tree_removed_fails(tmp_path: Path) -> None:
    use_case = CheckProject(FakeRunner(), all_readers())
    unit = CommandControl("unit", "L2", "u {out}", "junit", when_exists="tests/unit")

    res = use_case.execute(
        CheckProjectRequest(
            tmp_path, [unit], deleted=["tests/unit/test_a.py"], installed_plugins=()
        )
    )

    assert res.ok is False
    assert "elimina tests/unit" in (seen(res, "unit").error or "")


class _MutationRunner(FakeRunner):
    """Writes the counts a mutation pass leaves at `{out}`."""

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutput:
        Path(cmd[-1]).write_text('{"killed": 9, "survived": 1, "total": 10}', encoding="utf-8")
        return super().run(cmd, cwd, timeout_s, env)


@pytest.mark.parametrize(("minimum", "ok"), [(95, False), (90, True), (None, True)])
def test_a_mutation_control_passes_only_from_its_minimum(
    tmp_path: Path, minimum: float | None, ok: bool
) -> None:
    use_case = CheckProject(_MutationRunner(), all_readers())
    control = CommandControl("mut", "L5", "mut {out}", "mutation", min_score=minimum)

    res = use_case.execute(CheckProjectRequest(tmp_path, [control], installed_plugins=()))

    assert res.ok is ok
    error = seen(res, "mut").error or ""
    assert ("no llega al mínimo de 95 %" in error) is not ok


# --- ADR-0008: a reader in `lictor check` ---

_UNIT = CommandControl("unit", "L2", "pytest tests/unit --junitxml {out}", "junit")
_READER = ReaderControl(
    "coverage", "L2", "unit", "--cov-report=json:{out}", mode="measure", when_plugin="cov.p"
)
_LINT = CommandControl("lint", "L1", "lint", "exit-code")
_GREEN = '<testsuite><testcase name="test_a"/></testsuite>'
_RED = '<testsuite><testcase name="test_a"><failure message="m">t</failure></testcase></testsuite>'


def _check(
    tmp_path: Path,
    leaves: Leaves,
    controls: Sequence[Control] = (_LINT, _UNIT, _READER),
    installed: Sequence[str] = ("cov.p",),
    **fields: object,
) -> tuple[CheckProjectResult, FlagRunner]:
    runner = FlagRunner({"pytest": leaves})
    request = CheckProjectRequest(
        tmp_path, controls, installed_plugins=installed, evidence_dir=tmp_path / "check"
    )
    result = CheckProject(runner, all_readers()).execute(replace(request, **fields))
    return result, runner


def _unit_command(runner: FlagRunner) -> list[str]:
    return [str(t) for t in next(c["cmd"] for c in runner.calls if c["cmd"][0] == "pytest")]


def test_check_control_coverage_also_runs_unit_and_says_why(tmp_path: Path) -> None:
    result, _ = _check(
        tmp_path, Leaves(junit=_GREEN, coverage=coverage_json(1, 2)), control_ids=["coverage"]
    )

    assert ids(result) == ["unit", "coverage"]
    (added,) = result.added
    assert added.sentence == "Se ejecuta también unit, cuya ejecución lee coverage"
    assert seen(result, "coverage").score == CoverageReport(1, 2).score


def test_check_an_added_unit_that_fails_fails_the_check(tmp_path: Path) -> None:
    result, _ = _check(
        tmp_path, Leaves(junit=_RED, returncode=1), control_ids=["coverage"], keep_going=True
    )

    assert (result.ok, result.failed_at) == (False, "unit")
    assert "unit se ejecutó como" in (result.condensed_error or "")


def test_check_a_level_that_splits_the_pair_brings_unit(tmp_path: Path) -> None:
    reader = replace(_READER, level="L4")
    result, _ = _check(
        tmp_path, Leaves(junit=_GREEN), controls=(_LINT, _UNIT, reader), levels=["L4"]
    )

    assert ids(result) == ["unit", "coverage"]


def test_check_doesnt_add_unit_for_a_reader_without_its_plugin(tmp_path: Path) -> None:
    result, runner = _check(tmp_path, Leaves(junit=_GREEN), installed=(), control_ids=["coverage"])

    assert ids(result) == ["coverage"]
    assert runner.calls == []
    assert "cov.p" in (seen(result, "coverage").skipped or "")
    assert result.added == ()


def test_check_control_unit_runs_the_same_unit_as_the_whole_check(tmp_path: Path) -> None:
    """ADR-0008, point 4: a failure reproduces the same, whatever was selected."""
    leaves = Leaves(junit=_GREEN, coverage=coverage_json(1, 2))
    alone, alone_runner = _check(tmp_path / "a", leaves, control_ids=["unit"])
    whole, whole_runner = _check(tmp_path / "b", leaves)

    def shape(runner: FlagRunner, root: Path) -> list[str]:
        return [t.replace(str(root), "<root>") for t in _unit_command(runner)]

    assert shape(alone_runner, tmp_path / "a") == shape(whole_runner, tmp_path / "b")
    # Alone, its coverage report is written and nobody reads it.
    assert ids(alone) == ["unit"]
    assert (tmp_path / "a" / "check" / "coverage.xml").is_file()
    assert seen(whole, "coverage").score == CoverageReport(1, 2).score


def test_check_keeps_going_past_a_red_unit_and_scores_it(tmp_path: Path) -> None:
    unit = replace(_UNIT, mode="block")
    result, _ = _check(
        tmp_path,
        Leaves(junit=_RED, coverage=coverage_json(1, 2), returncode=1),
        controls=(unit, _READER),
        keep_going=True,
    )

    assert (result.ok, result.failed_at) == (False, "unit")
    read = next(e.outcome for e in result.controls if e.control.id == "coverage")
    assert isinstance(read, Read)
    assert from_failed_run(read, result.controls) is True


def test_check_marks_a_unit_that_got_the_arguments_of_a_check_reader(tmp_path: Path) -> None:
    late = replace(_READER, stage=CHECK)
    result, _ = _check(tmp_path, Leaves(junit=_GREEN), controls=(_UNIT, late))

    assert result.lent == {"unit": " (con los argumentos de coverage, que solo corre en check)"}


def test_check_doesnt_mark_a_unit_whose_reader_runs_in_every_attempt(tmp_path: Path) -> None:
    result, _ = _check(tmp_path, Leaves(junit=_GREEN))

    assert result.lent == {}


def test_check_names_no_command_for_a_unit_that_couldnt_run(tmp_path: Path) -> None:
    """A unit whose path the checked tree removed ran nothing: there is no command to name."""
    unit = replace(_UNIT, when_exists="tests/unit")
    result, runner = _check(
        tmp_path, Leaves(junit=_GREEN), controls=(unit, _READER), deleted=["tests/unit/test_a.py"]
    )

    assert runner.calls == []
    assert result.failed_at == "unit"
    assert "se ejecutó como" not in (result.condensed_error or "")


def test_check_doesnt_mark_a_skipped_unit_with_the_arguments_it_never_got(tmp_path: Path) -> None:
    unit = replace(_UNIT, when_exists="tests/unit")
    late = replace(_READER, stage=CHECK)
    result, _ = _check(tmp_path, Leaves(junit=_GREEN), controls=(unit, late))

    assert seen(result, "unit").skipped is not None
    assert result.lent == {}


def test_every_control_that_fails_says_why_whatever_its_mode(tmp_path: Path) -> None:
    """H1 of 0.20.0: a measure that fails is said too, and its whole failure is kept."""
    runner = FakeRunner()
    runner.set_response("echo mut", CommandOutput(returncode=1, stdout="sobrevive m1", stderr=""))
    runner.set_response("echo fail", CommandOutput(returncode=1, stdout="lint error", stderr=""))
    controls = [
        CommandControl(
            id="mutation", level="L5", run="echo mut", report="exit-code", mode="measure"
        ),
        CommandControl(id="lint", level="L1", run="echo fail", report="exit-code", mode="block"),
    ]
    req = CheckProjectRequest(
        project_root=tmp_path, controls=controls, installed_plugins=(), keep_going=True
    )

    res = CheckProject(runner=runner, readers=all_readers()).execute(req)

    assert [(f.id, f.mode) for f in res.failures] == [("mutation", "measure"), ("lint", "block")]
    assert [f.id for f in res.blocked] == ["lint"]
    assert "sobrevive m1" in res.failures[0].condensed()
    assert not res.failures[0].condensed().startswith("Falló")
    whole = tmp_path / ".lictor" / "check" / "mutation.error.txt"
    assert whole.read_text(encoding="utf-8").startswith("Falló mutation\n")


def test_a_minimum_of_coverage_with_no_code_to_measure_doesnt_block_the_check(
    tmp_path: Path,
) -> None:
    """S18: a base just set up has nothing to measure; its minimum waits for the first green."""
    reader = replace(_READER, min_score=90.0, mode="block")

    result, _ = _check(
        tmp_path,
        Leaves(junit=_GREEN, coverage=coverage_json(0, 0)),
        controls=(_LINT, _UNIT, reader),
        control_ids=["coverage"],
        unstarted=True,
    )

    assert result.ok
    assert result.not_measured == ("coverage",)
    assert result.blocked == ()
    assert [(f.id, f.mode) for f in result.failures] == [("coverage", "measure")]


def test_a_minimum_of_coverage_that_measured_code_still_blocks(tmp_path: Path) -> None:
    reader = replace(_READER, min_score=90.0, mode="block")

    result, _ = _check(
        tmp_path,
        Leaves(junit=_GREEN, coverage=coverage_json(1, 2)),
        controls=(_LINT, _UNIT, reader),
        control_ids=["coverage"],
        unstarted=True,
    )

    assert not result.ok
    assert result.not_measured == ()
    assert [f.id for f in result.blocked] == ["coverage"]


def test_nothing_to_measure_blocks_once_a_scenario_was_green(tmp_path: Path) -> None:
    """Fable's B1 of the PR: with a green in git, a `--cov` that measures nothing is a fault."""
    reader = replace(_READER, min_score=90.0, mode="block")

    result, _ = _check(
        tmp_path,
        Leaves(junit=_GREEN, coverage=coverage_json(0, 0)),
        controls=(_LINT, _UNIT, reader),
        control_ids=["coverage"],
    )

    assert not result.ok
    assert result.not_measured == ()
    assert [f.id for f in result.blocked] == ["coverage"]
