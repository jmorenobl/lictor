"""What a change left with no control executing it (H2 of 0.20.0), from git, the survey and JUnit.

The change is a diff between two commits: the run's start and its last commit, or the base of
`check --base` and what it verified. The survey reads the final tree as the start contract reads
HEAD, and the JUnit of each command says which test files had cases. Nothing is judged here: the
domain's `unexecuted_changes` does, and its facts go to the person.
"""

from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path

from lictor.application.ports import ReportReader, Vcs, VerificationCommands
from lictor.application.start_survey import StartSurvey
from lictor.domain.blind_spots import StartRules, blind_spots
from lictor.domain.reports import JUnitReport
from lictor.domain.unexecuted import (
    FinalTree,
    UnexecutedChanges,
    files_with_cases,
    unexecuted_changes,
)

# Where a verification left the JUnit of a command, by its id; None when it left none.
type ReportOf = Callable[[str], Path | None]


@dataclass(frozen=True)
class UnexecutedSurvey:
    """The project, the survey and the rules of the start, the JUnit reader and the commands that
    have one."""

    vcs: Vcs
    project_root: Path
    survey: StartSurvey
    rules: StartRules
    junit: ReportReader[JUnitReport]
    junit_ids: Collection[str]

    def execute(
        self,
        environment: VerificationCommands,
        commits: tuple[str, str],
        evidence: Path,
        reports: ReportOf,
    ) -> UnexecutedChanges | str:
        """What changed from `commits[0]` to `commits[1]` that no control executed, or why it
        can't be told."""
        base, head = commits
        vcs = self.vcs
        changed = vcs.changed_paths(base, head)
        if not changed:
            return UnexecutedChanges()
        surveyed = self.survey.execute(environment, vcs, self.project_root, evidence, rev=head)
        if isinstance(surveyed, str):
            return f"no se pudo leer lo que ve cada control en {head[:12]}: {surveyed}"
        present = frozenset(entry.path for entry in vcs.tree_entries(head))
        test_files = [test.path for test in surveyed.tests]
        ran: dict[str, frozenset[str] | None] = {}
        for command in self.junit_ids:
            path = reports(command)
            read = self.junit.read(path) if path is not None else None
            ran[command] = (
                files_with_cases((case.classname for case in read.cases), test_files)
                if isinstance(read, JUnitReport)
                else None
            )
        tree = FinalTree(surveyed, blind_spots(surveyed, self.rules), present)
        return unexecuted_changes(changed, tree, self.rules, ran)
