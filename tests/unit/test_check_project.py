from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.readers.mutation import MutationReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.application.usecases.check_project import (
    CheckAcceptanceSpec,
    CheckControlSpec,
    CheckProject,
    CheckProjectRequest,
)
from lictor.domain.errors import ConfigError
from lictor.domain.model import CommandOutput


def test_check_project_all_controls_and_acceptance_pass(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("echo lint", CommandOutput(returncode=0, stdout="ok", stderr=""))
    junit_xml = '<testsuites><testsuite name="s"><testcase name="test_1"/></testsuite></testsuites>'
    runner.set_response(
        "echo test",
        CommandOutput(
            returncode=0,
            stdout=junit_xml,
            stderr="",
        ),
    )
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}
    use_case = CheckProject(runner=runner, readers=readers)

    controls = [
        CheckControlSpec(id="lint", level="L1", run="echo lint", report="exit-code"),
    ]
    acceptance = CheckAcceptanceSpec(run="echo test", report="junit")

    req = CheckProjectRequest(
        project_root=tmp_path,
        controls=controls,
        acceptance=acceptance,
    )
    res = use_case.execute(req)

    assert res.ok is True
    assert res.failed_at is None
    assert "lint" in res.control_results
    assert res.control_results["lint"].ok is True
    assert "acceptance" in res.control_results
    assert res.control_results["acceptance"].ok is True


def test_check_project_blocking_control_fails_and_stops_early(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("echo fail", CommandOutput(returncode=1, stdout="lint error", stderr=""))
    runner.set_response("echo pass", CommandOutput(returncode=0, stdout="ok", stderr=""))
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}
    use_case = CheckProject(runner=runner, readers=readers)

    controls = [
        CheckControlSpec(id="lint", level="L1", run="echo fail", report="exit-code", mode="block"),
        CheckControlSpec(id="types", level="L1", run="echo pass", report="exit-code", mode="block"),
    ]

    req = CheckProjectRequest(project_root=tmp_path, controls=controls)
    res = use_case.execute(req)

    assert res.ok is False
    assert res.failed_at == "lint"
    assert "types" not in res.control_results
    assert "lint error" in (res.condensed_error or "")


def test_check_project_measure_mode_does_not_block(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response(
        "echo mut", CommandOutput(returncode=1, stdout="mutant survived", stderr="")
    )
    runner.set_response("echo types", CommandOutput(returncode=0, stdout="types ok", stderr=""))
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}
    use_case = CheckProject(runner=runner, readers=readers)

    controls = [
        CheckControlSpec(
            id="mutation", level="L5", run="echo mut", report="exit-code", mode="measure"
        ),
        CheckControlSpec(
            id="types", level="L1", run="echo types", report="exit-code", mode="block"
        ),
    ]

    req = CheckProjectRequest(project_root=tmp_path, controls=controls)
    res = use_case.execute(req)

    assert res.ok is True
    assert res.failed_at is None
    assert res.control_results["mutation"].ok is False
    assert res.control_results["types"].ok is True


def test_check_project_level_filter(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("echo l1", CommandOutput(returncode=0, stdout="l1 ok", stderr=""))
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}
    use_case = CheckProject(runner=runner, readers=readers)

    controls = [
        CheckControlSpec(id="l1_ctrl", level="L1", run="echo l1", report="exit-code"),
        CheckControlSpec(id="l4_ctrl", level="L4", run="echo l4", report="exit-code"),
    ]
    acceptance = CheckAcceptanceSpec(run="echo acc", report="junit")

    req = CheckProjectRequest(
        project_root=tmp_path,
        controls=controls,
        acceptance=acceptance,
        levels=["L1"],
    )
    res = use_case.execute(req)

    assert res.ok is True
    assert "l1_ctrl" in res.control_results
    assert "l4_ctrl" not in res.control_results
    assert "acceptance" not in res.control_results


def _project(tmp_path: Path, stdout: str, code: int) -> tuple[CheckProject, CheckProjectRequest]:
    runner = FakeRunner()
    runner.set_response("suite", CommandOutput(returncode=code, stdout=stdout, stderr="fallo"))
    readers = {"exit-code": ExitCodeReader(), "junit": JUnitReader()}
    use_case = CheckProject(runner=runner, readers=readers)
    control = CheckControlSpec(
        id="mut", level="L5", run="suite", report="exit-code", mode="measure"
    )
    junit = CheckControlSpec(id="unit", level="L2", run="suite --out {out}", report="junit")
    return use_case, CheckProjectRequest(project_root=tmp_path, controls=[control, junit])


def test_check_project_does_not_reuse_an_old_report(tmp_path: Path) -> None:
    use_case, request = _project(tmp_path, "", 0)
    runs = tmp_path / ".lictor" / "check"
    runs.mkdir(parents=True)
    old = '<testsuite><testcase name="test_viejo"/></testsuite>'
    (runs / "unit.xml").write_text(old, encoding="utf-8")

    result = use_case.execute(request)

    assert result.control_results["unit"].ok is False
    assert not (runs / "unit.xml").exists()


def test_check_project_records_the_output_of_a_measure_control(tmp_path: Path) -> None:
    use_case, request = _project(tmp_path, "salida de medida", 7)

    result = use_case.execute(request)

    measured = result.control_results["mut"]
    assert (measured.ok, measured.exit_code, measured.mode) == (False, 7, "measure")
    assert measured.output_path is not None
    assert "salida de medida" in (tmp_path / measured.output_path).read_text(encoding="utf-8")


def test_check_project_skips_a_control_whose_path_the_working_tree_lacks(tmp_path: Path) -> None:
    runner = FakeRunner()
    use_case = CheckProject(runner=runner, readers={"junit": JUnitReader()})
    unit = CheckControlSpec(
        id="unit",
        level="L2",
        run="pytest tests/unit --junitxml {out}",
        report="junit",
        when_exists="tests/unit",
    )

    res = use_case.execute(CheckProjectRequest(project_root=tmp_path, controls=[unit]))

    assert res.ok is True
    assert runner.calls == []
    assert "tests/unit" in (res.control_results["unit"].skipped or "")


def test_check_project_runs_a_control_whose_path_the_working_tree_has(tmp_path: Path) -> None:
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    runner = FakeRunner()
    runner.set_response("lint", CommandOutput(returncode=0, stdout="", stderr=""))
    use_case = CheckProject(runner=runner, readers={"exit-code": ExitCodeReader()})
    lint = CheckControlSpec(
        id="lint", level="L1", run="lint tests/unit", report="exit-code", when_exists="tests/unit"
    )

    res = use_case.execute(CheckProjectRequest(project_root=tmp_path, controls=[lint]))

    assert res.ok is True
    assert len(runner.calls) == 1
    assert res.control_results["lint"].skipped is None


OK = CommandOutput(returncode=0, stdout="", stderr="")
BAD = CommandOutput(returncode=1, stdout="mal", stderr="")
JUNIT_OK = '<testsuite><testcase name="test_1"/></testsuite>'


def _three(runner: FakeRunner) -> tuple[CheckProject, list[CheckControlSpec], CheckAcceptanceSpec]:
    use_case = CheckProject(runner, {"exit-code": ExitCodeReader(), "junit": JUnitReader()})
    controls = [
        CheckControlSpec("lint", "L1", "lint"),
        CheckControlSpec("types", "L1", "types"),
        CheckControlSpec("unit", "L2", "unit"),
    ]
    return use_case, controls, CheckAcceptanceSpec("acc --junitxml {out}", "junit")


def test_keep_going_runs_everything_and_reports_the_first_failure(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("lint", BAD)
    runner.set_response("types", BAD)
    runner.set_response("unit", OK)
    runner.set_response("acc", CommandOutput(0, stdout=JUNIT_OK, stderr=""))
    use_case, controls, acceptance = _three(runner)

    res = use_case.execute(CheckProjectRequest(tmp_path, controls, acceptance, keep_going=True))

    assert res.ok is False
    assert res.failed_at == "lint"
    assert list(res.control_results) == ["lint", "types", "unit", "acceptance"]
    assert "Falló lint" in (res.condensed_error or "")


def test_without_keep_going_acceptance_doesnt_run_after_a_failure(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("lint", BAD)
    use_case, controls, acceptance = _three(runner)

    res = use_case.execute(CheckProjectRequest(tmp_path, controls, acceptance))

    assert list(res.control_results) == ["lint"]
    assert len(runner.calls) == 1


def test_a_failing_acceptance_is_reported_after_passing_controls(tmp_path: Path) -> None:
    runner = FakeRunner()
    for name in ("lint", "types", "unit"):
        runner.set_response(name, OK)
    failing = '<testsuite><testcase name="t"><failure message="no"/></testcase></testsuite>'
    runner.set_response("acc", CommandOutput(1, stdout=failing, stderr=""))
    use_case, controls, acceptance = _three(runner)

    res = use_case.execute(CheckProjectRequest(tmp_path, controls, acceptance))

    assert (res.ok, res.failed_at) == (False, "acceptance")


def test_controls_are_chosen_by_id_and_level_together(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("lint", OK)
    runner.set_response("unit", OK)
    use_case, controls, acceptance = _three(runner)

    by_id = use_case.execute(
        CheckProjectRequest(tmp_path, controls, acceptance, control_ids=["unit", "lint"])
    )
    both = use_case.execute(
        CheckProjectRequest(
            tmp_path, controls, acceptance, levels=["L1"], control_ids=["unit", "lint"]
        )
    )

    assert list(by_id.control_results) == ["lint", "unit"]
    assert list(both.control_results) == ["lint"]


def test_acceptance_is_chosen_by_its_id(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.set_response("acc", CommandOutput(0, stdout=JUNIT_OK, stderr=""))
    use_case, controls, acceptance = _three(runner)

    res = use_case.execute(
        CheckProjectRequest(tmp_path, controls, acceptance, control_ids=["acceptance"])
    )

    assert list(res.control_results) == ["acceptance"]


def test_an_id_that_names_nothing_is_refused(tmp_path: Path) -> None:
    use_case, controls, acceptance = _three(FakeRunner())

    with pytest.raises(ConfigError, match="No hay ningún control mutacion"):
        use_case.execute(
            CheckProjectRequest(tmp_path, controls, acceptance, control_ids=["mutacion"])
        )


def test_commands_run_in_the_tree_and_leave_evidence_apart(tmp_path: Path) -> None:
    tree, evidence = tmp_path / "clon", tmp_path / "proyecto" / ".lictor" / "check"
    tree.mkdir()
    runner = FakeRunner()
    runner.set_response("lint", BAD)
    use_case, controls, _ = _three(runner)

    res = use_case.execute(
        CheckProjectRequest(tmp_path / "proyecto", controls[:1], tree=tree, evidence_dir=evidence)
    )

    assert runner.calls[0]["cwd"] == tree
    assert (evidence / "lint.log").is_file()
    assert res.control_results["lint"].output_path == ".lictor/check/lint.log"


def test_a_control_whose_path_the_checked_tree_removed_fails(tmp_path: Path) -> None:
    use_case = CheckProject(FakeRunner(), {"junit": JUnitReader()})
    unit = CheckControlSpec("unit", "L2", "u {out}", "junit", when_exists="tests/unit")

    res = use_case.execute(CheckProjectRequest(tmp_path, [unit], deleted=["tests/unit/test_a.py"]))

    assert res.ok is False
    assert "elimina tests/unit" in (res.control_results["unit"].error or "")


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
    use_case = CheckProject(_MutationRunner(), {"mutation": MutationReader()})
    control = CheckControlSpec("mut", "L5", "mut {out}", "mutation", min_score=minimum)

    res = use_case.execute(CheckProjectRequest(tmp_path, [control]))

    assert res.ok is ok
    error = res.control_results["mut"].error or ""
    assert ("no llega al mínimo de 95 %" in error) is not ok
