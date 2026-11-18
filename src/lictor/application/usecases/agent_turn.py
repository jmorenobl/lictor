"""One call to the agent in a copy of HEAD, as the implementer and the refactor make it.

The call is recorded before it starts, runs inside `IntegritySnapshot`, and ends with what it means
for the run (`classify_agent`) and its events. What differs between roles is the prompt and which
attempt the call belongs to once it ends.
"""

from dataclasses import dataclass, replace
from pathlib import Path

from lictor.application.ports import Clock, EventLog, Executor, Vcs
from lictor.application.run_files import agent_workspace
from lictor.application.usecases.agent_events import (
    AgentCall,
    agent_finished_event,
    agent_started_event,
    denial_event,
    tamper_event,
)
from lictor.application.usecases.verify_attempt import AgentCopyOutcome, IntegritySnapshot
from lictor.domain.errors import CandidateError
from lictor.domain.model import AgentSignal, AgentTask
from lictor.domain.rules import classify_agent, count_agent_failures, keeps_attempt


@dataclass(frozen=True)
class AgentTurn:
    """What a call left, what it means, and how many calls in a row the backend has failed."""

    outcome: AgentCopyOutcome
    signal: AgentSignal
    failures: int
    keeps: bool  # the backend failed, not the work: the attempt isn't spent


@dataclass(frozen=True)
class AgentTurns:
    """Make calls to the agent, each in a fresh copy of HEAD outside the project."""

    vcs: Vcs
    executor: Executor
    event_log: EventLog
    clock: Clock

    def take(
        self,
        task: AgentTask,
        started: AgentCall,
        *,
        max_attempts: int,
        agent_failures: int,
        attempt_if_kept: int,
    ) -> AgentTurn:
        """Run the call and record it; one that keeps its attempt is recorded under the old one."""
        self.event_log.append(agent_started_event(self.clock, started, max_attempts))
        outcome = self._run_in_copy(task, started.project_root)
        result = outcome.agent_result

        failures = count_agent_failures(result.status, agent_failures)
        signal = classify_agent(
            result.status,
            failures,
            spec_conflict=bool(result.spec_conflict),
            tampered=bool(outcome.tampered),
            unreadable=bool(outcome.unreadable),
        )
        keeps = keeps_attempt(signal, failures)
        call = replace(
            started,
            attempt=attempt_if_kept if keeps else started.attempt,
            signal=signal,
            cli_version=result.cli_version,
        )
        event = agent_finished_event(self.clock, call, result)
        self.event_log.append(event)
        if result.permission_denials:
            self.event_log.append(denial_event(result, event))
        if outcome.tampered:
            self.event_log.append(tamper_event(event, outcome.tampered))
        return AgentTurn(outcome, signal, failures, keeps)

    def _run_in_copy(self, task: AgentTask, project_root: Path) -> AgentCopyOutcome:
        snapshot = IntegritySnapshot(project_root / ".lictor", project_root, self.vcs)
        with agent_workspace(self.vcs, project_root) as workspace:
            agent_result = self.executor.run(replace(task, cwd=workspace))
            snapshot.verify_and_restore()
            if snapshot.changes:
                return AgentCopyOutcome(agent_result, tampered=tuple(snapshot.changes))
            # A declared conflict keeps its tree: what decides is whether it verifies (F54).
            if agent_result.status == "rate_limited":
                return AgentCopyOutcome(agent_result)
            try:
                return AgentCopyOutcome(agent_result, tree=self.vcs.capture(workspace))
            except CandidateError as exc:
                return AgentCopyOutcome(agent_result, unreadable=str(exc))
