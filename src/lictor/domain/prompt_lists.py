"""The rules of the agents' prompts that name a list, with a sentence of their own without one.

A rule with an empty list read «No añadas supresiones: ninguna.» or «Los escenarios ninguno ya
funcionan» (0.23.0, M8 of Fable's review of the pull request). Each rule here is a whole line of
its prompt, ending in a line break, or nothing when an empty list leaves nothing to say: the
template has the placeholder at the start of the line that follows.
"""

from collections.abc import Sequence
from typing import Final, Literal

Role = Literal["driver", "implement", "refactor"]

_KEEP_MOVED: Final = (
    "Mover o juntar las que ya hay no cuenta, aunque sea a otro fichero; cambiar su texto, sí."
)
_SEALED: Final = "El driver queda sellado: si las lleva, se rechaza."
_NO_LIST: Final = (
    "- No añadas supresiones: ningún comentario ni llamada que calle un aviso o salte un test.\n"
)


def _listed(items: Sequence[str]) -> str:
    return ", ".join(items)


def suppressions_rule(suppressions: Sequence[str], role: Role, *, gated: bool = False) -> str:
    """The suppressions the agent mustn't add: the profile's and the project's, or the idea.

    `gated` says `L0-supresiones` judges them, with `banned_patterns` alone too: then a sealed
    driver with one is still rejected, and the driver hears so.
    """
    sealed = f" {_SEALED}" if role == "driver" and gated else ""
    if not suppressions:
        return f"{_NO_LIST.rstrip()}{sealed}\n"
    tail = sealed if role == "driver" else f" {_KEEP_MOVED}"
    return f"- No añadas supresiones: {_listed(suppressions)}.{tail}\n"


def forbidden_rule(
    forbidden: Sequence[str], role: Role, exempt: Sequence[str] = (), driver: Sequence[str] = ()
) -> str:
    """The paths no agent creates or changes anywhere, and the driver's exception to them."""
    if not forbidden:
        return ""
    paths = _listed(forbidden)
    if role == "driver":
        allowed = f", salvo {_listed(exempt)} dentro de {_listed(driver)}" if exempt else ""
        return (
            f"- No crees ni modifiques, en ninguna carpeta: {paths}{allowed}. Se descartan: lo que "
            "hay ahí\n  lo cambia solo una persona.\n"
        )
    if role == "implement":
        return (
            f"- Tampoco crees ni modifiques, en ninguna carpeta: {paths}.\n"
            "  Lo que hay ahí lo cambia solo una persona.\n"
        )
    return f"- Tampoco crees ni modifiques, en ninguna carpeta: {paths}.\n"


def greens_rule(green_ids: Sequence[str], role: Role) -> str:
    """The scenarios already green, which must stay so; the refactor's rule stands without them."""
    if role == "refactor":
        named = (
            f" Todos estos escenarios funcionan y deben seguir funcionando: {_listed(green_ids)}."
            if green_ids
            else ""
        )
        return f"- No cambies el comportamiento.{named}\n"
    if not green_ids:
        return ""
    if role == "driver":
        return f"- Los escenarios {_listed(green_ids)} ya funcionan y sus tests deben pasar.\n"
    return f"- Estos escenarios ya funcionan y deben seguir funcionando: {_listed(green_ids)}.\n"


def protected_rule(protected: Sequence[str]) -> str:
    """What the driver doesn't touch besides its own paths."""
    named = f" No toques {_listed(protected)}." if protected else ""
    return f"- No implementes la funcionalidad.{named}\n"
