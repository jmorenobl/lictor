"""What it means that a control blocks on HEAD, before any agent has touched it.

The first attempt of a run would fail there whatever the agent did, and spend an attempt on what
only the person can fix: a configuration the profile overrides, a dependency not installed, an
unformatted base. `doctor` and a new run say so first (ADR-0015, amendment of 0.19.0).
"""

from collections.abc import Collection, Sequence

from lictor.domain.api import named_breaks
from lictor.domain.controls import Control, ReaderControl
from lictor.domain.reports import ApiComparison


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
        f"({', '.join(changes)}); se ha restaurado. Lo escribió un control o, mientras corrían, "
        "otro programa abierto sobre el repositorio (un editor con git, otra terminal): vuelve a "
        "lanzar, y si se repite, es del proyecto."
    )


def blocking_on_head(
    controls: Sequence[Control], cannot_start: Collection[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the blocking controls into those to run on HEAD and those left out.

    One whose command can't start, or a reader of one, is already wrong in «Comandos de
    verificación», which says whose it is: running it would only fail again and blame the project
    for what may be the machine's.
    """

    def left_out(control: Control) -> bool:
        return control.id in cannot_start or (
            isinstance(control, ReaderControl) and control.reads in cannot_start
        )

    blocking = [control for control in controls if control.mode == "block"]
    return (
        tuple(control.id for control in blocking if not left_out(control)),
        tuple(control.id for control in blocking if left_out(control)),
    )


def head_not_started(control_ids: Sequence[str]) -> str:
    """Tell which blocking controls weren't run on HEAD because their program can't start."""
    names = ", ".join(f"'{cid}'" for cid in control_ids)
    return (
        f"No se ejecutan en HEAD {names}: su programa no arranca, como dice «Comandos de "
        "verificación»."
    )


def head_api_broken(comparison: ApiComparison, ref: str, *, blocks: bool) -> str:
    """Tell what HEAD already breaks of the public API where it left `ref` (H4 of 0.20.0).

    When the control blocks, every attempt would fail on it whatever the agent did: only the
    person can accept it or change the mode. When it measures, a run only warns of new breaks.
    """
    count = len(comparison.broken)
    names = "\n".join(named_breaks(comparison.broken))
    what = "1 nombre" if count == 1 else f"{count} nombres"
    head = (
        f"HEAD ya rompe {what} de la API pública respecto a «{ref}» "
        f"({comparison.base[:12]}), sin aceptar:\n{names}\n"
    )
    if blocks:
        return head + (
            "Con el control 'api' en block, cada intento fallaría haga lo que haga el agente: "
            "acepta esas roturas en `api.accepted` de lictor.json, o pon 'api' en measure."
        )
    return head + "El run solo avisará de lo que rompa de nuevo."


def driver_controls(controls: Sequence[Control]) -> tuple[str, ...]:
    """The blocking controls the driver's commit passes before the seal (G3).

    Every one but those the person said a right driver can't pass (`"driver": false`).
    """
    checked = [control for control in controls if control.checks_driver]
    return blocking_on_head(checked, ())[0]


def driver_control_problem(control_id: str, error: str) -> str:
    """What the driver's agent reads of a blocking control its commit doesn't pass (G3)."""
    return (
        f"El commit del driver no pasa el control '{control_id}', que bloquea cada intento: lo "
        "que escribe el driver (sus tests y sus stubs) tiene que pasarlo, porque una vez sellado "
        f"nadie puede cambiarlo.\n{error.strip()}"
    )


def driver_controls_note(control_ids: Sequence[str]) -> str:
    """For the person, once the driver's attempts run out on a control: what they can change."""
    names = ", ".join(f"'{cid}'" for cid in control_ids)
    return (
        f"Si un driver correcto no puede pasar {names} (un umbral propio, el largo de la línea de "
        "un stub, algo que choca con las instrucciones del driver), cambia una de las dos cosas: "
        'el control, o acceptance.driver_hints; o escribe "driver": false en ese control de '
        "lictor.json, y el commit del driver dejará de pasarlo antes del sello."
    )
