"""Running the profile's survey on a clean clone of HEAD, and reading what it left (ADR-0015)."""

import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from lictor.adapters.readers.survey import SurveyReader
from lictor.application.ports import Vcs
from lictor.application.start_survey import StartSurvey, SurveyedCommandSpec
from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.ci import CiStep, CiSteps
from lictor.domain.model import CommandOutput
from lictor.domain.survey import SurveyReport

REPORT: dict[str, Any] = {
    "code": {"dir": "src", "modules": ["pkg"]},
    "commands": [{"id": "unit", "status": "unknown", "reason": "make", "program": None}],
    "tests": [],
    "loaded": [],
    "code_users": [],
    "skipped": {"projects": [], "unreadable": []},
}


def _survey() -> StartSurvey:
    return StartSurvey(
        run="tool -m lictor_survey src --in {in} --out {out}",
        commands=[SurveyedCommandSpec("unit", "make test")],
        detect=["pyproject.toml"],
        reader=SurveyReader(),
    )


def _vcs() -> MagicMock:
    vcs = MagicMock(spec=Vcs)
    vcs.export_tree.side_effect = lambda rev, dest: Path(dest).mkdir(parents=True)
    return vcs


def _environment(write: object, returncode: int = 0, timed_out: bool = False) -> MagicMock:
    environment = MagicMock(spec=VerificationEnvironment)
    seen: dict[str, Any] = {}

    def run(cmd: Sequence[str], cwd: Path, timeout_s: int | None) -> CommandOutput:
        given = Path(cmd[cmd.index("--in") + 1])
        out = Path(cmd[cmd.index("--out") + 1])
        seen.update(cmd=list(cmd), cwd=cwd, given=json.loads(given.read_text(encoding="utf-8")))
        if write is not None:
            out.write_text(write if isinstance(write, str) else json.dumps(write), "utf-8")
        return CommandOutput(returncode, "", "", timed_out=timed_out)

    environment.run.side_effect = run
    environment.seen = seen
    return environment


def test_the_survey_reads_a_clone_of_head_with_the_commands_it_is_asked_about(
    tmp_path: Path,
) -> None:
    vcs = _vcs()
    environment = _environment(REPORT)
    evidence = tmp_path / "evidence"

    result = _survey().execute(environment, vcs, tmp_path, evidence)

    assert isinstance(result, SurveyReport)
    vcs.export_tree.assert_called_once()
    assert vcs.export_tree.call_args.args[0] == "HEAD"
    assert environment.seen["cwd"] == vcs.export_tree.call_args.args[1]
    assert environment.seen["given"] == {
        "commands": [{"id": "unit", "run": "make test"}],
        "detect": ["pyproject.toml"],
    }
    assert environment.seen["cmd"][:3] == ["tool", "-m", "lictor_survey"]
    assert {p.name for p in evidence.iterdir()} == {"survey-in.json", "survey.json", "survey.log"}


def test_a_survey_that_leaves_nothing_usable_says_why(tmp_path: Path) -> None:
    cases = [
        (_environment(None), "no dejó informe"),
        (_environment("{"), "no es válido: no es JSON"),
        (_environment({"code": 1}), "no es válido: code no es un objeto"),
        (_environment(REPORT, returncode=2), "terminó con el código 2"),
        (_environment(REPORT, timed_out=True), "no terminó en 120 s"),
    ]
    for index, (environment, reason) in enumerate(cases):
        result = _survey().execute(environment, _vcs(), tmp_path, tmp_path / str(index))

        assert isinstance(result, str)
        assert reason in result


def test_a_report_left_by_an_earlier_survey_counts_for_nothing(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "survey.json").write_text(json.dumps(REPORT), encoding="utf-8")

    result = _survey().execute(_environment(None), _vcs(), tmp_path, evidence)

    assert result == "el análisis no dejó informe"


class _Ci:
    """A reader of the CI that answers with what it is given, and says where it was asked."""

    def __init__(self, steps: CiSteps) -> None:
        self.steps = steps
        self.roots: list[Path] = []

    def read(self, root: Path) -> CiSteps:
        self.roots.append(root)
        return self.steps


def test_the_steps_of_the_ci_are_read_from_the_same_clone_and_handed_to_the_survey(
    tmp_path: Path,
) -> None:
    step = CiStep("w.yml", 3, "uv run mypy src", "pre-commit", True, ("^src/",), ("x",))
    ci = _Ci(CiSteps((step,), ("bad.yml: no es YAML válido",)))
    vcs = _vcs()
    environment = _environment(REPORT)
    survey = dataclasses.replace(_survey(), ci=ci)

    result = survey.execute(environment, vcs, tmp_path, tmp_path / "evidence")

    assert ci.roots == [vcs.export_tree.call_args.args[1]]
    assert environment.seen["given"]["ci"] == [
        {
            "file": "w.yml",
            "line": 3,
            "run": "uv run mypy src",
            "origin": "pre-commit",
            "pass_filenames": True,
            "files": ["^src/"],
            "exclude": ["x"],
        }
    ]
    # What Lictor couldn't read of the CI goes with the survey's facts.
    assert isinstance(result, SurveyReport)
    assert result.ci_unreadable == ("bad.yml: no es YAML válido",)
