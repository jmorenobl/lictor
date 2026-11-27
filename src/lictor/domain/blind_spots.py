"""What a project needs before a run, and what its controls don't see (ADR-0015).

`metadata_facts` tells whether the project's own metadata (its version, its entry points) is in
the verification, where the project isn't installed (F82, ADR-0019); it only warns, since code that
doesn't read it loses nothing. `missing_plugins` and `program_problems` stop a run that couldn't do
anything: what fixes them is a dependency, and only a person changes those;
`configuration_problems` too, for a configuration of the tests that makes a command run something
else, or fail with every test passed (F78).
`blind_spots` only tells: tests no control runs, tests or files the run loads that the agent can't
change, code nobody reads, and what the project's CI checks that no control does (F76). Each of
them may be a decision of the project, so none stops anything, and what the person accepted in
`lictor.json` is only counted. The facts come from the profile's survey (`survey.py`); the rules
of who may write what are those of `L0-rutas`.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from lictor.domain.ci import ci_entry_matches, is_ci_entry
from lictor.domain.controls import ACCEPTANCE_ID
from lictor.domain.model import PreconditionProblem
from lictor.domain.rules import CHECK_STAGE_ONLY, control_label, forbidden_paths, path_within
from lictor.domain.survey import (
    CoverageGate,
    LoadedFile,
    ProjectImport,
    ProjectMetadata,
    SurveyedCi,
    SurveyedCommand,
    SurveyedTests,
    SurveyReport,
)

SpotKind = Literal[
    "sin_control",
    "solo_check",
    "sin_cambiar",
    "cargado",
    "sin_lector",
    "ci_sin_control",
    "ci_mas_ficheros",
]
ACCEPT_HINT = (
    "Si es una decisión del proyecto, apunta su ruta en blind_spots.accepted de lictor.json."
)
CI_ACCEPT_HINT = (
    "Si es una decisión del proyecto, apunta «fichero: comando» en blind_spots.accepted de "
    "lictor.json."
)


@dataclass(frozen=True)
class StartRules:
    """Who may write what, and when each command runs: what turns the survey's facts into spots.

    `check_only` are the ids of the controls only `lictor check` runs (ADR-0007); every other id,
    the acceptance included, a run verifies with. `accepted` holds paths and, for a step of the
    CI, «file: command» entries.
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
    accepted_ci: int = 0
    lines: tuple[str, ...] = field(default=(), compare=False)


def _label(cid: str) -> str:
    return "de la aceptación" if cid == ACCEPTANCE_ID else control_label(cid)


def _names(ids: Sequence[str]) -> str:
    return ", ".join(f"'{cid}'" for cid in ids)


def metadata_facts(metadata: ProjectMetadata | None) -> tuple[str | None, tuple[str, ...]]:
    """What «Dependencias de la verificación» tells of the project's metadata: a ✓, or warnings.

    The first is the detail of a ✓ (None when there is nothing to tell); the second, what to warn.
    """
    if metadata is None:
        return None, ()
    if metadata.problem is not None:
        fix = f": {metadata.fix}" if metadata.fix else ""
        return None, (
            f"La metadata del proyecto no está en la verificación: {metadata.problem}. Si el "
            f"código lee la suya (su versión, sus entry points), ahí fallará o no encontrará "
            f"nada{fix}. Si no la lee, no pasa nada.",
        )
    named = f"{metadata.name} {metadata.version}"
    if metadata.unread:
        return None, (
            f"La metadata del proyecto ({named}) está en la verificación sin "
            f"{', '.join(metadata.unread)}: el proyecto los deja a su backend, y Lictor no los lee "
            "sin ejecutarlo.",
        )
    groups = ", ".join(f"{group} ({count})" for group, count in metadata.groups)
    return f"metadata del proyecto {named}" + (f", con {groups}" if groups else ""), ()


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


def accepted_paths(rules: StartRules) -> list[str]:
    """The paths the person accepted, without the entries of the CI."""
    return [entry for entry in rules.accepted if not is_ci_entry(entry)]


@dataclass
class _Spots:
    rules: StartRules
    accepted_paths: set[str] = field(default_factory=set)
    accepted_steps: set[str] = field(default_factory=set)
    spots: list[BlindSpot] = field(default_factory=list)

    def keep(self, paths: Sequence[str]) -> list[str]:
        kept = []
        roots = accepted_paths(self.rules)
        for path in paths:
            if path_within(path, roots):
                self.accepted_paths.add(path)
            else:
                kept.append(path)
        return kept

    def keep_steps(self, steps: Sequence[SurveyedCi]) -> list[SurveyedCi]:
        """The steps of the CI the person didn't accept by file and command."""
        kept = []
        for step in steps:
            if any(ci_entry_matches(e, step.file, step.run) for e in self.rules.accepted):
                self.accepted_steps.add(step.place())
            else:
                kept.append(step)
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


def _ci_runs(
    tests: Sequence[SurveyedTests], unrun: Collection[str], ci: Sequence[SurveyedCi]
) -> str:
    """Which steps of the CI run the tests no control runs, as a sentence; empty if none does."""
    indexes = sorted({i for t in tests if t.path in unrun for i in t.ci_run_by if i < len(ci)})
    if not indexes:
        return ""
    return f" La CI sí los ejecuta: {', '.join(_step(ci[i]) for i in indexes)}."


def _unrun_spots(
    tests: Sequence[SurveyedTests], found: _Spots, ci: Sequence[SurveyedCi] = ()
) -> None:
    unrun = found.keep([t.path for t in tests if not t.run_by])
    count = sum(t.tests for t in tests if t.path in unrun)
    found.add(
        "sin_control",
        unrun,
        f"Tests que ningún control ejecuta: {_grouped(unrun)}, {count} tests."
        f"{_ci_runs(tests, set(unrun), ci)} Un cambio de la API puede romperlos sin que se vea. "
        "Para que cuenten, amplía en lictor.json el run del control que ejecuta los tests y añade "
        f"su directorio a paths.writable. {ACCEPT_HINT}",
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


def _by_top(paths: Sequence[str]) -> str:
    """The files by their first directory: `tests/ (40), scripts/ (3), noxfile.py`."""
    tops: dict[str, int] = {}
    for path in paths:
        parts = PurePosixPath(path).parts
        top = f"{parts[0]}/" if len(parts) > 1 else path
        tops[top] = tops.get(top, 0) + 1
    return ", ".join(
        f"{top} ({count})" if top.endswith("/") else top for top, count in tops.items()
    )


def _step(step: SurveyedCi) -> str:
    return f"`{step.run}` ({step.place()})"


def _checks(report: SurveyReport, tool: str) -> list[SurveyedCommand]:
    return [c for c in report.commands if c.understood and c.tool == tool]


def _ci_spots(report: SurveyReport, found: _Spots) -> None:
    """What a check of the CI reaches that no control with its tool and subcommand does.

    What runs tests is left to `sin_control`, which names the steps that run them.
    """
    by_tool: dict[str, list[SurveyedCi]] = {}
    for step in found.keep_steps([s for s in report.ci if s.understood and not s.tests]):
        by_tool.setdefault(step.tool or "", []).append(step)
    by_tool.pop("", None)
    for tool, steps in by_tool.items():
        controls = _checks(report, tool)
        if not controls:
            _no_control(steps, found)
        for step in steps if controls else ():
            _wider(step, controls, found)


def _no_control(steps: Sequence[SurveyedCi], found: _Spots) -> None:
    places = ", ".join(_step(s) for s in steps)
    found.add(
        "ci_sin_control",
        [s.place() for s in steps],
        f"La CI comprueba {places} y ningún control lo hace: el run puede dejar un verde que la "
        f"CI rechace. Añade en lictor.json un control que lo ejecute. {CI_ACCEPT_HINT}",
    )


def _wider(step: SurveyedCi, controls: Sequence[SurveyedCommand], found: _Spots) -> None:
    read = {path for c in controls for path in c.covers}
    missing = found.keep([path for path in step.covers if path not in read])
    found.add(
        "ci_mas_ficheros",
        missing,
        f"La CI comprueba {_step(step)} en ficheros que no lee ningún control que haga lo mismo "
        f"({_names([c.id for c in controls])}): {_by_top(missing)}; {len(missing)} ficheros. "
        f"Amplía en lictor.json el run de ese control. {CI_ACCEPT_HINT}",
    )


def _ci_notes(report: SurveyReport) -> list[str]:
    """What of the CI couldn't be read or known: it is only named."""
    notes = [f"No se pudo leer la CI en {why}." for why in report.ci_unreadable]
    unknown = [s for s in report.ci if not s.understood]
    if unknown:
        steps = "; ".join(f"{_step(s)}: {s.reason}" for s in unknown)
        notes.append(f"No se puede saber qué comprueba la CI en {steps}.")
    return notes


def _unjudged(tests: Sequence[SurveyedTests], rules: StartRules) -> str:
    """How many files of tests the commands that can't be read leave without a judgment."""
    roots = accepted_paths(rules)
    left = [t for t in tests if not t.run_by and not path_within(t.path, roots)]
    if not left:
        return ""
    count = sum(t.tests for t in left)
    return (
        f"Por eso quedan sin juzgar {len(left)} ficheros de tests ({count} tests) que ningún otro "
        "comando ejecuta."
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
        _unrun_spots(tests, found, report.ci)
        _check_only_spots(tests, found)
    _fixed_spots(tests, found)
    _loaded_spots(report, found)
    if not unknown:
        _users_spots(report, found)
    _ci_spots(report, found)
    unknown_lines = _unknown_lines(unknown, tests, rules)
    spots = tuple(found.spots)
    notes = tuple(_notes(report))
    accepted, accepted_ci = len(found.accepted_paths), len(found.accepted_steps)
    told = _told(spots, unknown_lines, notes, _ci_notes(report))
    lines = told + _accepted_line(accepted, accepted_ci)
    return BlindSpots(
        spots, tuple(unknown_lines), notes, accepted, lines=lines, accepted_ci=accepted_ci
    )


def _unknown_lines(
    unknown: Sequence[SurveyedCommand], tests: Sequence[SurveyedTests], rules: StartRules
) -> list[str]:
    """What can't be known, and how many files of tests that leaves without a judgment."""
    lines = [
        f"No se puede saber qué ejecuta el comando {_label(c.id)}: {c.reason}." for c in unknown
    ]
    unjudged = _unjudged(tests, rules) if unknown else ""
    return [*lines, unjudged] if unjudged else lines


def _told(
    spots: Sequence[BlindSpot],
    unknown: Sequence[str],
    notes: Sequence[str],
    ci_notes: Sequence[str],
) -> tuple[str, ...]:
    """Every line to tell, in order: the spots, what can't be known, the notes, the CI's notes."""
    told = [s.message for s in spots] + list(unknown)
    if told:
        told += notes
    return (*told, *ci_notes)


def _accepted_line(files: int, steps: int) -> tuple[str, ...]:
    parts = [f"{files} ficheros"] if files else []
    parts += [f"{steps} comandos de la CI"] if steps else []
    if not parts:
        return ()
    return (f"Aceptados en lictor.json (blind_spots.accepted): {' y '.join(parts)}.",)


def _labels(ids: Sequence[str]) -> str:
    """`'unit' y la aceptación`: who a problem of the configuration reaches."""
    labels = ["la aceptación" if cid == ACCEPTANCE_ID else f"'{cid}'" for cid in ids]
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} y {labels[-1]}"


def _eaten(command: SurveyedCommand) -> PreconditionProblem:
    return PreconditionProblem(
        "config",
        f"El comando {_label(command.id)} nombra {command.eaten}, pero el `--cov` con que acaba "
        f"addopts en {command.config} lo toma como su valor, y lo que se ejecuta es "
        f"{', '.join(command.paths)}. Escribe `--cov=<paquete>` en addopts, con el paquete que "
        "mide; el agente no puede tocar esa configuración.",
    )


def configuration_problems(report: SurveyReport) -> list[PreconditionProblem]:
    """What the configuration of the tests does to the commands that run them (F78).

    A `--cov` at the end of `addopts` takes the command's first path as its value, so the command
    runs something else; and a `--cov` there with a minimum makes any part of the tests fail with
    every test passed, which is what the acceptance runs. Both are the project's to fix.
    """
    commands = [c for c in report.commands if c.understood]
    problems = [_eaten(c) for c in commands if c.eaten is not None]
    gates: dict[CoverageGate, list[str]] = {}
    for command in commands:
        if command.gate is not None:
            gates.setdefault(command.gate, []).append(command.id)
    problems += [
        PreconditionProblem(
            "config",
            f"addopts en {gate.addopts} mide la cobertura en todo comando de tests ({gate.cov}) "
            f"y {gate.minimum()}: un comando que ejecuta solo "
            "una parte de los tests sale con un fallo aunque pasen todos, y la aceptación nunca "
            f"podría pasar. Afecta a {_labels(ids)}. Saca {gate.cov} de addopts y ponlo en la "
            "orden de la CI que mide la cobertura; el agente no puede tocar esa configuración.",
        )
        for gate, ids in gates.items()
    ]
    return problems


def blind_spots_record(spots: BlindSpots | None) -> dict[str, object] | None:
    """What `run.started` keeps of the blind spots: None when they weren't looked at."""
    if spots is None:
        return None
    return {
        "skipped": spots.skipped,
        "spots": [{"kind": s.kind, "paths": list(s.paths)} for s in spots.spots],
        "lines": list(spots.lines),
        "accepted": spots.accepted,
        "accepted_ci": spots.accepted_ci,
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
