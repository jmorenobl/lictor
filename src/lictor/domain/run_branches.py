"""Which branch a run works on, which run `lictor run` resumes, and what to say about both.

Every run gets a worktree and a branch of its own (ADR-0005): the result is a branch the person
integrates, never a commit on the branch they were on.
"""

import re
from collections.abc import Sequence

RUN_BRANCH_PREFIX = "lictor/"
# One path component: it also names the worktree's directory, so no `/` and no `..`.
_BRANCH_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_LISTED_PATHS = 5


def branch_name_problem(name: str) -> str | None:
    """Say why a name given with `--branch` can't name a run's branch, or None if it can."""
    if _BRANCH_NAME.fullmatch(name) and ".." not in name and not name.endswith((".", ".lock")):
        return None
    return (
        f"«{name}» no vale como nombre de rama: usa letras, números, «.», «_» o «-», "
        "empezando por una letra o un número, sin «/» ni «..»."
    )


def run_branch(name: str | None, run_id: str) -> str:
    """Name the branch of a run: the one given, or the run's own id."""
    return f"{RUN_BRANCH_PREFIX}{name or run_id}"


def resume_choice(pending: Sequence[str], wanted: str | None) -> tuple[str | None, str | None]:
    """Pick, among the branches whose run can go on, the one to resume, or say why none.

    With `--branch` only that one counts, and none means a new run. Without it, a single one is
    resumed and several have to be told apart: guessing would spend on the wrong one.
    """
    if wanted is not None:
        return (wanted if wanted in pending else None), None
    if len(pending) == 1:
        return pending[0], None
    if len(pending) > 1:
        return None, (
            f"hay varias ejecuciones que se pueden reanudar: {', '.join(sorted(pending))}. "
            "No se ha tocado nada: elige una con `lictor run --branch <nombre>`, o empieza otra "
            "con `lictor run --new`."
        )
    return None, None


def integration_hint(branch: str) -> str:
    """Tell where the result is and how it gets in: always a step of the person's own."""
    return (
        f"El resultado está en la rama {branch}: intégralo con `git merge {branch}` "
        "o con una pull request."
    )


def uncommitted_left_out(paths: Sequence[str]) -> str:
    """Warn that what isn't committed stays in the checkout: the run starts from the last commit."""
    shown = ", ".join(paths[:_LISTED_PATHS])
    rest = len(paths) - _LISTED_PATHS
    more = f" y {rest} más" if rest > 0 else ""
    return (
        "La ejecución parte del último commit, así que lo que no has confirmado se queda "
        f"en tu checkout y no entra en ella: {shown}{more}."
    )
