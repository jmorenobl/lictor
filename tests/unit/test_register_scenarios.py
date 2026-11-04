"""Unit tests for registering scenarios as a person's assertion."""

from pathlib import Path
from unittest.mock import MagicMock

from lictor.application.ports import AuditSink, Clock, EventLog, SpecSource, Vcs
from lictor.application.spec_history import SpecHistory
from lictor.application.usecases.register_scenarios import RegisterScenarios
from lictor.domain.errors import EnvironmentUnavailableError, SpecError
from lictor.domain.model import Candidate, PreconditionProblem, Scenario

SCENARIOS = [Scenario("IVA-001", "a", "f", 1, "h1"), Scenario("IVA-002", "b", "f", 2, "h2")]


def _use_case() -> tuple[RegisterScenarios, MagicMock, MagicMock, MagicMock]:
    vcs = MagicMock(spec=Vcs)
    vcs.status.return_value = []
    vcs.committer_identity.return_value = "Ana <ana@example.com>"
    vcs.get_head_sha.return_value = "head"
    vcs.get_tree.return_value = "arbol"
    vcs.commit_tree.return_value = Candidate("nuevo", "arbol", "head")
    history = MagicMock(spec=SpecHistory)
    history.spec_source = MagicMock(spec=SpecSource)
    history.spec_source.load.return_value = SCENARIOS
    history.classify.return_value = {"IVA-001": "new", "IVA-002": "reopened"}
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-11-04T10:00:00Z"
    use_case = RegisterScenarios(vcs, history, AuditSink(event_log, clock), Path("/p"))
    return use_case, vcs, history, event_log


def test_the_plan_says_what_each_class_asserts() -> None:
    use_case, _, history, _ = _use_case()

    plan = use_case.plan(["IVA-002", "IVA-001", "IVA-002"])

    assert plan.problems == ()
    assert list(plan.scenarios) == ["IVA-002", "IVA-001"]
    assert plan.identity == "Ana <ana@example.com>"
    reopened, new = plan.statements()
    assert reopened.startswith("Afirmas que la especificación de IVA-002 solo cambió de redacción")
    assert new.startswith("Afirmas que el código de HEAD ya cumple IVA-001. Lictor no lo ha")
    history.classify.assert_called_once_with({"IVA-001": "h1", "IVA-002": "h2"})


def test_a_dirty_tree_is_the_repositorys_problem() -> None:
    use_case, vcs, _, _ = _use_case()
    vcs.status.return_value = ["src/a.py"]

    (problem,) = use_case.plan(["IVA-001"]).problems

    assert problem.kind == "repository"
    assert "src/a.py" in problem.message
    vcs.committer_identity.assert_not_called()


def test_no_identity_is_the_machines_problem() -> None:
    use_case, vcs, _, _ = _use_case()
    vcs.committer_identity.side_effect = EnvironmentUnavailableError("sin identidad")

    assert use_case.plan(["IVA-001"]).problems == (
        PreconditionProblem("environment", "sin identidad"),
    )


def test_an_invalid_spec_is_the_projects_problem() -> None:
    use_case, _, history, _ = _use_case()
    history.spec_source.load.side_effect = SpecError("ID repetido")

    assert use_case.plan(["IVA-001"]).problems == (
        PreconditionProblem("config", "Spec inválida: ID repetido"),
    )


def test_one_scenario_that_cant_be_registered_registers_none() -> None:
    use_case, _, history, _ = _use_case()
    history.classify.return_value = {"IVA-001": "new", "IVA-002": "green"}

    plan = use_case.plan(["IVA-001", "IVA-002"])

    assert plan.scenarios == {}
    assert [p.kind for p in plan.problems] == ["config"]
    assert "IVA-002 ya está en verde" in plan.problems[0].message


def test_registering_makes_one_empty_commit_and_moves_the_branch() -> None:
    use_case, vcs, _, event_log = _use_case()
    plan = use_case.plan(["IVA-001", "IVA-002"])

    sha = use_case.execute(plan, "register-1")

    assert sha == "nuevo"
    vcs.commit_tree.assert_called_once_with(
        "arbol",
        "chore(lictor): registro manual de IVA-001, IVA-002",
        {"Scenario": ["IVA-001", "IVA-002"], "Lictor-Asserted-By": "Ana <ana@example.com>"},
        empty=True,
    )
    vcs.get_tree.assert_called_once_with("head")
    vcs.advance_head.assert_called_once_with("nuevo", "head")
    events = [call.args[0] for call in event_log.append.call_args_list]
    assert [(e.type, e.scenarioid, e.runid) for e in events] == [
        ("lictor.scenario.asserted.v1", "IVA-001", "register-1"),
        ("lictor.scenario.asserted.v1", "IVA-002", "register-1"),
    ]
    assert events[1].data == {"commit": "nuevo", "class": "reopened", "by": "Ana <ana@example.com>"}
