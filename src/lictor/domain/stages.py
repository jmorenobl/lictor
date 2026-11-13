"""When each control runs: in every attempt of a run, or only in `lictor check` (ADR-0007).

A `check` control never runs in a run, so the run names it twice and never per attempt: in
`report.md`, from `run.started`, and in one sentence of the final reason. This module decides
which controls go where and what that sentence says.
"""

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from lictor.domain.run_branches import integration_hint

Stage = Literal["attempt", "check"]
ATTEMPT: Stage = "attempt"
CHECK: Stage = "check"


class Staged(Protocol):
    """A control as the domain sees it here: something with an id and a stage."""

    @property
    def id(self) -> str: ...

    @property
    def stage(self) -> str: ...


def controls_of_stage[StagedT: Staged](controls: Sequence[StagedT], stage: Stage) -> list[StagedT]:
    """The controls of one stage, in the order they were declared."""
    return [control for control in controls if control.stage == stage]


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


def check_stage_note(check_ids: Sequence[str]) -> str:
    """What is left to check after a run without a linked worktree."""
    return (
        f"Quedan sin comprobar {spoken_list(check_ids)}, que solo corren en `lictor check`: "
        "ejecútalo antes de integrar."
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


def final_reason(
    reason: str,
    *,
    check_ids: Sequence[str],
    committed: bool,
    branch: str = "",
    worktree: str = "",
) -> str:
    """The reason a run ends with, telling what is left to check and how the result gets in.

    `branch` is given only to a run that reached the end in a linked worktree: then the result
    is that branch, and the sentence on the `check` controls goes into `integration_hint`. The
    sentence only comes when the run committed a green: without commits nothing is left to check.
    """
    pending = bool(check_ids) and committed
    if branch:
        hint = (
            integration_with_check_stage(branch, worktree, check_ids)
            if pending
            else integration_hint(branch)
        )
        return with_sentence(reason, hint)
    if pending:
        return with_sentence(reason, check_stage_note(check_ids))
    return reason
