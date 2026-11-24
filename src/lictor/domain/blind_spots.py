"""What a project needs before a run, and what its controls don't see (ADR-0015).

`missing_plugins` and `program_problems` stop a run that couldn't do anything: what fixes them is a
dependency, and only a person changes those. `blind_spots` only tells: tests no control runs, tests
or files the run loads that the agent can't change, and code nobody reads. Each of them may be a
decision of the project, so none stops anything, and what the person accepted in `lictor.json`
is only counted. The facts come from the profile's survey (`survey.py`); the rules of who may
write what are those of `L0-rutas`.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from lictor.domain.controls import ACCEPTANCE_ID
from lictor.domain.model import PreconditionProblem
from lictor.domain.rules import CHECK_STAGE_ONLY, control_label, forbidden_paths, path_within
from lictor.domain.survey import LoadedFile, ProjectImport, SurveyedTests, SurveyReport

SpotKind = Literal["sin_control", "solo_check", "sin_cambiar", "cargado", "sin_lector"]
ACCEPT_HINT = (
    "Si es una decisión del proyecto, apunta su ruta en blind_spots.accepted de lictor.json."
)


@dataclass(frozen=True)
class StartRules:
    """Who may write what, and when each command runs: what turns the survey's facts into spots.

    `check_only` are the ids of the controls only `lictor check` runs (ADR-0007); every other id,
    the acceptance included, a run verifies with.
    """

    writable: Sequence[str]
    driver: Sequence[str]
    forbidden: Sequence[str] = ()
    check_only: Collection[str] = ()
    accepted: Sequence[str] = ()


@dataclass(frozen=True)
class BlindSpot:
    """One kind of thing the controls don't see, with the files it is about and what to do."""

    kind: SpotKind
    paths: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class BlindSpots:
    """Every spot, what can't be known and why, and how many files the person accepted."""

    spots: tuple[BlindSpot, ...] = ()
    unknown: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    accepted: int = 0
    skipped: str | None = None
    lines: tuple[str, ...] = field(default=(), compare=False)


def _label(cid: str) -> str:
    return "de la aceptación" if cid == ACCEPTANCE_ID else control_label(cid)


def _names(ids: Sequence[str]) -> str:
    return ", ".join(f"'{cid}'" for cid in ids)


def missing_plugins(
    needs: Mapping[str, str], installed: Sequence[str]
) -> list[PreconditionProblem]:
    """The plugins the acceptance needs that the environment doesn't load, and how to add each."""
    return [
        PreconditionProblem(
            "config",
            f"La aceptación necesita el plugin {plugin}, que el entorno de verificación no "
            f"instala: añádelo al proyecto ({how}) y confirma los ficheros de dependencias que "
            "cambie. El agente no puede tocarlos.",
        )
        for plugin, how in needs.items()
        if plugin not in installed
    ]


def program_problems(
    report: SurveyReport, check_only: Collection[str]
) -> tuple[list[PreconditionProblem], list[str]]:
    """What the environment lacks of the programs its commands launch: problems, and warnings.

    A command launched through the environment runs what its lock fixes: a program only the
    machine has is not that one. Missing for a control only `lictor check` runs, it only warns.
    """
    problems: list[PreconditionProblem] = []
    warnings: list[str] = []
    for command in report.commands:
        program = command.program
        if program is None or program.in_env:
            continue
        message = (
            f"El comando {_label(command.id)} lanza '{program.name}' con el entorno de "
            "verificación, y el entorno no lo tiene"
        )
        if program.path is not None:
            message += (
                f": usaría el de la máquina ({program.path}), que no fija el lock, con otro "
                "intérprete y sin las dependencias del proyecto"
            )
        message += (
            ". Añade al proyecto la dependencia de desarrollo que lo instala y confirma los "
            "ficheros de dependencias, que el agente no puede tocar; o, si es de la máquina a "
            "propósito, escribe el comando sin lanzarlo con el entorno."
        )
        if command.id in check_only:
            warnings.append(f"{message} {CHECK_STAGE_ONLY}")
        else:
            problems.append(PreconditionProblem("config", message))
    return problems, warnings


def _agent_writes(path: str, rules: StartRules) -> bool:
    return path_within(path, rules.writable) and not forbidden_paths([path], rules.forbidden)


def _grouped(paths: Sequence[str]) -> str:
    """The files by directory, in order: `tests/integration/ (15)`, or the file when it's alone."""
    by_dir: dict[str, list[str]] = {}
    for path in paths:
        by_dir.setdefault(str(PurePosixPath(path).parent), []).append(path)
    parts = []
    for directory, files in by_dir.items():
        if len(files) == 1:
            parts.append(files[0])
        else:
            name = "" if directory == "." else f"{directory}/"
            parts.append(f"{name} ({len(files)})" if name else f"la raíz ({len(files)})")
    return ", ".join(parts)


def _imported(imports: Sequence[ProjectImport]) -> str:
    lines = sorted({i.line for i in imports})
    where = f"línea {lines[0]}" if len(lines) == 1 else f"líneas {', '.join(map(str, lines))}"
    names = [name for i in imports for name in (i.names or (i.module,))]
    return f"{where}: {', '.join(dict.fromkeys(names))}"


@dataclass
class _Spots:
    rules: StartRules
    accepted_paths: set[str] = field(default_factory=set)
    spots: list[BlindSpot] = field(default_factory=list)

    def keep(self, paths: Sequence[str]) -> list[str]:
        kept = []
        for path in paths:
            if path_within(path, self.rules.accepted):
                self.accepted_paths.add(path)
            else:
                kept.append(path)
        return kept

    def add(self, kind: SpotKind, paths: Sequence[str], message: str) -> None:
        if paths:
            self.spots.append(BlindSpot(kind, tuple(paths), message))


def _in_run(ids: Sequence[str], rules: StartRules) -> bool:
    """Whether a run verifies with any of these commands, not only `lictor check`."""
    return any(cid not in rules.check_only for cid in ids)


def _out_of_reach(path: str, rules: StartRules) -> bool:
    """A file the agent can't change and that isn't the oracle's: only a person fixes it."""
    return not path_within(path, rules.driver) and not _agent_writes(path, rules)


def _unrun_spots(tests: Sequence[SurveyedTests], found: _Spots) -> None:
    unrun = found.keep([t.path for t in tests if not t.run_by])
    count = sum(t.tests for t in tests if t.path in unrun)
    found.add(
        "sin_control",
        unrun,
        f"Tests que ningún control ejecuta: {_grouped(unrun)}, {count} tests. Un cambio de la "
        "API puede romperlos sin que se vea. Para que cuenten, amplía en lictor.json el run "
        f"del control que ejecuta los tests y añade su directorio a paths.writable. {ACCEPT_HINT}",
    )


def _check_only_spots(tests: Sequence[SurveyedTests], found: _Spots) -> None:
    later = found.keep([t.path for t in tests if t.run_by and not _in_run(t.run_by, found.rules)])
    found.add(
        "solo_check",
        later,
        f"Tests que solo ejecuta `lictor check`: {_grouped(later)}. El run no los ve, y el "
        "agente puede romperlos sin saberlo.",
    )


def _fixed_spots(tests: Sequence[SurveyedTests], found: _Spots) -> None:
    rules = found.rules
    fixed = found.keep(
        [t.path for t in tests if _in_run(t.run_by, rules) and _out_of_reach(t.path, rules)]
    )
    found.add(
        "sin_cambiar",
        fixed,
        f"Tests que el run ejecuta y el agente no puede cambiar: {_grouped(fixed)}. Si un "
        "escenario cambia la API que usan, el run parará a la espera de una persona. Añade su "
        f"directorio a paths.writable, o sácalos del run de ese control. {ACCEPT_HINT}",
    )


def _risky(loaded: LoadedFile, rules: StartRules) -> bool:
    """A file a run's tests load that imports the code and that only a person can change."""
    return bool(loaded.imports) and _in_run(loaded.by, rules) and _out_of_reach(loaded.path, rules)


def _run_ids(entries: Sequence[LoadedFile], rules: StartRules) -> list[str]:
    return list(dict.fromkeys(c for f in entries for c in f.by if c not in rules.check_only))


def _loaded_spots(report: SurveyReport, found: _Spots) -> None:
    candidates = [f for f in report.loaded if _risky(f, found.rules)]
    kept = set(found.keep([f.path for f in candidates]))
    entries = [f for f in candidates if f.path in kept]
    if not entries:
        return
    files = "; ".join(f"{f.path} ({_imported(f.imports)})" for f in entries)
    ids = _run_ids(entries, found.rules)
    found.add(
        "cargado",
        [f.path for f in entries],
        "Ficheros que cargan los tests del run, importan el código y el agente no puede cambiar: "
        f"{files}. Si un escenario cambia lo que importan, fallan {_names(ids)} y el run para a "
        "la espera de una persona. Mueve lo que necesitan junto a los tests que lo usan, en una "
        f"ruta que el agente pueda cambiar. {ACCEPT_HINT}",
    )


def _users_spots(report: SurveyReport, found: _Spots) -> None:
    driver = found.rules.driver
    unread = found.keep(
        [u.path for u in report.code_users if not u.read_by and not path_within(u.path, driver)]
    )
    found.add(
        "sin_lector",
        unread,
        f"Código que usa el proyecto y que ningún control lee: {_grouped(unread)}. Un cambio de "
        "la API puede romperlo sin que se vea: añade su directorio al run de los controles que "
        f"leen el código (los de estilo y tipos). {ACCEPT_HINT}",
    )


def _notes(report: SurveyReport) -> list[str]:
    """What goes with the spots: what a command selects, and what wasn't looked at."""
    notes = [
        f"El control {_names([c.id])} selecciona qué tests corre: lo que deja fuera no se cuenta."
        for c in report.commands
        if c.understood and c.tests and c.selects
    ]
    if report.unreadable:
        notes.append(f"No se pudieron leer, y no se miran: {', '.join(report.unreadable)}.")
    if report.projects:
        notes.append(f"Son otros proyectos, y no se miran: {', '.join(report.projects)}.")
    return notes


def blind_spots(report: SurveyReport, rules: StartRules) -> BlindSpots:
    """What the controls don't see of the project, as warnings that stop nothing.

    A command whose reach can't be known silences what it might see: a false warning teaches not
    to read the true ones.
    """
    if not report.modules:
        return BlindSpots(
            skipped=f"no comprobado: no hay código del proyecto en {report.code_dir}/"
        )
    unknown = [c for c in report.commands if not c.understood]
    found = _Spots(rules)
    tests = [t for t in report.tests if not path_within(t.path, rules.driver)]
    if not unknown:
        _unrun_spots(tests, found)
        _check_only_spots(tests, found)
    _fixed_spots(tests, found)
    _loaded_spots(report, found)
    if not unknown:
        _users_spots(report, found)
    unknown_lines = tuple(
        f"No se puede saber qué ejecuta el comando {_label(c.id)}: {c.reason}." for c in unknown
    )
    spots = tuple(found.spots)
    notes = tuple(_notes(report))
    accepted = len(found.accepted_paths)
    lines = _told(spots, unknown_lines, notes, accepted)
    return BlindSpots(spots, unknown_lines, notes, accepted, lines=lines)


def _told(
    spots: Sequence[BlindSpot], unknown: Sequence[str], notes: Sequence[str], accepted: int
) -> tuple[str, ...]:
    """Every line to tell, in order: the spots, what can't be known, the notes, the accepted."""
    told = [s.message for s in spots] + list(unknown)
    if told:
        told += notes
    if accepted:
        told.append(f"Aceptados en lictor.json (blind_spots.accepted): {accepted} ficheros.")
    return tuple(told)


def blind_spots_record(spots: BlindSpots | None) -> dict[str, object] | None:
    """What `run.started` keeps of the blind spots: None when they weren't looked at."""
    if spots is None:
        return None
    return {
        "skipped": spots.skipped,
        "spots": [{"kind": s.kind, "paths": list(s.paths)} for s in spots.spots],
        "lines": list(spots.lines),
        "accepted": spots.accepted,
    }


def blind_spot_lines(record: object) -> list[str] | None:
    """The lines a recorded blind spots told, or None if the run didn't look at them."""
    if not isinstance(record, Mapping):
        return None
    skipped = record.get("skipped")
    if isinstance(skipped, str):
        return [skipped]
    lines = record.get("lines")
    if not isinstance(lines, list):
        return None
    return [line for line in lines if isinstance(line, str)]
