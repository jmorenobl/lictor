"""Phase 4: what earlier phases left noted, measured with the real flow."""

import json
from pathlib import Path

import pytest

from lictor.application.verification_env import VerificationEnvironment
from lictor.domain.model import AgentStatus, AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    GOOD_2,
    MODULE_PATH,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_impl,
    green_ids,
    known_limitation,
    require,
    run_lictor,
    write,
)
from tests.integration.repro.test_phase3_verification_boundary import (
    CHEAT_PLUGIN,
    DIST_METADATA,
    ENTRY_POINTS,
)


def _edit_profile(tmp_path: Path) -> None:
    path = tmp_path / "perfiles" / "python-uv.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["banned_patterns"] = []
    path.write_text(json.dumps(profile), encoding="utf-8")


def test_new_run_after_a_profile_change_gets_through(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    root = make_project()
    limited = [True]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if scenario_id == "IVA-002" and limited[0]:
            return "rate_limited"
        good_impl(task, scenario_id, attempt)
        return None

    agent = Agent(impl=impl)
    require(run_lictor(root, agent) == Outcome(50), "la ejecución debía quedar pausada")
    _edit_profile(tmp_path)
    limited[0] = False

    assert run_lictor(root, agent) == Outcome(10)
    refused = events_of(root, "run.stopped")[-1]["data"]["reason"]
    assert "--new" in refused

    assert run_lictor(root, agent, new=True) == Outcome(0)
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []


def test_new_run_blocked_by_a_collateral_green_says_how_to_go_on(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """Phase 5 records these by itself (D5); until then the way out has to be in the message."""
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if scenario_id == "IVA-001":
            write(task.cwd, MODULE_PATH, GOOD_2)
            return None
        if scenario_id == "IVA-002":
            return "rate_limited"
        good_impl(task, scenario_id, attempt)
        return None

    agent = Agent(impl=impl)
    require(run_lictor(root, agent) == Outcome(50), "la ejecución debía quedar pausada")
    _edit_profile(tmp_path)
    require(run_lictor(root, agent) == Outcome(10), "reanudar debía rechazarse")

    assert run_lictor(root, agent, new=True) == Outcome(10)

    reason = events_of(root, "run.finished")[-1]["data"]["reason"]
    assert "--allow-empty" in reason
    assert "IVA-002" in reason

    git(root, "commit", "-q", "--allow-empty", "-m", "registro", "--trailer", "Scenario: IVA-002")
    assert run_lictor(root, agent, new=True) == Outcome(0)
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []


def test_journal_damage_reaches_the_report(make_project: ProjectFactory) -> None:
    root = make_project()
    limited = [True]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if limited.pop() if limited else False:
            return "rate_limited"
        good_impl(task, scenario_id, attempt)
        return None

    agent = Agent(impl=impl)
    require(run_lictor(root, agent) == Outcome(50), "la ejecución debía quedar pausada")
    with (root / ".lictor" / "journal.jsonl").open("a", encoding="utf-8") as log:
        log.write('{"forjado":true}\n{"type":"lictor.run.fini\n')

    assert run_lictor(root, agent) == Outcome(0)

    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "**Líneas descartadas**: 2" in report


def _plant(site_packages: Path) -> None:
    write(site_packages, "lab_cheat.py", CHEAT_PLUGIN)
    write(site_packages, "lab_cheat-1.0.dist-info/METADATA", DIST_METADATA)
    write(site_packages, "lab_cheat-1.0.dist-info/entry_points.txt", ENTRY_POINTS)


@known_limitation("un proceso del mismo usuario puede plantar entre prepare y el hash")
def test_nothing_planted_during_the_build_is_trusted(
    make_project: ProjectFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process the agent left behind, at the exact moment: a real orphan did it 3 times of 3.

    Only the moment is simulated, because a race can't hold a strict xfail. The hash is taken
    after the planting, so it vouches for it: only a sandbox keeps that process away.
    """
    root = make_project()
    original = VerificationEnvironment._install_files

    def planting(self: VerificationEnvironment, run_id: str) -> None:
        for site_packages in self.env_dir.glob("lib/python*/site-packages"):
            _plant(site_packages)
        original(self, run_id)

    monkeypatch.setattr(VerificationEnvironment, "_install_files", planting)

    run_lictor(root, Agent())

    built = events_of(root, "env.built")
    require(built, "debía construirse el entorno")
    assert "lab_cheat" not in built[0]["data"]["plugins"]
