"""How a change is told apart from what its controls executed (H2 of 0.20.0): git, survey, JUnit."""

from pathlib import Path
from unittest.mock import MagicMock

from lictor.application.ports import ReportReader, Vcs, VerificationCommands
from lictor.application.start_survey import StartSurvey
from lictor.application.unexecuted import UnexecutedSurvey
from lictor.domain.blind_spots import StartRules
from lictor.domain.conventions import TreeEntry
from lictor.domain.model import TestCaseResult
from lictor.domain.reports import JUnitReport, MissingReport
from lictor.domain.survey import SurveyedCommand, SurveyedTests, SurveyReport
from lictor.domain.unexecuted import Unexecuted, UnexecutedChanges

_REPORT = SurveyReport(
    code_dir="src",
    modules=("pkg",),
    commands=(SurveyedCommand("unit", True, tests=True, paths=("tests",), selects=True),),
    tests=(
        SurveyedTests("tests/unit/test_a.py", 1, run_by=("unit",)),
        SurveyedTests("tests/integration/test_m.py", 1, run_by=("unit",)),
    ),
)


def _finder(
    changed: list[str], surveyed: SurveyReport | str = _REPORT
) -> tuple[UnexecutedSurvey, MagicMock]:
    vcs = MagicMock(spec=Vcs)
    vcs.changed_paths.return_value = changed
    vcs.tree_entries.return_value = [TreeEntry(p, "100644") for p in changed]
    survey = MagicMock(spec=StartSurvey)
    survey.execute.return_value = surveyed
    junit = MagicMock(spec=ReportReader)
    junit.read.side_effect = lambda path: (
        JUnitReport((TestCaseResult("t", classname="tests.unit.test_a"),))
        if path.name == "unit.xml"
        else MissingReport()
    )
    rules = StartRules(writable=("src/", "tests/"), driver=())
    return UnexecutedSurvey(vcs, Path("/p"), survey, rules, junit, ("unit", "acceptance")), survey


def _run(finder: UnexecutedSurvey) -> UnexecutedChanges | str:
    return finder.execute(
        MagicMock(spec=VerificationCommands),
        ("a" * 40, "b" * 40),
        Path("/e"),
        lambda command: Path(f"/r/{command}.xml"),
    )


def test_a_test_no_case_of_which_ran_is_told_by_the_junit() -> None:
    finder, survey = _finder(["tests/unit/test_a.py", "tests/integration/test_m.py"])

    found = _run(finder)

    assert found == UnexecutedChanges(
        files=(Unexecuted("tests/integration/test_m.py", "sin_ejecutar"),)
    )
    assert survey.execute.call_args.kwargs["rev"] == "b" * 40


def test_an_empty_change_surveys_nothing() -> None:
    finder, survey = _finder([])

    assert _run(finder) == UnexecutedChanges()
    survey.execute.assert_not_called()


def test_a_survey_that_fails_says_why() -> None:
    finder, _ = _finder(["README.md"], surveyed="el análisis no dejó informe")

    found = _run(finder)

    assert isinstance(found, str)
    assert "el análisis no dejó informe" in found


def test_git_failing_is_the_reason_not_the_end_of_the_run() -> None:
    """M5 of 0.20.0: it runs in `_finish`, once the branch moved; it tells, it never breaks."""
    finder, _ = _finder(["src/a.py"])
    vcs = finder.vcs
    assert isinstance(vcs, MagicMock)
    vcs.changed_paths.side_effect = OSError("git no está")

    assert _run(finder) == "no se pudo leer lo que cambió (OSError: git no está)"
