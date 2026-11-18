"""What a run has done so far, folded from its journal.

The same fold gives the lines `lictor run` prints as it works (F10) and the counts `lictor status`
shows (F11), so both say the same thing: what the journal recorded, never a guess.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from lictor.domain.model import Event
from lictor.domain.refactor import REFACTORED
from lictor.domain.results import (
    VERIFY_FINISHED,
    acceptance_from_record,
    recorded_controls,
    recorded_duration,
)

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
    "refactor": "refactorizar",
    "verify_refactor": "verificar el refactor",
    "commit_refactor": "confirmar el refactor",
    "quarantine": "poner en cuarentena",
    "pause": "pausa",
    "report": "escribir el informe",
}
REFACTOR_ROLE: Final[str] = "refactor"
_REFACTOR_NODES: Final[frozenset[str]] = frozenset(
    {"refactor", "verify_refactor", "commit_refactor"}
)
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
    # How each refactor ended (ADR-0010), by scenario: applied, discarded or unchanged.
    refactors: dict[str, str] = field(default_factory=dict)
    _spend: dict[str, Spend] = field(default_factory=dict)
    # What the refactors cost, by scenario: apart, so a green's line tells only its own work.
    _refactor_spend: dict[str, Spend] = field(default_factory=dict)
    _held: list[str] = field(default_factory=list)  # collateral greens not told yet

    def _all_spend(self) -> list[Spend]:
        return [*self._spend.values(), *self._refactor_spend.values()]

    @property
    def calls(self) -> int:
        return sum(spend.calls for spend in self._all_spend())

    @property
    def cost_usd(self) -> float:
        return sum(spend.cost_usd for spend in self._all_spend())

    def refactor_spend(self) -> Spend:
        """What every refactor of the run has cost, added up."""
        total = Spend()
        for spend in self._refactor_spend.values():
            total.calls += spend.calls
            total.cost_usd += spend.cost_usd
            total.agent_ms += spend.agent_ms
            total.verify_ms += spend.verify_ms
        return total

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
            VERIFY_FINISHED: self._verify_finished,
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
            REFACTORED: self._refactored,
            "lictor.run.finished.v1": self._run_finished,
        }

    def _owner(self, event: Event) -> Spend:
        owner = event.scenarioid or DRIVER_OWNER
        if event.data.get("role") == REFACTOR_ROLE:
            return self._refactor_spend.setdefault(owner, Spend())
        return self._spend.setdefault(owner, Spend())

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
        if event.data.get("role") == REFACTOR_ROLE:
            return [f"{event.scenarioid}: refactorizando sin cambiar el comportamiento…"]
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
            again = (
                "se repite"
                if event.data.get("role") == REFACTOR_ROLE
                else ("se repite sin gastar el intento")
            )
            lines.append(f"{owner}: la llamada al agente falló; {again}.")
        if event.data.get("signal") == "spec_conflict":
            lines.append(
                f"{owner}: el agente declara un conflicto con la especificación; su intento se "
                "verifica igualmente y, si pasa, queda en verde."
            )
        return lines

    def _verify_finished(self, event: Event) -> list[str]:
        controls = recorded_controls(event.data.get("controls"))
        acceptance = acceptance_from_record(event.data.get("acceptance"))
        self._owner(event).verify_ms += recorded_duration(controls, acceptance)
        # A refactor that fails tells it once, with `scenario.refactored`.
        if event.data.get("ok") or event.data.get("role") == REFACTOR_ROLE:
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

    def _refactored(self, event: Event) -> list[str]:
        sid = str(event.scenarioid)
        outcome = str(event.data.get("outcome", ""))
        self.refactors[sid] = outcome
        spend = self._refactor_spend.get(sid, Spend())
        took = f"{duration_text(spend.agent_ms + spend.verify_ms)}, {cost_text(spend.cost_usd)}"
        if outcome == "applied":
            return [f"{sid}: refactor aplicado, {took}."]
        if outcome == "unchanged":
            return [f"{sid}: refactor sin cambios, nada que mejorar, {took}."]
        failed_at = event.data.get("failed_at")
        where = f" en {failed_at}" if failed_at else ""
        return [f"{sid}: refactor descartado (falló{where}); el verde se queda, {took}."]

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


_SCENARIO_NODES: Final[frozenset[str]] = frozenset(
    {"implement", "verify", "commit", "pause", *_REFACTOR_NODES}
)
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


def _step(view: StatusView) -> str | None:
    """The step in progress, or the one a resume goes back to; None when the run isn't on one."""
    node = view.next_nodes[0] if view.next_nodes else None
    if node != "pause":
        return node
    resume = view.values.get("resume_to")
    return resume if isinstance(resume, str) else None


def _attempt_line(view: StatusView) -> list[str]:
    """The step in progress, or the one a resume goes back to, with its attempt."""
    node, values = _step(view), view.values
    if node in _DRIVER_NODES:
        # The driver's call counts its attempt when it ends: the red check judges that one.
        done = int(values.get("driver_attempts") or 0)
        attempt = done + 1 if node == "build_driver" else max(done, 1)
        return [f"Driver: intento {attempt} de {view.max_driver_attempts}."]
    current = values.get("current")
    if node not in _SCENARIO_NODES or not current:
        return []
    if node in _REFACTOR_NODES:
        return [f"Escenario en curso: {current}, ya en verde; su refactor, sin reintento."]
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
    parts = [
        f"{who}: {calls_text(spend.calls)}, {cost_text(spend.cost_usd)}"
        for who, spend in (
            ("driver", progress.spend(DRIVER_OWNER)),
            ("refactor", progress.refactor_spend()),
        )
        if spend.calls
    ]
    split = f" ({'; '.join(parts)})" if parts else ""
    return f"Agente: {calls_text(progress.calls)}, {cost_text(progress.cost_usd)}{split}."


_LABELS: Final[Mapping[str, str]] = {kind: one for kind, one, _ in _KINDS}


_NUMBERED: Final = re.compile(r"(.*?)(\d+)")
SHORTEST_RANGE: Final = 3  # two consecutive IDs read better as they are


def _numbered(sid: str) -> tuple[str, int, int] | None:
    found = _NUMBERED.fullmatch(sid)
    if found is None:
        return None
    return found.group(1), int(found.group(2)), len(found.group(2))


def _follows(previous: str, sid: str) -> bool:
    before, after = _numbered(previous), _numbered(sid)
    return (
        before is not None
        and after is not None
        and (before[0], before[2]) == (after[0], after[2])
        and after[1] == before[1] + 1
    )


def _id_order(sid: str) -> tuple[str, int, int]:
    """By prefix and number, so `A-024` goes before `A-028` wherever the feature puts it."""
    numbered = _numbered(sid)
    return (numbered[0], numbered[1], numbered[2]) if numbered else (sid, -1, 0)


def id_ranges(ids: Sequence[str]) -> str:
    """Write the IDs in order, three or more consecutive ones as a range: `A-003 a A-027`.

    The order is the IDs', not the feature's: a rule written last with the lowest IDs would
    otherwise split one range in three (F50).
    """
    runs: list[list[str]] = []
    for sid in sorted(ids, key=_id_order):
        if runs and _follows(runs[-1][-1], sid):
            runs[-1].append(sid)
        else:
            runs.append([sid])
    parts: list[str] = []
    for run in runs:
        parts.extend([f"{run[0]} a {run[-1]}"] if len(run) >= SHORTEST_RANGE else run)
    return ", ".join(parts)


def _scenario_lines(view: StatusView, status: Mapping[str, str]) -> list[str]:
    """Each scenario on its line, but the greens from before and those simply waiting.

    Those two say nothing about this run one by one, so each shares a line at the end, with its
    IDs as ranges (F38, F42).
    """
    if not status:
        return []
    order = [sid for sid in view.progress.scenario_ids if sid in status]
    order += [sid for sid in status if sid not in order]
    current = view.values.get("current") if view.next_nodes else None
    lines = ["Escenarios:"]
    previous: list[str] = []
    waiting: list[str] = []
    for sid in order:
        st = status[sid]
        label = ""
        if st == "green":
            kind = _green_kind(view, sid)
            if kind == "previous" and sid not in view.by_hand:
                previous.append(sid)
                continue
            label = "registrado a mano" if sid in view.by_hand else _LABELS.get(kind, "")
            if sid == current and _step(view) in _REFACTOR_NODES:
                label = f"{label}, refactor en curso" if label else "refactor en curso"
        elif sid == current:
            label = "en curso"
        elif st == "pending":
            waiting.append(sid)
            continue
        lines.append(f"  - {sid}: {status_text(st)}{f' ({label})' if label else ''}")
    if previous:
        many = "verdes" if len(previous) > 1 else status_text("green")
        lines.append(f"  - {id_ranges(previous)}: {many} (de antes)")
    if waiting:
        many = "pendientes" if len(waiting) > 1 else status_text("pending")
        lines.append(f"  - {id_ranges(waiting)}: {many}")
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
