"""A verification control as the domain sees it, and how one control reads another's run.

The configuration resolves to these, in one conversion (ADR-0009, point 9); the run and
`lictor check` get them in the order they were declared. A control either runs a command
(`CommandControl`) or reads the run of another (`ReaderControl`, ADR-0008): coverage measured on
the run of the unit tests, which then runs once. The rules of that pairing live here: which
controls a reader may read, what command the read control runs, what `lictor check` adds to a
selection, and the steps a verification follows.
"""

import shlex
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from lictor.domain.errors import ConfigError
from lictor.domain.reports import ReportFormat
from lictor.domain.rules import minimum_text, missing_plugin
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
    # How the agent measures this control by itself: verification never runs it (F55).
    agent_run: str | None = None
    # How the agent leaves done what this control checks, while it blocks every attempt (F83).
    agent_fix: str | None = None
    # The complexity no function may exceed; only, and always, with a complexity report.
    limit: int | None = None

    def __post_init__(self) -> None:
        if (self.report == "complexity") != (self.limit is not None):
            raise ConfigError(complexity_limit_message(self.id, self.report))

    @property
    def verdict_rule(self) -> VerdictRule:
        """What of this control decides its verdict."""
        return VerdictRule(self.report, self.timeout_s, self.empty_exit_codes, self.limit)


def complexity_limit_message(control_id: str, report: str) -> str:
    """What a control is told when `limit` and a complexity report don't come together."""
    if report == "complexity":
        return (
            f"El control '{control_id}' con report='complexity' necesita limit: la complejidad "
            "que ninguna función puede superar"
        )
    return f"El control '{control_id}' declara limit, que solo vale con report='complexity'"


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
    agent_run: str | None = None


type Control = CommandControl | ReaderControl


# The agent's commands (F83, ADR-0018): they come from the controls, never from a list of their own,
# which went out of date with every control a project changed.

REPORT_PLACEHOLDER: Final = "{out}"


def allowed_by(command: str, agent_tools: Sequence[str]) -> bool:
    """Tell whether the agent's tools let it run `command`, as the backend decides.

    A rule `Bash(<prefix>:*)` allows what starts with that prefix and `Bash(<command>)` that exact
    command. The command is judged as written, `{lictor_files}` included, as its rule is.
    """
    for tool in agent_tools:
        if not (tool.startswith("Bash(") and tool.endswith(")")):
            continue
        rule = tool[len("Bash(") : -1]
        if rule.endswith(":*") and command.startswith(rule[: -len(":*")]):
            return True
        if command == rule:
            return True
    return False


def bash_prefixes(agent_tools: Sequence[str]) -> list[str]:
    """What the agent's Bash accepts, as it reads it: each rule's prefix or exact command."""
    found = [
        tool[len("Bash(") : -1].removesuffix(":*")
        for tool in agent_tools
        if tool.startswith("Bash(") and tool.endswith(")")
    ]
    return list(dict.fromkeys(found))


def counts_for_agent(control: Control, installed: Collection[str]) -> bool:
    """Tell whether the agent should check, or fix, what `control` decides before it ends.

    Only what blocks every attempt: a control that measures decides nothing, one of `check` never
    runs in a run, and one whose plugin the environment lacks is skipped.
    """
    return (
        control.mode == "block"
        and control.stage == ATTEMPT
        and missing_plugin(control.when_plugin, installed) is None
    )


def without_report(run: str) -> str | None:
    """A command with its report taken out, as the agent runs it, or None if it can't be.

    The token holding `{out}` goes and, when it is exactly `{out}` after an option, the option
    too: `--junitxml {out}`, `--out {out}`. One alone, a positional argument, can't go without
    changing the command. Known limit: an option without value right before `{out}` goes too.
    """
    if REPORT_PLACEHOLDER not in run:
        return run  # as the person wrote it, quotes and all
    tokens = shlex.split(run)
    kept: list[str] = []
    for token in tokens:
        if REPORT_PLACEHOLDER not in token:
            kept.append(token)
        elif token != REPORT_PLACEHOLDER:
            continue
        elif kept and kept[-1].startswith("-"):
            kept.pop()
        else:
            return None
    return shlex.join(kept)


@dataclass(frozen=True)
class AgentFix:
    """A command with which the agent leaves done what a blocking control checks."""

    control_id: str
    command: str


@dataclass(frozen=True)
class AgentCommands:
    """What the agent runs before it ends: the fixes first, then what checks its work."""

    fixes: tuple[AgentFix, ...] = ()
    checks: tuple[str, ...] = ()

    @property
    def all(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*(f.command for f in self.fixes), *self.checks]))


def _check_of(control: Control) -> str | None:
    """How the agent checks one control: its `agent_run`, or its `run` without the report.

    A reader runs nothing of its own, and a read control's command is its `run`, never with the
    reader's `adds`: measuring has its own command, and the agent would leave reports in its copy.
    """
    if control.agent_run is not None:
        return control.agent_run
    if isinstance(control, ReaderControl):
        return None
    return without_report(control.run)


def agent_commands(
    controls: Sequence[Control],
    acceptance_run: str,
    agent_tools: Sequence[str],
    installed: Collection[str],
) -> AgentCommands:
    """The agent's commands, from the controls the run verifies and its acceptance (F83).

    A control that counts gives its fix, if it has one, and its check: with both, a fix that
    doesn't fix what the control reads shows before the verification. Only what the agent's tools
    allow stays, each once.
    """
    counting = [c for c in controls if counts_for_agent(c, installed)]
    return AgentCommands(
        _fixes(counting, agent_tools), _checks(counting, acceptance_run, agent_tools)
    )


def _fixes(counting: Sequence[Control], agent_tools: Sequence[str]) -> tuple[AgentFix, ...]:
    return tuple(
        AgentFix(c.id, c.agent_fix)
        for c in command_controls(counting)
        if c.agent_fix is not None and allowed_by(c.agent_fix, agent_tools)
    )


def _checks(
    counting: Sequence[Control], acceptance_run: str, agent_tools: Sequence[str]
) -> tuple[str, ...]:
    found = [_check_of(c) for c in counting]
    found.append(without_report(acceptance_run) if acceptance_run else None)
    return tuple(dict.fromkeys(c for c in found if c is not None and allowed_by(c, agent_tools)))


def fixes_text(fixes: Sequence[AgentFix]) -> str:
    """The line of the prompt on the fixes: run them after the last change, then the checks."""
    if not fixes:
        return ""
    named = " y ".join(f"`{f.command}` (el control {f.control_id} bloquea)" for f in fixes)
    return f"- Tras tu último cambio, ejecuta {named} y después las comprobaciones."


def commands_list(commands: Sequence[str]) -> str:
    """Commands as a prompt lists them: one per line, between backticks."""
    return "\n".join(f"  - `{command}`" for command in commands) or "  - los tests del proyecto"


def runnable_fixes(fixes: Sequence[AgentFix], files_dir: str | None) -> list[AgentFix]:
    """The fixes the agent can run in this call, as it runs them."""
    return [
        AgentFix(f.control_id, command)
        for f in fixes
        if (command := runnable_command(f.command, files_dir)) is not None
    ]


def agent_command_lines(
    agent_tools: Sequence[str],
    checks: Sequence[str],
    fixes: Sequence[AgentFix],
    files_dir: str | None,
    extra: Sequence[str] = (),
) -> dict[str, str]:
    """The placeholders of a prompt on the agent's commands, as this call can run them (F83).

    `extra` are the call's own commands, already runnable: those that measure. What Bash admits
    comes from the tools, by prefix: not from the commands it checks with, which are only some of
    what it may run.
    """
    tools = agent_tools_with_files(agent_tools, files_dir)
    runnable = [c for raw in checks if (c := runnable_command(raw, files_dir)) is not None]
    admitted = ", ".join(f"`{prefix}`" for prefix in bash_prefixes(tools))
    return {
        "{bash_admite}": admitted or "ninguna",
        "{comprobaciones}": commands_list(list(dict.fromkeys([*runnable, *extra]))),
        "{arreglos}": fixes_text(runnable_fixes(fixes, files_dir)),
    }


@dataclass(frozen=True)
class AgentMeasure:
    """A minimum the verification demands, and the command with which the agent can check it."""

    control_id: str
    command: str
    min_score: float


def agent_measures(
    controls: Sequence[Control], installed: Collection[str]
) -> tuple[AgentMeasure, ...]:
    """What the implementer is told to measure by itself before it ends (F55).

    Only a control that declares a minimum and an `agent_run`, and whose plugin the environment
    loads: a skipped control demands nothing, and its command would fail in the agent's copy.
    `controls` are the ones the run verifies, so the `check` ones never reach the agent.
    """
    return tuple(
        AgentMeasure(control.id, control.agent_run, control.min_score)
        for control in controls
        if control.agent_run
        and control.min_score is not None
        and missing_plugin(control.when_plugin, installed) is None
    )


def agent_measures_text(measures: Sequence[AgentMeasure]) -> str:
    """The lines of the implementer's prompt that name each minimum and how to measure it."""
    if not measures:
        return ""
    lines = [
        "- La verificación también exige una puntuación mínima, medida igual que con estas "
        "órdenes. Mídela antes de terminar y, si no llega, añade tests:"
    ]
    for measure in measures:
        lines.append(
            f"  - {measure.control_id}: al menos {minimum_text(measure.min_score)} %, "
            f"con `{measure.command}`."
        )
    return "\n".join(lines)


def refactor_measures(
    controls: Sequence[Control], installed: Collection[str]
) -> tuple[tuple[str, str], ...]:
    """The id and `agent_run` of every control the refactor can measure by itself (ADR-0010).

    A refactor must not measure less than its green, with or without a minimum, so every
    `agent_run` whose plugin the environment loads counts, not only those with a `min_score`.
    A control that measures the code has its own lines (`refactor_complexity_text`,
    `refactor_duplication_text`): what it measures isn't what is left untested.
    """
    return tuple(
        (control.id, control.agent_run)
        for control in controls
        if control.agent_run
        and control.report not in CODE_REPORTS
        and missing_plugin(control.when_plugin, installed) is None
    )


def refactor_measures_text(
    measures: Sequence[tuple[str, str]], left: Mapping[str, Mapping[str, int]] | None = None
) -> str:
    """The lines of the refactor's prompt that say how to check it leaves no more untested.

    `left` is what the green left untested, by control and kind (`score_misses`): with it the
    refactor can tell a rise from one measure after editing (F71), as with `in_clones`.
    """
    if not measures:
        return ""
    lines = [
        "- La verificación compara lo que estos controles dejan sin probar con lo que dejaban en "
        "el verde, y descarta el refactor si deja más. Quitar código que ya estaba probado no "
        "cuenta. Mídelo antes y después de cambiar nada:"
    ]
    counted = False
    for control_id, command in measures:
        counts = (left or {}).get(control_id)
        green = _green_left(counts) if counts else ""
        counted = counted or bool(green)
        lines.append(f"  - {control_id}: `{command}`.{green}")
    if counted:
        lines.append(
            "  Si sube, es código que el verde no cubría y que has partido o movido (cada función "
            "nueva es una línea más sin cubrir): cúbrelo con un test unitario o no lo partas."
        )
    return "\n".join(lines)


def _green_left(counts: Mapping[str, int]) -> str:
    """What the green left untested, in the words of the refactor's prompt."""
    if "lines" in counts or "branches" in counts:
        return (
            f" El verde dejaba {counts.get('lines', 0)} líneas y {counts.get('branches', 0)} "
            "ramas sin cubrir: el refactor no puede dejar más."
        )
    if "mutants" in counts:
        return (
            f" El verde dejaba {counts['mutants']} mutantes sin matar: el refactor no puede "
            "dejar más."
        )
    return ""


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
    """What a control runs: its `run`, and the `adds` of the reader that lends them, if any.

    The parts derive from the two controls, so a command can't carry arguments of no reader.
    """

    control: CommandControl
    reader: ReaderControl | None = None

    @property
    def parts(self) -> tuple[CommandPart, ...]:
        own = CommandPart(self.control.run, self.control.id)
        if self.reader is None:
            return (own,)
        return (own, CommandPart(self.reader.adds, self.reader.id))

    @property
    def configured(self) -> str:
        """The command as configured, each `{out}` where a report goes, for a person to read."""
        return " ".join(part.text for part in self.parts)


# The plugin each control lacks, by id: decided once per verification for every use of it.
type Missing = Mapping[str, str | None]


def missing_plugins(
    controls: Sequence[Control], installed: Collection[str]
) -> dict[str, str | None]:
    """The plugin each control needs and the environment lacks, or None, by its id."""
    return {c.id: missing_plugin(c.when_plugin, installed) for c in controls}


def effective_command(
    control: CommandControl, verified: Sequence[Control], installed: Collection[str]
) -> EffectiveCommand:
    """The command `control` runs where `verified` are the controls that verification runs.

    In a run those are the `attempt` controls; in `lictor check`, all of them, whatever was
    selected. The reader's `adds` join only while the reader runs there and its plugin is loaded:
    without it the arguments would break the read control's own run.
    """
    return _effective(control, verified, missing_plugins(verified, installed))


def _effective(
    control: CommandControl, verified: Sequence[Control], missing: Missing
) -> EffectiveCommand:
    reader = reader_of(control.id, verified)
    if reader is None or missing.get(reader.id) is not None:
        return EffectiveCommand(control)
    return EffectiveCommand(control, reader)


REPORT_PATHS_NOTE = (
    "Cada {out} es la ruta donde Lictor deja un informe, fuera de tu copia: para reproducirlo, "
    "pon en cada uno una ruta distinta fuera del proyecto."
)


def executed_as(command: EffectiveCommand, ran: bool) -> str | None:
    """What the agent reads about a failed control whose command isn't its `run`, or None.

    Its own commands don't measure coverage: naming the command lets it reproduce a test that
    only fails under it, instead of spending attempts with one that passes. A control that never
    ran, skipped or unable to run, ran no command to name.
    """
    if command.reader is None or not ran:
        return None
    return f"{command.control.id} se ejecutó como: {command.configured}\n{REPORT_PATHS_NOTE}"


def lent_by_check(command: EffectiveCommand) -> str | None:
    """How `lictor check` marks a control that ran with the arguments of a `check` reader.

    Its run in each attempt didn't have them, so a failure here may be one the run never saw.
    The caller only asks for a control that ran: a skipped one ran with nothing.
    """
    reader = command.reader
    if reader is None or reader.stage != CHECK:
        return None
    return f" (con los argumentos de {reader.id}, que solo corre en check)"


# The steps a verification follows: what each control does at its turn.


@dataclass(frozen=True)
class RunStep:
    """Run a control's command, and read its reader's report once it ends if `reads` says so.

    `missing` is the control's own plugin the environment lacks: then it is skipped. The reader
    whose report is read can only be the one lending its arguments (`read_by`).
    """

    command: EffectiveCommand
    missing: str | None = None
    reads: bool = False

    @property
    def control(self) -> CommandControl:
        return self.command.control

    @property
    def read_by(self) -> ReaderControl | None:
        return self.command.reader if self.reads else None


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

    The plugin of each control is decided once, here, and the same decision gives both the
    command of a read control and its reader's own turn, so they can't contradict each other. A
    reader's report is only read when the reader is selected: `lictor check --control unit` runs
    the same command and reads nothing.
    """
    missing = missing_plugins(verified, installed)
    selected_ids = {control.id for control in selected}
    steps: list[Step] = []
    for control in selected:
        if isinstance(control, ReaderControl):
            steps.append(ReadStep(control, missing.get(control.id)))
            continue
        command = _effective(control, verified, missing)
        reads = command.reader is not None and command.reader.id in selected_ids
        steps.append(RunStep(command, missing.get(control.id), reads))
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


# Where the files of the profile are copied for an agent call, outside the project and the copy
# (ADR-0013): an `agent_run` and a rule of `agent_tools` may name it.
AGENT_FILES = "{lictor_files}"


def with_agent_files(text: str, files_dir: str) -> str:
    """A command or a rule with the directory of the profile's files written in, quoted once."""
    return text.replace(AGENT_FILES, shlex.quote(files_dir))


def agent_tools_with_files(tools: Sequence[str], files_dir: str | None) -> tuple[str, ...]:
    """The agent's rules for one call: a rule naming the profile's files only when they're there.

    A call without the copy drops it: a rule for a path that doesn't exist allows nothing, and a
    literal placeholder in a rule would say nothing to whoever reads the call.
    """
    if files_dir is None:
        return tuple(tool for tool in tools if AGENT_FILES not in tool)
    return tuple(with_agent_files(tool, files_dir) for tool in tools)


# The reports of a control that measures the code itself, whose findings the refactor's prompt
# names: the functions above a limit (ADR-0013) and the clones (ADR-0014).
CODE_REPORTS: Final[frozenset[str]] = frozenset({"complexity", "duplication"})


def code_controls(controls: Sequence[Control]) -> tuple[CommandControl, ...]:
    """The controls that measure the code itself, in the order they were declared."""
    return tuple(c for c in command_controls(controls) if c.report in CODE_REPORTS)


def agent_command(control: Control, files_dir: str | None) -> str | None:
    """A control's `agent_run` as the agent can run it in this call, or None if it can't."""
    return runnable_command(control.agent_run, files_dir)


def runnable_command(command: str | None, files_dir: str | None) -> str | None:
    """A command as the agent can run it in this call: one naming the profile's files needs them."""
    if command is None:
        return None
    if AGENT_FILES in command:
        return None if files_dir is None else with_agent_files(command, files_dir)
    return command


# The green's coverage report, next to the profile's files for a refactor call (F71): the
# profile's coverage measure compares with it.
GREEN_COVERAGE: Final = "coverage-del-verde.json"
