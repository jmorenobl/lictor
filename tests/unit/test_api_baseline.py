"""The base of the public API: measured from git, once per run, and how a verification uses it.

The state keeps only the base's SHA (ADR-0017); its report is the same command run in a clean
clone of that commit, never read from `runs/`. These fix that, and what a verification of the
implementer and of the refactor does with the comparison.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from lictor.adapters.readers.api import ApiReader
from lictor.adapters.runner.fake import FakeRunner
from lictor.application.api_baseline import ApiBaseline, RunIn, api_control
from lictor.application.usecases.verify_attempt import VerifyAttempt, VerifyAttemptRequest
from lictor.domain.controls import CommandControl, ControlMode, ReaderControl
from lictor.domain.model import CommandOutput
from lictor.domain.refactor import Measure
from lictor.domain.reports import ApiBreak, ApiComparison, ApiReport
from lictor.domain.results import Executed, Judged, VerifyResult
from tests.environments import PlainEnvironment
from tests.readers import all_readers
from tests.unit.test_verify_attempt import FakeVcs, _setup_project

_BASE = "b" * 40
_PASSING = '<testsuite><testcase name="test_iva_001" classname="t"/></testsuite>'


def _api(mode: ControlMode = "measure") -> CommandControl:
    return CommandControl(id="api", level="L1", run="api {out}", report="api", mode=mode)


def _symbols(*names: str) -> str:
    return json.dumps(
        {
            "symbols": [
                {"name": n, "kind": "function", "origin": n, "file": "src/p.py", "line": 1}
                for n in names
            ]
        }
    )


class _TreesVcs(FakeVcs):
    """Each clone says which commit it is, so the runner can answer for it."""

    def __init__(self) -> None:
        super().__init__(changed_files=["src/p.py"])

    def export_tree(self, rev: str, dest: Path) -> None:
        super().export_tree(rev, dest)
        (dest / "REV").write_text(rev, encoding="utf-8")


class _ApiRunner(FakeRunner):
    """Leaves at `{out}` the API of the commit its clone is, and passes the acceptance."""

    def __init__(self, apis: Mapping[str, str], returncode: int = 0) -> None:
        super().__init__()
        self.apis = dict(apis)
        self.returncode = returncode
        self.measured: list[str] = []

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutput:
        super().run(cmd, cwd, timeout_s, env)
        marker = cwd / "REV"
        rev = marker.read_text(encoding="utf-8") if marker.is_file() else ""
        if cmd[0] == "api":
            self.measured.append(rev)
            if rev in self.apis:
                Path(cmd[-1]).write_text(self.apis[rev], encoding="utf-8")
            code = self.returncode if rev == _BASE else 0
            return CommandOutput(returncode=code, stdout="", stderr="")
        Path(cmd[-1]).write_text(_PASSING, encoding="utf-8")
        return CommandOutput(returncode=0, stdout="", stderr="")


def _baseline(accepted: tuple[str, ...] = ()) -> ApiBaseline:
    return ApiBaseline("api {out}", 60, ApiReader(), accepted)


def _run_in(runner: FakeRunner, changed: tuple[str, ...] = ()) -> RunIn:
    def run_in(
        command: Sequence[str], tree: Path, timeout_s: int
    ) -> tuple[CommandOutput, tuple[str, ...]]:
        return runner.run(command, tree, timeout_s), changed

    return run_in


def test_the_base_is_measured_in_a_clone_of_its_commit_once(tmp_path: Path) -> None:
    runner = _ApiRunner({_BASE: _symbols("p.f")})
    baseline, vcs = _baseline(("p.g",)), _TreesVcs()

    first = baseline.base(_BASE, vcs, tmp_path, _run_in(runner), tmp_path / "e")
    again = baseline.base(_BASE, vcs, tmp_path, _run_in(runner), tmp_path / "e")

    assert runner.measured == [_BASE]
    assert first == again
    assert first.sha == _BASE
    assert first.accepted == ("p.g",)
    assert isinstance(first.found, ApiReport)
    assert [s.name for s in first.found.symbols] == ["p.f"]
    assert (tmp_path / "e" / "api-base.json").is_file()
    assert (tmp_path / "e" / "api-base.log").read_text(encoding="utf-8").startswith("Comando: api ")


@pytest.mark.parametrize(
    ("apis", "returncode", "changed", "problem"),
    [
        ({}, 0, (), "la medida no dejó informe"),
        ({_BASE: "{"}, 0, (), "su informe no es válido: no es JSON"),
        ({_BASE: _symbols("p.f")}, 2, (), "la medida terminó con el código 2 (api-base.log)"),
        (
            {_BASE: _symbols("p.f")},
            0,
            (".lictor/seal.json",),
            "su medida cambió el estado de la ejecución (.lictor/seal.json)",
        ),
    ],
)
def test_a_base_that_cant_be_measured_says_why_and_is_measured_again(
    apis: Mapping[str, str],
    returncode: int,
    changed: tuple[str, ...],
    problem: str,
    tmp_path: Path,
) -> None:
    runner = _ApiRunner(apis, returncode)
    baseline = _baseline()

    first = baseline.base(_BASE, _TreesVcs(), tmp_path, _run_in(runner, changed), tmp_path / "e")
    baseline.base(_BASE, _TreesVcs(), tmp_path, _run_in(runner, changed), tmp_path / "e")

    assert isinstance(first.found, str)
    assert first.found.startswith(problem)
    assert runner.measured == [_BASE, _BASE]


def test_a_base_cut_by_its_timeout_says_so(tmp_path: Path) -> None:
    def run_in(
        command: Sequence[str], tree: Path, timeout_s: int
    ) -> tuple[CommandOutput, tuple[str, ...]]:
        return CommandOutput(returncode=-9, stdout="", stderr="", timed_out=True), ()

    found = _baseline().base(_BASE, _TreesVcs(), tmp_path, run_in, tmp_path / "e")

    assert found.found == "la medida no terminó en 60 s"


def test_the_api_control_is_the_first_with_its_report() -> None:
    lint = CommandControl(id="lint", level="L1", run="l", report="exit-code")
    reader = ReaderControl(id="coverage", level="L2", reads="unit", adds="--cov")

    assert api_control([lint, reader, _api(), _api("block")]) == _api()
    assert api_control([lint, reader]) is None


def _verify(
    tmp_path: Path,
    runner: _ApiRunner,
    *,
    mode: ControlMode = "measure",
    measurer: ApiBaseline | None = None,
    green: Mapping[str, Measure] | None = None,
) -> tuple[VerifyAttempt, VerifyAttemptRequest]:
    project_root, run_id = _setup_project(tmp_path)
    verifier = VerifyAttempt(
        vcs=_TreesVcs(),
        environment=PlainEnvironment(runner),
        readers=all_readers(),
        project_root=project_root,
        api=measurer if measurer is not None else _baseline(),
    )
    req = VerifyAttemptRequest(
        tree="agent-tree",
        scenario_id="IVA-001",
        run_id=run_id,
        paths_writable=("src/",),
        paths_driver=("tests/acceptance/",),
        suppressions=(),
        controls=(_api(mode),),
        acceptance_run="pytest {out}",
        all_scenario_ids=("IVA-001",),
        api_base=_BASE,
        role="refactor" if green is not None else "implement",
        baseline=green or {},
    )
    return verifier, req


def _judged(result: VerifyResult) -> Judged:
    (entry,) = result.controls
    assert isinstance(entry.outcome, Executed)
    assert isinstance(entry.outcome.judgement, Judged)
    return entry.outcome.judgement


def test_a_green_that_breaks_the_api_in_measure_passes_and_says_what_broke(
    tmp_path: Path,
) -> None:
    runner = _ApiRunner({_BASE: _symbols("p.f", "p.g"), "fake-candidate": _symbols("p.g")})
    verifier, req = _verify(tmp_path, runner)

    result = verifier.execute(req)

    assert result.ok
    judged = _judged(result)
    assert isinstance(judged.report, ApiComparison)
    assert judged.report.base == _BASE
    assert judged.report.broken == (ApiBreak("p.f", "removed"),)
    assert runner.measured == [_BASE, "fake-candidate"]
    assert (verifier.project_root / result.evidence["path"] / "api-base.json").is_file()


def test_a_blocking_api_control_fails_naming_what_broke(tmp_path: Path) -> None:
    runner = _ApiRunner({_BASE: _symbols("p.f"), "fake-candidate": _symbols()})
    verifier, req = _verify(tmp_path, runner, mode="block")

    result = verifier.execute(req)

    assert result.failed_at == "api"
    assert "`p.f`: ya no existe" in (result.condensed_error or "")
    assert "SPEC_CONFLICT" not in (result.condensed_error or ""), "the verdict gives facts"


def test_what_the_person_accepts_doesnt_block(tmp_path: Path) -> None:
    runner = _ApiRunner({_BASE: _symbols("p.f"), "fake-candidate": _symbols()})
    verifier, req = _verify(tmp_path, runner, mode="block", measurer=_baseline(("p.f",)))

    assert verifier.execute(req).ok


def test_a_run_without_a_base_only_counts_the_names(tmp_path: Path) -> None:
    runner = _ApiRunner({"fake-candidate": _symbols("p.f")})
    verifier, req = _verify(tmp_path, runner, mode="block")

    result = verifier.execute(replace(req, api_base=None))

    assert result.ok
    assert isinstance(_judged(result).report, ApiReport)
    assert runner.measured == ["fake-candidate"]


def test_a_verification_without_an_api_control_measures_no_base(tmp_path: Path) -> None:
    runner = _ApiRunner({})
    verifier, req = _verify(tmp_path, runner)

    assert verifier.execute(replace(req, controls=())).ok
    assert runner.measured == []


def test_a_refactor_that_breaks_what_its_green_didnt_is_discarded(tmp_path: Path) -> None:
    runner = _ApiRunner({_BASE: _symbols("p.f", "p.g"), "fake-candidate": _symbols("p.g")})
    green = {"api": Measure(breaks=frozenset())}
    verifier, req = _verify(tmp_path, runner, green=green)

    result = verifier.execute(req)

    assert result.failed_at == "L0-regresión"
    assert "api: rompe `p.f`: ya no existe, y el verde no" in (result.condensed_error or "")


def test_a_refactor_may_keep_what_its_green_broke(tmp_path: Path) -> None:
    runner = _ApiRunner({_BASE: _symbols("p.f", "p.g"), "fake-candidate": _symbols("p.g")})
    green = {"api": Measure(breaks=frozenset({ApiBreak("p.f", "removed")}))}
    verifier, req = _verify(tmp_path, runner, green=green)

    assert verifier.execute(req).ok
