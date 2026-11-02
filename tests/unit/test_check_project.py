from pathlib import Path

from lictor.adapters.readers.exit_code import ExitCodeReader
from lictor.adapters.readers.junit import JUnitReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.application.usecases.check_project import (
    CheckAcceptanceSpec,
    CheckControlSpec,
    CheckProject,
    CheckProjectRequest,
)
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
    runs = tmp_path / ".lictor" / "runs" / "check"
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
