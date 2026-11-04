"""A person registers scenarios as green: what Lictor can't prove, said by someone who knows.

With a fresh driver, a scenario the code already meets passes exactly like one whose test checks
nothing, so Lictor never marks it green on its own. This is how a person does, and says so: one
empty commit with a `Scenario:` per scenario and `Lictor-Asserted-By`, never `Lictor-Run`. What
the red check guarantees for Lictor's own greens doesn't change.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from lictor.application.events import new_event
from lictor.application.ports import AuditSink, Vcs
from lictor.application.spec_history import ASSERTED_TRAILER, SpecHistory
from lictor.domain.errors import EnvironmentUnavailableError, SpecError
from lictor.domain.model import PreconditionProblem
from lictor.domain.rules import ScenarioClass, registration_problems

_STATEMENTS: dict[ScenarioClass, str] = {
    "new": (
        "Afirmas que el código de HEAD ya cumple {sid}. Lictor no lo ha probado: el driver que "
        "se escriba después solo tendrá que pasarlo, y un test que no comprueba nada también pasa."
    ),
    "reopened": (
        "Afirmas que la especificación de {sid} solo cambió de redacción y que la implementación "
        "ya cumple la nueva. Lictor no lo ha probado."
    ),
}


@dataclass(frozen=True)
class RegistrationPlan:
    """What registering would assert, or why it can't be done."""

    scenarios: dict[str, ScenarioClass] = field(default_factory=dict)
    identity: str = ""
    problems: tuple[PreconditionProblem, ...] = ()

    def statements(self) -> list[str]:
        return [_STATEMENTS[cls].format(sid=sid) for sid, cls in self.scenarios.items()]


class RegisterScenarios:
    """Check what a person asks to register, then commit it on top of HEAD."""

    def __init__(
        self, vcs: Vcs, history: SpecHistory, audit: AuditSink, project_root: Path
    ) -> None:
        self.vcs = vcs
        self.history = history
        self.audit = audit
        self.project_root = project_root

    def plan(self, requested: Sequence[str]) -> RegistrationPlan:
        """Say what registering `requested` asserts; all of them or none."""
        changed = self.vcs.status()
        if changed:
            message = (
                f"El árbol de trabajo tiene cambios sin confirmar: {', '.join(changed)}. "
                "Confírmalos o descártalos: el registro va sobre HEAD."
            )
            return RegistrationPlan(problems=(PreconditionProblem("repository", message),))
        try:
            identity = self.vcs.committer_identity()
        except EnvironmentUnavailableError as err:
            return RegistrationPlan(problems=(PreconditionProblem("environment", str(err)),))
        try:
            scenarios = self.history.spec_source.load(self.project_root / "features")
        except SpecError as err:
            return RegistrationPlan(
                problems=(PreconditionProblem("config", f"Spec inválida: {err}"),)
            )
        classes = self.history.classify({s.id.value: s.spec_hash for s in scenarios})
        problems = registration_problems(requested, classes)
        if problems:
            return RegistrationPlan(
                problems=tuple(PreconditionProblem("config", problem) for problem in problems)
            )
        chosen = {sid: classes[sid] for sid in dict.fromkeys(requested)}
        return RegistrationPlan(chosen, identity)

    def execute(self, plan: RegistrationPlan, run_id: str) -> str:
        """Commit the registration and move the branch to it; return the commit."""
        head = self.vcs.get_head_sha()
        names = list(plan.scenarios)
        candidate = self.vcs.commit_tree(
            self.vcs.get_tree(head),
            f"chore(lictor): registro manual de {', '.join(names)}",
            {"Scenario": names, ASSERTED_TRAILER: plan.identity},
            empty=True,
        )
        self.vcs.advance_head(candidate.sha, head)
        for sid, cls in plan.scenarios.items():
            self.audit.event_log.append(
                new_event(
                    self.audit.clock,
                    "lictor.scenario.asserted.v1",
                    run_id,
                    scenario_id=sid,
                    data={"commit": candidate.sha, "class": cls, "by": plan.identity},
                )
            )
        return candidate.sha
