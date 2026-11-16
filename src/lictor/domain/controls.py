"""A verification control as the domain sees it, and how one control reads another's run.

The configuration resolves to these, in one conversion (ADR-0009, point 9); the run and
`lictor check` get them in the order they were declared. A control either runs a command
(`CommandControl`) or reads the run of another (`ReaderControl`, ADR-0008): coverage measured on
the run of the unit tests, which then runs once. The rules of that pairing live here: which
controls a reader may read, what command the read control runs, what `lictor check` adds to a
selection, and the steps a verification follows.
"""

from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal

from lictor.domain.errors import ConfigError
from lictor.domain.reports import ReportFormat
from lictor.domain.rules import missing_plugin
from lictor.domain.stages import ATTEMPT, CHECK, Stage
from lictor.domain.verdicts import VerdictRule

ControlMode = Literal["block", "measure"]
ACCEPTANCE_ID = "acceptance"
_ACCEPTANCE_LEVELS = frozenset({"L3", "ACCEPTANCE"})


@dataclass(frozen=True)
class CommandControl:
    """A control that runs a command and is judged by what that command left.

    `when_exists` skips it when that path is in neither HEAD nor what is verified; `when_plugin`,
    when the environment doesn't load that plugin. `min_score` only means something for a report
    with a score.
    """

    id: str
    level: str
    run: str
    report: ReportFormat
    mode: ControlMode = "block"
    stage: Stage = ATTEMPT
    empty_exit_codes: tuple[int, ...] = ()
    timeout_s: int = 600
    when_exists: str | None = None
    when_plugin: str | None = None
    min_score: float | None = None

    @property
    def verdict_rule(self) -> VerdictRule:
        """What of this control decides its verdict."""
        return VerdictRule(self.report, self.timeout_s, self.empty_exit_codes)


@dataclass(frozen=True)
class ReaderControl:
    """A control that runs nothing: it scores the coverage the run of `reads` measured.

    `adds` are the arguments that control's command gets at its end while this reader runs where
    it is verified; its `{out}` is this reader's report. It has nothing of a run of its own: no
    timeout, no exit codes and no path, which are all its read control's.
    """

    id: str
    level: str
    reads: str
    adds: str
    report: Literal["coverage"] = "coverage"
    mode: ControlMode = "block"
    stage: Stage = ATTEMPT
    when_plugin: str | None = None
    min_score: float | None = None


type Control = CommandControl | ReaderControl


def controls_of_stage[C: CommandControl | ReaderControl](
    controls: Sequence[C], stage: Stage
) -> tuple[C, ...]:
    """The controls of one stage, in the order they were declared (ADR-0007)."""
    return tuple(control for control in controls if control.stage == stage)


def command_controls(controls: Sequence[Control]) -> tuple[CommandControl, ...]:
    """The controls that run a command of their own: only theirs need a program to start."""
    return tuple(control for control in controls if isinstance(control, CommandControl))


def reader_of(control_id: str, controls: Sequence[Control]) -> ReaderControl | None:
    """The reader among `controls` that reads the run of `control_id`, if any."""
    return next(
        (c for c in controls if isinstance(c, ReaderControl) and c.reads == control_id), None
    )


# The rules of the pairing, which `LictorConfig` and `Profile` check whoever builds them.


def reader_problems(controls: Sequence[Control]) -> list[str]:
    """Say what is wrong with how the readers among `controls` read: empty if nothing is.

    A reader reads an active control that runs a JUnit suite, after it, never alone in a stage
    where that control doesn't run; a control has one reader at most.
    """
    problems: list[str] = []
    place = {control.id: i for i, control in enumerate(controls)}
    readers = [c for c in controls if isinstance(c, ReaderControl)]
    for reader in readers:
        read = next((c for c in controls if c.id == reader.reads), None)
        if read is None:
            problems.append(_orphan(reader))
        elif isinstance(read, ReaderControl):
            problems.append(_reads_a_reader(reader, read))
        elif read.report != "junit":
            problems.append(_reads_no_junit(reader, read))
        elif place[read.id] > place[reader.id]:
            problems.append(_reads_ahead(reader, read))
        elif reader.stage == ATTEMPT and read.stage == CHECK:
            problems.append(_reads_where_it_doesnt_run(reader, read))
    counts = Counter(reader.reads for reader in readers)
    for read_id, count in counts.items():
        if count > 1:
            names = [f"'{r.id}'" for r in readers if r.reads == read_id]
            problems.append(
                f"El control '{read_id}' tiene {count} lectores ({', '.join(names)}): la "
                "ejecución de un control la lee como mucho uno. Quita los que sobren o dales un "
                "run propio."
            )
    return problems


def _orphan(reader: ReaderControl) -> str:
    return (
        f"El control '{reader.id}' lee la ejecución de '{reader.reads}', que no está entre los "
        f"controles activos: quita también '{reader.id}' "
        f'({{"id": "{reader.id}", "enabled": false}}) o dale un run propio.'
    )


def _reads_a_reader(reader: ReaderControl, read: ReaderControl) -> str:
    return (
        f"El control '{reader.id}' lee la ejecución de '{read.id}', que también es un lector y "
        "no ejecuta nada: solo se lee la ejecución de un control con run y report 'junit'."
    )


def _reads_no_junit(reader: ReaderControl, read: CommandControl) -> str:
    return (
        f"El control '{reader.id}' lee la ejecución de '{read.id}', cuyo report es "
        f"'{read.report}': un lector de cobertura solo lee un control con report 'junit'."
    )


def _reads_ahead(reader: ReaderControl, read: CommandControl) -> str:
    return (
        f"El control '{reader.id}' va antes que '{read.id}', cuya ejecución lee: escríbelo "
        f"detrás de '{read.id}' en controls."
    )


def _reads_where_it_doesnt_run(reader: ReaderControl, read: CommandControl) -> str:
    return (
        f"El control '{reader.id}' corre en cada intento y lee la ejecución de '{read.id}', que "
        f"solo corre en lictor check: pon '{reader.id}' también en check "
        f'({{"id": "{reader.id}", "stage": "check"}}), quítalo o dale un run propio.'
    )


# What command the read control runs, which depends only on the configuration, on where it is
# verified and on the reader's plugin: never on what `--level` or `--control` choose.


@dataclass(frozen=True)
class CommandPart:
    """A piece of a command as it is configured, and the control whose report its `{out}` is."""

    text: str
    owner: str


@dataclass(frozen=True)
class EffectiveCommand:
    """What a control runs: its `run`, and the `adds` of its reader when that reader runs too."""

    parts: tuple[CommandPart, ...]
    reader: ReaderControl | None = None

    @property
    def configured(self) -> str:
        """The command as configured, each `{out}` where a report goes, for a person to read."""
        return " ".join(part.text for part in self.parts)


def effective_command(
    control: CommandControl, verified: Sequence[Control], installed: Collection[str]
) -> EffectiveCommand:
    """The command `control` runs where `verified` are the controls that verification runs.

    In a run those are the `attempt` controls; in `lictor check`, all of them, whatever was
    selected. The reader's `adds` join only while the reader runs there and its plugin is loaded:
    without it the arguments would break the read control's own run.
    """
    own = CommandPart(control.run, control.id)
    reader = reader_of(control.id, verified)
    if reader is None or missing_plugin(reader.when_plugin, installed) is not None:
        return EffectiveCommand((own,))
    return EffectiveCommand((own, CommandPart(reader.adds, reader.id)), reader)


def executed_as(control: CommandControl, command: EffectiveCommand) -> str | None:
    """What the agent reads about a failed control whose command isn't its `run`, or None.

    Its own commands don't measure coverage: naming the command lets it reproduce a test that
    only fails under it, instead of spending attempts with one that passes.
    """
    if command.reader is None:
        return None
    return f"{control.id} se ejecutó como: {command.configured}"


def lent_by_check(command: EffectiveCommand) -> str | None:
    """How `lictor check` marks a control that ran with the arguments of a `check` reader.

    Its run in each attempt didn't have them, so a failure here may be one the run never saw.
    """
    reader = command.reader
    if reader is None or reader.stage != CHECK:
        return None
    return f" (con los argumentos de {reader.id}, que solo corre en check)"


# The steps a verification follows: what each control does at its turn.


@dataclass(frozen=True)
class RunStep:
    """Run a control's command; `read_by` is the reader whose report is read once it ends.

    `missing` is the control's own plugin the environment lacks: then it is skipped.
    """

    control: CommandControl
    command: EffectiveCommand
    missing: str | None = None
    read_by: ReaderControl | None = None


@dataclass(frozen=True)
class ReadStep:
    """Give a reader its verdict over what its read control left, or skip it without its plugin."""

    control: ReaderControl
    missing: str | None = None


type Step = RunStep | ReadStep


def verification_steps(
    selected: Sequence[Control], verified: Sequence[Control], installed: Collection[str]
) -> tuple[Step, ...]:
    """The steps for the `selected` controls of a verification that runs `verified`.

    The plugin of each reader is decided once, here, for both the command of its read control and
    its own turn, so they can't contradict each other. A reader's report is only read when the
    reader is selected: `lictor check --control unit` runs the same command and reads nothing.
    """
    selected_ids = {control.id for control in selected}
    steps: list[Step] = []
    for control in selected:
        missing = missing_plugin(control.when_plugin, installed)
        if isinstance(control, ReaderControl):
            steps.append(ReadStep(control, missing))
            continue
        command = effective_command(control, verified, installed)
        reader = command.reader
        read_by = reader if reader is not None and reader.id in selected_ids else None
        steps.append(RunStep(control, command, missing, read_by))
    return tuple(steps)


# What `lictor check` runs of what was asked.


@dataclass(frozen=True)
class Added:
    """A control `lictor check` runs although it wasn't selected: a selected reader reads it."""

    control: CommandControl
    reader: ReaderControl

    @property
    def sentence(self) -> str:
        return f"Se ejecuta también {self.control.id}, cuya ejecución lee {self.reader.id}"


@dataclass(frozen=True)
class CheckSelection:
    """The controls `lictor check` runs, in the order of the configuration, and the acceptance."""

    controls: tuple[Control, ...]
    acceptance: bool
    added: tuple[Added, ...] = ()


def check_selection(
    controls: Sequence[Control],
    levels: Sequence[str] | None,
    control_ids: Sequence[str] | None,
    installed: Collection[str],
) -> CheckSelection:
    """Apply `--level` and `--control` together, and complete the selection in one direction.

    A selected reader brings the control whose run it reads, unless the reader is going to be
    skipped for its plugin: then there is nothing to read. A selected read control brings
    nothing: it runs the same command anyway. An id that names nothing is refused.
    """
    wanted_levels = {lvl.strip().upper() for lvl in levels} if levels is not None else None
    ids = set(control_ids) if control_ids is not None else None
    if ids is not None:
        known = {c.id for c in controls} | {ACCEPTANCE_ID}
        unknown = sorted(ids - known)
        if unknown:
            raise ConfigError(
                f"No hay ningún control {', '.join(unknown)}. "
                f"Los controles son: {', '.join(sorted(known))}."
            )
    chosen = {
        c.id
        for c in controls
        if (wanted_levels is None or c.level.strip().upper() in wanted_levels)
        and (ids is None or c.id in ids)
    }
    by_id = {c.id: c for c in controls}
    added: dict[str, Added] = {}
    for control in controls:
        if not isinstance(control, ReaderControl) or control.id not in chosen:
            continue
        if control.reads in chosen or missing_plugin(control.when_plugin, installed) is not None:
            continue
        read = by_id.get(control.reads)
        if isinstance(read, CommandControl):
            added[read.id] = Added(read, control)
    selected = chosen | set(added)
    acceptance = bool(
        (wanted_levels is None or _ACCEPTANCE_LEVELS & wanted_levels)
        and (ids is None or ACCEPTANCE_ID in ids)
    )
    return CheckSelection(
        tuple(c for c in controls if c.id in selected),
        acceptance,
        tuple(added[c.id] for c in controls if c.id in added),
    )
