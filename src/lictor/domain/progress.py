"""What a run has done so far, folded from its journal.

The same fold gives the lines `lictor run` prints as it works (F10) and the counts `lictor status`
shows (F11), so both say the same thing: what the journal recorded, never a guess.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from lictor.domain.model import Event

DRIVER_OWNER: Final[str] = "driver"

_STATUS_TEXT: Final[Mapping[str, str]] = {
    "green": "verde",
    "pending": "pendiente",
    "quarantined": "en cuarentena",
}
_NODE_TEXT: Final[Mapping[str, str]] = {
    "preflight": "comprobar las precondiciones",
    "load_spec": "cargar la especificación",
    "build_driver": "escribir el driver",
    "red_check": "comprobar el rojo del driver",
    "pick_next": "elegir el siguiente escenario",
    "implement": "implementar",
    "verify": "verificar",
    "commit": "confirmar el verde",
    "quarantine": "poner en cuarentena",
    "pause": "pausa",
    "report": "escribir el informe",
}
_MS_PER_S: Final[int] = 1000
_S_PER_MIN: Final[int] = 60
_MIN_PER_H: Final[int] = 60


def status_text(status: str) -> str:
    """A scenario's status as the person reads it."""
    return _STATUS_TEXT.get(status, status)


def node_text(node: str) -> str:
    """What a step of the graph does, instead of its name in the code."""
    return _NODE_TEXT.get(node, node)


def cost_text(usd: float, decimals: int = 2) -> str:
    """A cost in dollars with a decimal comma: `0,12 $`."""
    return f"{usd:.{decimals}f} $".replace(".", ",")


def duration_text(ms: int) -> str:
    """A duration as a person says it: `59 s`, `2 min 5 s`, `1 h 1 min`."""
    seconds = round(ms / _MS_PER_S)
    if seconds < _S_PER_MIN:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, _S_PER_MIN)
    if minutes < _MIN_PER_H:
        return f"{minutes} min {seconds} s" if seconds else f"{minutes} min"
    hours, minutes = divmod(minutes, _MIN_PER_H)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def calls_text(count: int) -> str:
    """How many calls to the agent, agreeing in number."""
    return f"{count} llamada" if count == 1 else f"{count} llamadas"


def _pending_text(count: int) -> str:
    return f"{count} pendiente" if count == 1 else f"{count} pendientes"


@dataclass
class Spend:
    """What the agent calls of one owner, a scenario or the driver, have cost."""

    calls: int = 0
    cost_usd: float = 0.0
    agent_ms: int = 0
    verify_ms: int = 0


def _number(raw: object) -> float:
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0


@dataclass
class RunProgress:
    """Fold a run's events, one at a time, into what it has done and a line per step."""

    scenario_ids: list[str] = field(default_factory=list)
    previous: list[str] = field(default_factory=list)  # green before this run started
    implemented: list[str] = field(default_factory=list)
    collateral: list[str] = field(default_factory=list)
    reregistered: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)
    _spend: dict[str, Spend] = field(default_factory=dict)
    _held: list[str] = field(default_factory=list)  # collateral greens not told yet

    @property
    def calls(self) -> int:
        return sum(spend.calls for spend in self._spend.values())

    @property
    def cost_usd(self) -> float:
        return sum(spend.cost_usd for spend in self._spend.values())

    @property
    def greens(self) -> list[str]:
        """Every scenario green now, in the order of the spec."""
        green = {*self.previous, *self.implemented, *self.collateral, *self.reregistered}
        return [sid for sid in self.scenario_ids if sid in green]

    def spend(self, owner: str) -> Spend:
        """What one owner's calls cost so far; nothing if it made none."""
        return self._spend.get(owner, Spend())

    def observe(self, event: Event) -> list[str]:
        """Take one event and return the lines it tells, if any."""
        collateral = event.type == "lictor.scenario.green.v1" and bool(event.data.get("collateral"))
        told = [] if collateral else self.flush()
        handler = self._handlers().get(event.type)
        return [*told, *(handler(event) if handler else [])]

    def flush(self) -> list[str]:
        """Tell the collateral greens held so far, on one line."""
        held, self._held = self._held, []
        if not held:
            return []
        if len(held) == 1:
            return [f"{held[0]}: verde colateral, sin llamar al agente."]
        return [f"{', '.join(held)}: verdes colaterales, sin llamar al agente."]

    def _handlers(self) -> Mapping[str, Callable[[Event], list[str]]]:
        return {
            "lictor.run.started.v1": lambda _: ["Entorno de verificación listo."],
            "lictor.run.resumed.v1": self._resumed,
            "lictor.spec.loaded.v1": self._spec_loaded,
            "lictor.agent.started.v1": self._agent_started,
            "lictor.agent.finished.v1": self._agent_finished,
            "lictor.verify.finished.v1": self._verify_finished,
            "lictor.driver.rejected.v1": self._driver_rejected,
            "lictor.driver.sealed.v1": self._driver_sealed,
            "lictor.driver.checked.v1": lambda _: [
                "driver: el driver sellado sigue siendo válido."
            ],
            "lictor.scenario.reopened.v1": lambda event: [
                f"{event.scenarioid}: reabierto, su especificación cambió desde su verde."
            ],
            "lictor.scenario.reregistered.v1": self._reregistered,
            "lictor.scenario.green.v1": self._green,
            "lictor.scenario.quarantined.v1": self._quarantined,
            "lictor.run.finished.v1": self._run_finished,
        }

    def _owner(self, event: Event) -> Spend:
        return self._spend.setdefault(event.scenarioid or DRIVER_OWNER, Spend())

    def _resumed(self, event: Event) -> list[str]:
        return [f"Reanudando la ejecución: {node_text(str(event.data.get('resume_node', '')))}."]

    def _spec_loaded(self, event: Event) -> list[str]:
        raw = event.data.get("scenarios")
        scenarios = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
        self.scenario_ids = [str(s.get("id", "")) for s in scenarios]
        self.previous = [str(s.get("id", "")) for s in scenarios if s.get("green")]
        total, green = len(self.scenario_ids), len(self.previous)
        return [f"Escenarios: {total} ({green} en verde, {_pending_text(total - green)})."]

    def _agent_started(self, event: Event) -> list[str]:
        attempt = f"intento {event.data.get('attempt')}"
        if event.data.get("max_attempts"):
            attempt = f"{attempt} de {event.data['max_attempts']}"
        if event.scenarioid is None:
            return [f"driver: escribiendo el driver, {attempt}…"]
        return [f"{event.scenarioid}: implementando, {attempt}…"]

    def _agent_finished(self, event: Event) -> list[str]:
        spend = self._owner(event)
        spend.calls += 1
        spend.cost_usd += _number(event.data.get("cost_usd"))
        spend.agent_ms += int(_number(event.data.get("duration_ms")))
        owner = event.scenarioid or DRIVER_OWNER
        lines: list[str] = []
        skills = event.data.get("project_skills")
        if isinstance(skills, list) and skills:
            names = ", ".join(str(name) for name in skills)
            lines.append(
                f"⚠ {owner}: el agente cargó skills del proyecto ({names}), y no debería: "
                "comprueba la versión de Claude Code."
            )
        if event.data.get("signal") == "error":
            lines.append(f"{owner}: la llamada al agente falló; se repite sin gastar el intento.")
        return lines

    def _verify_finished(self, event: Event) -> list[str]:
        durations = event.data.get("control_durations")
        if isinstance(durations, dict):
            self._owner(event).verify_ms += int(sum(_number(v) for v in durations.values()))
        if event.data.get("ok"):
            return []
        failed_at = event.data.get("failed_at")
        where = f" en {failed_at}" if failed_at else ""
        return [f"{event.scenarioid}: el intento {event.data.get('attempt')} falló{where}."]

    def _driver_rejected(self, event: Event) -> list[str]:
        attempt, reason = event.data.get("attempt"), event.data.get("reason")
        return [f"driver: el intento {attempt} se rechazó ({reason})."]

    def _driver_sealed(self, _: Event) -> list[str]:
        spend = self.spend(DRIVER_OWNER)
        return [f"driver: sellado ({calls_text(spend.calls)}, {cost_text(spend.cost_usd)})."]

    def _reregistered(self, event: Event) -> list[str]:
        sid = str(event.scenarioid)
        self.reregistered.append(sid)
        return [f"{sid}: sigue pasando con la especificación nueva; registrado sin agente."]

    def _green(self, event: Event) -> list[str]:
        sid = str(event.scenarioid)
        if event.data.get("collateral"):
            self.collateral.append(sid)
            self._held.append(sid)
            return []
        self.implemented.append(sid)
        spend = self.spend(sid)
        took = duration_text(spend.agent_ms + spend.verify_ms)
        attempt = event.data.get("attempt")
        return [f"{sid}: verde, intento {attempt}, {took}, {cost_text(spend.cost_usd)}."]

    def _quarantined(self, event: Event) -> list[str]:
        sid = str(event.scenarioid)
        self.quarantined.append(sid)
        spend = self.spend(sid)
        reason = event.data.get("reason")
        return [
            f"{sid}: en cuarentena ({reason}), {calls_text(spend.calls)}, "
            f"{cost_text(spend.cost_usd)}."
        ]

    def _run_finished(self, _: Event) -> list[str]:
        if not self.scenario_ids:
            return []
        greens, quarantined = len(self.greens), len(self.quarantined)
        pending = len(self.scenario_ids) - greens - quarantined
        return [
            f"Total: {calls_text(self.calls)}, {cost_text(self.cost_usd)}; {greens} en verde, "
            f"{quarantined} en cuarentena, {_pending_text(pending)}."
        ]


@dataclass(frozen=True)
class StatusView:
    """What `lictor status` puts together: the checkpoint, the journal and git's history."""

    run_id: str
    next_nodes: Sequence[str]
    values: Mapping[str, Any]  # the checkpoint's state
    progress: RunProgress  # the run's journal, folded
    by_hand: frozenset[str]  # greens whose commit lacks `Lictor-Run`
    max_attempts: int
    max_driver_attempts: int


_SCENARIO_NODES: Final[frozenset[str]] = frozenset({"implement", "verify", "commit", "pause"})
_DRIVER_NODES: Final[frozenset[str]] = frozenset({"build_driver", "red_check"})
_KINDS: Final[tuple[tuple[str, str, str], ...]] = (
    ("implemented", "implementado", "implementados"),
    ("collateral", "colateral", "colaterales"),
    ("reregistered", "registrado de nuevo", "registrados de nuevo"),
    ("previous", "de antes", "de antes"),
)


def _green_kind(view: StatusView, sid: str) -> str:
    progress = view.progress
    for kind, members in (
        ("reregistered", progress.reregistered),
        ("implemented", progress.implemented),
        ("collateral", progress.collateral),
        ("previous", progress.previous),
    ):
        if sid in members:
            return kind
    return ""


def _state_line(view: StatusView) -> list[str]:
    values = view.values
    if view.next_nodes:
        return [f"Estado: sin terminar; siguiente paso: {node_text(view.next_nodes[0])}."]
    code = values.get("exit_code")
    if code is None:
        return ["Estado: sin empezar."]
    reason = values.get("exit_reason")
    return [f"Estado: terminada con código {code}.", *([f"Motivo: {reason}"] if reason else [])]


def _attempt_line(view: StatusView) -> list[str]:
    """The step in progress, or the one a resume goes back to, with its attempt."""
    if not view.next_nodes:
        return []
    node, values = view.next_nodes[0], view.values
    if node == "pause":
        node = str(values.get("resume_to") or "")
    if node in _DRIVER_NODES:
        # The driver's call counts its attempt when it ends: the red check judges that one.
        done = int(values.get("driver_attempts") or 0)
        attempt = done + 1 if node == "build_driver" else max(done, 1)
        return [f"Driver: intento {attempt} de {view.max_driver_attempts}."]
    current = values.get("current")
    if node not in _SCENARIO_NODES or not current:
        return []
    done = int(values.get("attempt") or 0)
    attempt = done + 1 if node == "implement" else max(done, 1)
    return [f"Escenario en curso: {current}, intento {attempt} de {view.max_attempts}."]


def _greens_line(view: StatusView, status: Mapping[str, str]) -> str:
    greens = [sid for sid, st in status.items() if st == "green"]
    quarantined = sum(1 for st in status.values() if st == "quarantined")
    counts = {kind: 0 for kind, _, _ in _KINDS}
    for sid in greens:
        kind = _green_kind(view, sid)
        if kind:
            counts[kind] += 1
    parts = [
        f"{counts[kind]} {one if counts[kind] == 1 else many}"
        for kind, one, many in _KINDS
        if counts[kind]
    ]
    detail = f" ({', '.join(parts)})" if parts else ""
    pending = len(status) - len(greens) - quarantined
    return (
        f"Verdes: {len(greens)} de {len(status)}{detail}. En cuarentena: {quarantined}. "
        f"Pendientes: {pending}."
    )


def _spend_line(progress: RunProgress) -> str:
    driver = progress.spend(DRIVER_OWNER)
    split = (
        f" (driver: {calls_text(driver.calls)}, {cost_text(driver.cost_usd)})"
        if driver.calls
        else ""
    )
    return f"Agente: {calls_text(progress.calls)}, {cost_text(progress.cost_usd)}{split}."


_LABELS: Final[Mapping[str, str]] = {kind: one for kind, one, _ in _KINDS}


def _scenario_lines(view: StatusView, status: Mapping[str, str]) -> list[str]:
    if not status:
        return []
    order = [sid for sid in view.progress.scenario_ids if sid in status]
    order += [sid for sid in status if sid not in order]
    current = view.values.get("current") if view.next_nodes else None
    lines = ["Escenarios:"]
    for sid in order:
        st = status[sid]
        label = ""
        if st == "green":
            kind = _green_kind(view, sid)
            label = "registrado a mano" if sid in view.by_hand else _LABELS.get(kind, "")
        elif sid == current:
            label = "en curso"
        lines.append(f"  - {sid}: {status_text(st)}{f' ({label})' if label else ''}")
    return lines


def describe_status(view: StatusView) -> list[str]:
    """What `lictor status` says, in Spanish and never as Python would print it (F11)."""
    raw = view.values.get("status")
    status: Mapping[str, str] = raw if isinstance(raw, Mapping) else {}
    return [
        f"Ejecución: {view.run_id}",
        *_state_line(view),
        *_attempt_line(view),
        _greens_line(view, status),
        _spend_line(view.progress),
        *_scenario_lines(view, status),
    ]
