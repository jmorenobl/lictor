"""The scope of a refactor, drawn from its green's commit and what its verification measured.

`RefactorScenario` writes the prompt from it and `VerifyAttempt` judges `L0-alcance` with it: one
function, so what the prompt names is what the gate allows (F84). It keeps nothing: git objects
don't change, so a resume draws the same scope again.
"""

from collections.abc import Mapping, Sequence

from lictor.application.ports import Vcs
from lictor.domain.refactor import Findings, Measure
from lictor.domain.refactor_scope import RefactorScope, refactor_scope


def baseline_findings(baseline: Mapping[str, Measure]) -> list[Findings]:
    """What each control of the code found in the green's verification, in their order."""
    return [m.findings for m in baseline.values() if m.findings is not None]


def green_scope(
    vcs: Vcs, refactor_of: str, baseline: Mapping[str, Measure], code: Sequence[str]
) -> RefactorScope:
    """Where the refactor of the green `refactor_of` may work, and what it is asked there."""
    parent = vcs.parent_of(refactor_of)
    changed = vcs.changed_lines(parent, refactor_of) if parent else {}
    return refactor_scope(changed, code, baseline_findings(baseline))
