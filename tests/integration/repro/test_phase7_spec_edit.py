"""Phase 7: what a person editing the specification during a run should expect.

The seal is compared with the working tree, so an edit stops the run at once rather than letting
it go on with a specification nobody verified; the edit itself is never touched.
"""

from lictor.domain.model import AgentStatus, AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    events_of,
    git,
    good_impl,
    green_ids,
    quarantined,
    run_lictor,
)

EDIT = "\n# Nota de quien escribe la especificación, sin confirmar todavía.\n"


def test_an_uncommitted_feature_edit_stops_the_run_with_10_and_survives(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    feature = root / "features" / "iva.feature"

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if scenario_id == "IVA-001" and attempt == 1:
            # The person, in their own checkout, while the agent works in its copy.
            feature.write_text(feature.read_text(encoding="utf-8") + EDIT, encoding="utf-8")
        return good_impl(task, scenario_id, attempt)

    agent = Agent(impl=impl)
    outcome = run_lictor(root, agent)

    assert outcome.exit_code == 10
    (verified,) = events_of(root, "verify.finished")
    assert verified["data"]["failed_at"] == "L0-sello"
    assert "cambiaron a mitad de ejecución" in events_of(root, "run.finished")[0]["data"]["reason"]
    assert green_ids(root) == []
    assert quarantined(root) == []
    assert feature.read_text(encoding="utf-8").endswith(EDIT)

    # The next run won't start over the edit either (D4), and still leaves it alone.
    again = run_lictor(root, agent)

    assert again.exit_code == 10
    assert "cambios sin confirmar" in events_of(root, "run.finished")[-1]["data"]["reason"]
    assert agent.calls == ["driver", "IVA-001"]
    assert git(root, "status", "--short").strip() == "M features/iva.feature"
    assert feature.read_text(encoding="utf-8").endswith(EDIT)
