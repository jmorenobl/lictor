"""When each control runs: in every attempt of a run, or only in `lictor check` (ADR-0007).

A `check` control never runs in a run, so the run names it twice and never per attempt: in
`report.md`, from `run.started`, and in one sentence of the final reason. This module decides
which controls go where and what that sentence says.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lictor.domain.run_branches import integration_hint

Stage = Literal["attempt", "check"]
ATTEMPT: Stage = "attempt"
CHECK: Stage = "check"


def spoken_list(names: Sequence[str]) -> str:
    """Join names as Spanish says them: «a», «a y b», «a, b y c»."""
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} y {names[-1]}"


def committed_greens(status: Mapping[str, str], green_at_load: Sequence[str]) -> bool:
    """Whether the run committed a green: implemented, collateral or registered again.

    A scenario green when the spec loaded was green from before: its commit isn't this run's.
    """
    before = set(green_at_load)
    return any(st == "green" and sid not in before for sid, st in status.items())


def check_stage_note(check_ids: Sequence[str], branch: str = "", worktree: str = "") -> str:
    """What is left to check after a run that didn't reach the end of a linked worktree.

    With a branch, the run stopped early in a linked worktree: what it committed is there, and
    `lictor check` anywhere else would verify something else.
    """
    where = f" en `{worktree}`, donde está la rama {branch}," if branch else ""
    return (
        f"Quedan sin comprobar {spoken_list(check_ids)}, que solo corren en `lictor check`: "
        f"ejecútalo{where} antes de integrar."
    )


def integration_with_check_stage(branch: str, worktree: str, check_ids: Sequence[str]) -> str:
    """`integration_hint` with what is left to check, and where to check it."""
    return (
        f"El resultado está en la rama {branch}, en `{worktree}`. Quedan sin comprobar "
        f"{spoken_list(check_ids)}, que solo corren en `lictor check`: ejecútalo en ese "
        "worktree, o deja que lo haga la CI de la pull request, antes de integrarlo con "
        f"`git merge {branch}` o con una pull request."
    )


def check_controls_warning(check_ids: Sequence[str]) -> str:
    """What `doctor` says while any control only runs in `lictor check`."""
    return (
        f"Controles que solo corren en `lictor check`: {spoken_list(check_ids)}. Un run no los "
        "ejecuta: solo protegen lo que integras si tu CI ejecuta `lictor check`."
    )


def with_sentence(reason: str, sentence: str) -> str:
    """Add a sentence to a reason, which may or may not end with its own full stop.

    A reason that already ends with it stays as it is: `report` finishes again with the reason
    of a node that had already finished.
    """
    if reason.endswith(sentence):
        return reason
    if not reason:
        return sentence
    return f"{reason.rstrip().removesuffix('.')}. {sentence}"


@dataclass(frozen=True)
class ResultBranch:
    """The branch of a linked worktree, where a run leaves its result, and whether it ended."""

    branch: str
    worktree: str
    reached_end: bool


def final_reason(
    reason: str,
    *,
    check_ids: Sequence[str],
    committed: bool,
    result: ResultBranch | None = None,
) -> str:
    """The reason a run ends with, telling what is left to check and how the result gets in.

    A run that reached its end in a linked worktree gets `integration_hint`, with the sentence on
    the `check` controls fused into it; one that stopped early there gets only that sentence,
    naming the worktree. The sentence only comes when the run committed a green: without commits
    nothing is left to check.
    """
    pending = bool(check_ids) and committed
    if result is not None and result.reached_end:
        hint = (
            integration_with_check_stage(result.branch, result.worktree, check_ids)
            if pending
            else integration_hint(result.branch, result.worktree)
        )
        return with_sentence(reason, hint)
    if not pending:
        return reason
    if result is None:
        return with_sentence(reason, check_stage_note(check_ids))
    return with_sentence(reason, check_stage_note(check_ids, result.branch, result.worktree))
