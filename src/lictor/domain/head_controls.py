"""What it means that a control blocks on HEAD, before any agent has touched it.

The first attempt of a run would fail there whatever the agent did, and spend an attempt on what
only the person can fix: a configuration the profile overrides, a dependency not installed, an
unformatted base. `doctor` and a new run say so first (ADR-0015, amendment of 0.19.0).
"""

from collections.abc import Sequence


def head_control_problem(control_id: str, error: str, evidence: str) -> str:
    """Tell the person that a blocking control fails on HEAD, and that it is theirs to fix.

    `error` is the condensed one an agent would read: the reason and the first failed cases.
    """
    return (
        f"El control '{control_id}' no pasa en HEAD, antes de que ningún agente toque nada. Es "
        f"del proyecto y el agente no puede arreglarlo: corrígelo en un commit tuyo, o cambia o "
        f"desactiva el control '{control_id}' en lictor.json. Su salida entera está en "
        f"{evidence}.\n{error.strip()}"
    )


def head_tampered(changes: Sequence[str]) -> str:
    """Tell that running the controls on HEAD changed Lictor's state, which was restored."""
    return (
        "Los controles cambiaron el estado de Lictor al ejecutarse en HEAD "
        f"({', '.join(changes)}); se ha restaurado. Algo del proyecto escribe fuera de su árbol."
    )
