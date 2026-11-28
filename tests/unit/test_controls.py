"""A control as the domain sees it, which run when, and how one reads another (ADR-0007/8/9)."""

from dataclasses import replace
from typing import Any

import pytest

from lictor.domain.controls import (
    REPORT_PATHS_NOTE,
    AgentCommands,
    AgentFix,
    AgentMeasure,
    CommandControl,
    CommandPart,
    Control,
    EffectiveCommand,
    ReaderControl,
    ReadStep,
    RunStep,
    agent_command_lines,
    agent_commands,
    agent_measures,
    agent_measures_text,
    allowed_by,
    bash_admits,
    check_selection,
    command_controls,
    commands_list,
    controls_of_stage,
    counts_for_agent,
    effective_command,
    executed_as,
    fixes_text,
    granted_tools,
    lent_by_check,
    reader_of,
    reader_problems,
    refactor_measures,
    refactor_measures_text,
    runnable_fixes,
    unrunnable_by_agent,
    verification_steps,
    without_report,
)
from lictor.domain.errors import ConfigError
from lictor.domain.stages import ATTEMPT, CHECK, Stage
from lictor.domain.verdicts import VerdictRule


def _control(control_id: str, stage: Stage = ATTEMPT) -> CommandControl:
    return CommandControl(control_id, "L1", "cmd", "exit-code", stage=stage)


def test_each_stage_keeps_only_its_controls_in_their_order() -> None:
    controls = [
        _control("lint"),
        _control("mutation", CHECK),
        _control("unit"),
        _control("audit", CHECK),
    ]

    assert [c.id for c in controls_of_stage(controls, ATTEMPT)] == ["lint", "unit"]
    assert [c.id for c in controls_of_stage(controls, CHECK)] == ["mutation", "audit"]


def test_no_controls_of_a_stage_is_an_empty_tuple() -> None:
    assert controls_of_stage([_control("lint")], CHECK) == ()


def test_a_control_runs_in_every_attempt_and_blocks_unless_it_says_otherwise() -> None:
    control = CommandControl("lint", "L1", "cmd", "exit-code")

    assert (control.mode, control.stage, control.timeout_s) == ("block", ATTEMPT, 600)
    assert (control.empty_exit_codes, control.when_exists, control.when_plugin) == ((), None, None)
    assert control.min_score is None
    assert control.agent_run is None


def test_its_verdict_rule_is_its_report_its_timeout_and_its_empty_codes() -> None:
    control = CommandControl(
        "unit", "L2", "cmd {out}", "junit", timeout_s=30, empty_exit_codes=(5,)
    )

    assert control.verdict_rule == VerdictRule("junit", 30, (5,))


# --- ADR-0008: a control that reads the run of another ---

UNIT = CommandControl("unit", "L2", "pytest --junitxml {out}", "junit", empty_exit_codes=(5,))
COVERAGE = ReaderControl(
    "coverage", "L2", "unit", "--cov --json={out}", mode="measure", when_plugin="cov.plugin"
)
LINT = CommandControl("lint", "L1", "ruff", "exit-code")
COV = ("cov.plugin",)


def test_a_reader_has_nothing_of_a_run_and_only_reads_coverage() -> None:
    reader = ReaderControl("coverage", "L2", "unit", "--cov {out}")

    assert (reader.report, reader.mode, reader.stage) == ("coverage", "block", ATTEMPT)
    assert (reader.when_plugin, reader.min_score) == (None, None)
    assert not hasattr(reader, "timeout_s")
    assert not hasattr(reader, "when_exists")


def test_only_command_controls_name_a_program_to_start() -> None:
    assert command_controls([LINT, UNIT, COVERAGE]) == (LINT, UNIT)


def test_a_stage_keeps_its_readers_as_well() -> None:
    late = replace(COVERAGE, stage=CHECK)

    assert controls_of_stage([UNIT, late], CHECK) == (late,)


def test_the_reader_of_a_control_is_the_one_that_reads_it() -> None:
    assert reader_of("unit", [LINT, UNIT, COVERAGE]) == COVERAGE
    assert reader_of("lint", [LINT, UNIT, COVERAGE]) is None


@pytest.mark.parametrize(
    ("controls", "expected"),
    [
        pytest.param([LINT, UNIT, COVERAGE], [], id="a-valid-pair"),
        pytest.param([LINT, COVERAGE], ["no está entre los controles activos"], id="orphan"),
        pytest.param(
            [UNIT, COVERAGE, replace(COVERAGE, id="otro", reads="coverage")],
            ["también es un lector"],
            id="reads-a-reader",
        ),
        pytest.param(
            [LINT, replace(COVERAGE, reads="lint")],
            ["cuyo report es 'exit-code'"],
            id="reads-no-junit",
        ),
        pytest.param(
            [UNIT, COVERAGE, replace(COVERAGE, id="otro")],
            ["tiene 2 lectores ('coverage', 'otro')"],
            id="two-readers",
        ),
        pytest.param([COVERAGE, UNIT], ["va antes que 'unit'"], id="reader-first"),
        pytest.param(
            [replace(UNIT, stage=CHECK), COVERAGE],
            ['pon \'coverage\' también en check ({"id": "coverage", "stage": "check"})'],
            id="attempt-reader-of-a-check-control",
        ),
        pytest.param([UNIT, replace(COVERAGE, stage=CHECK)], [], id="check-reader-of-attempt"),
        pytest.param(
            [replace(UNIT, stage=CHECK), replace(COVERAGE, stage=CHECK)], [], id="both-in-check"
        ),
    ],
)
def test_what_is_wrong_with_how_readers_read(controls: list[Control], expected: list[str]) -> None:
    problems = reader_problems(controls)

    assert len(problems) == len(expected)
    for problem, fragment in zip(problems, expected, strict=True):
        assert fragment in problem


def test_an_orphan_reader_is_told_how_to_go_without_its_read_control() -> None:
    (problem,) = reader_problems([COVERAGE])

    assert '{"id": "coverage", "enabled": false}' in problem
    assert "run propio" in problem


@pytest.mark.parametrize(
    ("verified", "installed", "expected", "lent"),
    [
        pytest.param(
            [UNIT, COVERAGE],
            COV,
            "pytest --junitxml {out} --cov --json={out}",
            None,
            id="reader-runs-there",
        ),
        pytest.param([UNIT, COVERAGE], (), "pytest --junitxml {out}", None, id="no-plugin"),
        pytest.param([UNIT], COV, "pytest --junitxml {out}", None, id="reader-not-there"),
        pytest.param(
            [UNIT, replace(COVERAGE, stage=CHECK)],
            COV,
            "pytest --junitxml {out} --cov --json={out}",
            " (con los argumentos de coverage, que solo corre en check)",
            id="check-reader-in-check",
        ),
        pytest.param(
            [UNIT, replace(COVERAGE, when_plugin=None)],
            (),
            "pytest --junitxml {out} --cov --json={out}",
            None,
            id="reader-needs-no-plugin",
        ),
    ],
)
def test_the_effective_command_of_a_read_control(
    verified: list[Control], installed: tuple[str, ...], expected: str, lent: str | None
) -> None:
    command = effective_command(UNIT, verified, installed)

    assert command.configured == expected
    assert lent_by_check(command) == lent


def test_each_part_of_the_effective_command_names_whose_out_it_holds() -> None:
    command = effective_command(UNIT, [UNIT, COVERAGE], COV)

    assert command.parts == (
        CommandPart("pytest --junitxml {out}", "unit"),
        CommandPart("--cov --json={out}", "coverage"),
    )
    assert command.reader == COVERAGE


def test_the_agent_reads_the_effective_command_only_when_it_isnt_the_run() -> None:
    lent = effective_command(UNIT, [UNIT, COVERAGE], COV)
    own = effective_command(UNIT, [UNIT, COVERAGE], ())

    assert executed_as(lent, ran=True) == (
        "unit se ejecutó como: pytest --junitxml {out} --cov --json={out}\n" + REPORT_PATHS_NOTE
    )
    assert executed_as(own, ran=True) is None
    # A control that never ran ran no command: skipped, or unable to run.
    assert executed_as(lent, ran=False) is None


def test_the_run_and_check_give_one_command_whatever_is_selected() -> None:
    """ADR-0008, point 4: `--control unit` runs the same unit as the whole check."""
    everything = [LINT, UNIT, COVERAGE]
    alone = verification_steps([UNIT], everything, COV)
    whole = verification_steps(everything, everything, COV)

    assert isinstance(alone[0], RunStep)
    assert isinstance(whole[1], RunStep)
    assert alone[0].command == whole[1].command
    # Alone, nobody reads the report its `{out}` leaves.
    assert alone[0].read_by is None
    assert whole[1].read_by == COVERAGE


def test_the_steps_decide_the_readers_plugin_once_for_both_turns() -> None:
    without = verification_steps([UNIT, COVERAGE], [UNIT, COVERAGE], ())

    assert without == (RunStep(EffectiveCommand(UNIT)), ReadStep(COVERAGE, "cov.plugin"))


def test_a_step_reads_only_the_report_of_the_reader_lending_its_arguments() -> None:
    lent = RunStep(EffectiveCommand(UNIT, COVERAGE), reads=True)

    assert (lent.control, lent.read_by) == (UNIT, COVERAGE)
    assert RunStep(EffectiveCommand(UNIT, COVERAGE)).read_by is None
    # Without a reader there is no report to read, whatever the flag says.
    assert RunStep(EffectiveCommand(UNIT), reads=True).read_by is None


def test_a_command_control_without_its_plugin_is_a_step_that_skips() -> None:
    needs = replace(LINT, when_plugin="x.plugin")

    (step,) = verification_steps([needs], [needs], ())

    assert isinstance(step, RunStep)
    assert step.missing == "x.plugin"


@pytest.mark.parametrize(
    ("levels", "ids", "installed", "expected"),
    [
        pytest.param(None, None, COV, (["lint", "unit", "coverage"], [], True), id="everything"),
        pytest.param(None, ["coverage"], COV, (["unit", "coverage"], ["unit"], False), id="reader"),
        pytest.param(None, ["coverage"], (), (["coverage"], [], False), id="reader-no-plugin"),
        pytest.param(None, ["unit"], COV, (["unit"], [], False), id="read-control-alone"),
        pytest.param(["L1"], None, COV, (["lint"], [], False), id="a-level"),
        pytest.param([" l3 "], None, COV, ([], [], True), id="acceptance-level"),
        pytest.param(None, ["acceptance"], COV, ([], [], True), id="acceptance-id"),
        pytest.param(["L1"], ["unit", "lint"], COV, (["lint"], [], False), id="both-filters"),
    ],
)
def test_what_check_runs_of_what_was_asked(
    levels: list[str] | None,
    ids: list[str] | None,
    installed: tuple[str, ...],
    expected: tuple[list[str], list[str], bool],
) -> None:
    """The controls that run, those added for a reader, and whether the acceptance runs."""
    selection = check_selection([LINT, UNIT, COVERAGE], levels, ids, installed)

    added = [a.control.id for a in selection.added]
    assert ([c.id for c in selection.controls], added, selection.acceptance) == expected


def test_a_level_that_splits_the_pair_brings_the_read_control_in_its_place() -> None:
    reader = replace(COVERAGE, level="L4")
    controls = [LINT, UNIT, CommandControl("arch", "L4", "a", "exit-code"), reader]

    selection = check_selection(controls, ["L4"], None, COV)

    assert [c.id for c in selection.controls] == ["unit", "arch", "coverage"]
    (added,) = selection.added
    assert added.sentence == "Se ejecuta también unit, cuya ejecución lee coverage"


def test_check_refuses_an_id_that_names_nothing_and_names_what_there_is() -> None:
    with pytest.raises(ConfigError, match=r"mutacion.*acceptance, coverage, lint, unit"):
        check_selection([LINT, UNIT, COVERAGE], None, ["mutacion"], COV)


# What the agent is told to measure by itself (F55).

_COVERAGE = ReaderControl(
    "coverage",
    "L2",
    reads="unit",
    adds="--cov {out}",
    min_score=80,
    when_plugin="cov.plugin",
    agent_run="tests --cov",
)


def test_a_minimum_with_its_command_and_its_plugin_is_told_to_the_agent() -> None:
    assert agent_measures([_COVERAGE], ["cov.plugin"]) == (
        AgentMeasure("coverage", "tests --cov", 80),
    )


@pytest.mark.parametrize(
    ("control", "installed"),
    [
        (replace(_COVERAGE, min_score=None), ["cov.plugin"]),
        (replace(_COVERAGE, agent_run=None), ["cov.plugin"]),
        (replace(_COVERAGE, agent_run=""), ["cov.plugin"]),
        (_COVERAGE, []),
    ],
    ids=["sin mínimo", "sin orden", "orden vacía", "sin su plugin"],
)
def test_nothing_is_told_without_a_minimum_a_command_or_the_plugin(
    control: Control, installed: list[str]
) -> None:
    assert agent_measures([control], installed) == ()


def test_a_command_control_with_a_minimum_is_told_too_and_order_is_kept() -> None:
    mutation = CommandControl(
        "mutation", "L5", "mutar {out}", "mutation", min_score=92.5, agent_run="mutar"
    )

    measures = agent_measures([mutation, _control("lint"), _COVERAGE], ["cov.plugin"])

    assert [m.control_id for m in measures] == ["mutation", "coverage"]


def test_the_prompt_names_each_minimum_and_its_command() -> None:
    text = agent_measures_text(
        [AgentMeasure("coverage", "tests --cov", 80), AgentMeasure("mutation", "mutar", 92.5)]
    )

    assert text == (
        "- La verificación también exige una puntuación mínima, medida igual que con estas "
        "órdenes. Mídela antes de terminar y, si no llega, añade tests:\n"
        "  - coverage: al menos 80 %, con `tests --cov`.\n"
        "  - mutation: al menos 92,5 %, con `mutar`."
    )


def test_no_minimum_leaves_the_prompt_as_it_was() -> None:
    assert agent_measures_text([]) == ""


def test_a_refactor_measures_every_agent_run_whose_plugin_loads_with_or_without_a_minimum() -> None:
    """It must not measure less than its green, minimum or not (ADR-0010)."""
    unscored = replace(_COVERAGE, id="otra", min_score=None)
    controls = [_control("lint"), _COVERAGE, unscored, replace(_COVERAGE, id="sin", agent_run="")]

    assert refactor_measures(controls, ["cov.plugin"]) == (
        ("coverage", "tests --cov"),
        ("otra", "tests --cov"),
    )
    assert refactor_measures(controls, []) == ()


def test_the_refactor_is_told_to_measure_before_and_after() -> None:
    text = refactor_measures_text([("coverage", "tests --cov")])

    assert "descarta el refactor si deja más" in text
    assert "Quitar código que ya estaba probado no cuenta" in text
    assert "  - coverage: `tests --cov`." in text
    assert refactor_measures_text([]) == ""


def test_the_refactor_is_told_what_the_green_left_untested_and_what_to_do_if_it_rises() -> None:
    """F71: one measure after editing is enough to see a rise, as with the clones."""
    text = refactor_measures_text(
        [("coverage", "cov"), ("mutation", "mut"), ("otra", "x")],
        {"coverage": {"lines": 712, "branches": 327}, "mutation": {"mutants": 4}},
    )

    assert (
        "  - coverage: `cov`. El verde dejaba 712 líneas y 327 ramas sin cubrir: el refactor no "
        "puede dejar más." in text
    )
    assert "  - mutation: `mut`. El verde dejaba 4 mutantes sin matar" in text
    assert "  - otra: `x`.\n" in text
    assert "cúbrelo con un test unitario o no lo partas" in text
    assert "no lo partas" not in refactor_measures_text([("coverage", "cov")])


def test_the_refactor_measures_read_whole() -> None:
    assert refactor_measures_text([("a", "x"), ("b", "y")]) == (
        "- La verificación compara lo que estos controles dejan sin probar con lo que dejaban en "
        "el verde, y descarta el refactor si deja más. Quitar código que ya estaba probado no "
        "cuenta. Mídelo antes y después de cambiar nada:\n"
        "  - a: `x`.\n"
        "  - b: `y`."
    )


# The agent's commands come from the controls (F83, ADR-0018).


def _block(cid: str, run: str, **fields: Any) -> CommandControl:
    return CommandControl(id=cid, level="L1", run=run, report="exit-code", **fields)


def _junit(cid: str, run: str, **fields: Any) -> CommandControl:
    return CommandControl(id=cid, level="L2", run=run, report="junit", **fields)


_TOOLS = ("Bash(uv run ruff:*)", "Bash(uv run pytest:*)", "Bash(make lint)", "Read")


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("uv run ruff check src", True),
        ("uv run pytest", True),
        ("make lint", True),
        ("make lint src", False),
        ("make", False),
        ("uv run mypy src", False),
        ("Read", False),
    ],
)
def test_the_agents_tools_allow_by_prefix_or_exact_command(command: str, allowed: bool) -> None:
    assert allowed_by(command, _TOOLS) is allowed


def test_what_bash_admits_is_each_rule_once() -> None:
    tools = (*_TOOLS, "Bash(uv run ruff:*)", "Bash(*)")

    assert bash_admits(tools) == (
        "`uv run ruff`, `uv run pytest`, y estas órdenes tal cual: `make lint`, `*`"
    )


def test_bash_names_its_exact_commands_apart_from_its_prefixes() -> None:
    """S17: an exact rule isn't a prefix; the prompt says each as it is."""
    tools = ("Bash(uv run pytest:*)", "Bash(gitleaks dir)", "Bash(gitleaks dir)")

    assert bash_admits(tools) == "`uv run pytest`, y estas órdenes tal cual: `gitleaks dir`"
    assert bash_admits(("Bash(gitleaks dir)",)) == (
        "ninguna, y estas órdenes tal cual: `gitleaks dir`"
    )
    assert bash_admits(()) == "ninguna"


@pytest.mark.parametrize(
    ("control", "counts"),
    [
        (_block("l", "l"), True),
        (_block("l", "l", mode="measure"), False),
        (_block("l", "l", stage="check"), False),
        (_block("l", "l", when_plugin="falta.plugin"), False),
        (_block("l", "l", when_plugin="esta.plugin"), True),
        (_block("l", "l", when_exists="no/existe"), True),
        (ReaderControl(id="c", level="L2", reads="u", adds="--c {out}"), True),
    ],
)
def test_what_counts_for_the_agent_blocks_every_attempt(control: Control, counts: bool) -> None:
    assert counts_for_agent(control, ("esta.plugin",)) is counts


@pytest.mark.parametrize(
    ("run", "command"),
    [
        ("uv run ruff check src", "uv run ruff check src"),
        ('uv run pytest -m "not x" -q', 'uv run pytest -m "not x" -q'),
        ("uv run pytest tests -q --junitxml {out}", "uv run pytest tests -q"),
        ("medir src --out {out} -q", "medir src -q"),
        ("pytest --cov-report=json:{out} -q", "pytest -q"),
        ('pytest -m "not x" --junitxml {out}', "pytest -m 'not x'"),
        ("mutation_score.py {out}", None),
        ("script.py -v {out}", "script.py"),  # known limit: an option without value goes too
    ],
)
def test_a_run_without_its_report_is_how_the_agent_runs_it(run: str, command: str | None) -> None:
    assert without_report(run) == command


def test_the_profiles_order_gives_the_fixes_and_then_each_check() -> None:
    controls: list[Control] = [
        _block("lint", "uv run ruff check src"),
        _block("format", "uv run ruff format --check src", agent_fix="uv run ruff format src"),
        _block("types", "uv run mypy src"),
        _junit("unit", "uv run pytest tests/unit -q --junitxml {out}"),
        ReaderControl(id="coverage", level="L2", reads="unit", adds="--cov {out}", mode="measure"),
        _block("api", "uv run python -m api src --out {out}", mode="measure"),
    ]
    tools = (*_TOOLS, "Bash(uv run mypy:*)")

    found = agent_commands(
        controls, "uv run pytest tests/acceptance -q --junitxml {out}", tools, ()
    )

    assert found.fixes == (AgentFix("format", "uv run ruff format src"),)
    assert found.checks == (
        "uv run ruff check src",
        "uv run ruff format --check src",
        "uv run mypy src",
        "uv run pytest tests/unit -q",
        "uv run pytest tests/acceptance -q",
    )
    assert found.all == ("uv run ruff format src", *found.checks)


def test_a_read_control_is_checked_without_its_readers_arguments() -> None:
    """F83: measuring has its own command, and the agent would leave reports in its copy."""
    unit = _junit("unit", "uv run pytest tests -q --junitxml {out}")
    reader = ReaderControl(id="coverage", level="L2", reads="unit", adds="--cov=src --cov {out}")

    found = agent_commands([unit, reader], "", _TOOLS, ())

    assert found.checks == ("uv run pytest tests -q",)


def test_a_blocking_reader_gives_its_agent_run_or_nothing() -> None:
    unit = _junit("unit", "uv run pytest tests -q --junitxml {out}")
    measured = ReaderControl(
        id="cov", level="L2", reads="unit", adds="--c {out}", agent_run="uv run pytest --cov"
    )
    silent = ReaderControl(id="cov", level="L2", reads="unit", adds="--c {out}")

    assert agent_commands([unit, measured], "", _TOOLS, ()).checks == (
        "uv run pytest tests -q",
        "uv run pytest --cov",
    )
    assert agent_commands([unit, silent], "", _TOOLS, ()).checks == ("uv run pytest tests -q",)


def test_what_counts_nothing_or_the_tools_deny_gives_no_command() -> None:
    controls: list[Control] = [
        _block("a", "uv run ruff a", mode="measure", agent_fix="uv run ruff fix-a"),
        _block("b", "uv run ruff b", stage="check"),
        _block("c", "uv run ruff c", when_plugin="falta", agent_fix="uv run ruff fix-c"),
        _block("d", "make d", agent_fix="make fix-d"),
        _block("e", "mutation_score.py {out}"),
        _block("f", "uv run ruff f", agent_run="uv run ruff f --x"),
        _block("g", "uv run ruff f", agent_run="uv run ruff f --x"),
    ]

    found = agent_commands(controls, "", _TOOLS, ())

    assert found == AgentCommands((), ("uv run ruff f --x",))


def test_the_acceptance_goes_last_and_without_its_report() -> None:
    found = agent_commands([], "uv run pytest acc --junitxml {out}", _TOOLS, ())

    assert found.checks == ("uv run pytest acc",)
    assert agent_commands([], "", _TOOLS, ()) == AgentCommands()


def test_the_fixes_are_asked_after_the_last_change_and_before_the_checks() -> None:
    assert fixes_text([]) == ""
    assert fixes_text([AgentFix("format", "fmt"), AgentFix("lint", "fix")]) == (
        "- Tras tu último cambio, ejecuta `fmt` (el control format bloquea) y `fix` (el control "
        "lint bloquea) y después las comprobaciones."
    )


def test_the_prompt_lists_one_command_a_line_and_says_what_bash_admits() -> None:
    lines = agent_command_lines(
        ["Bash(make:*)", "Bash(uv run python {lictor_files}/m.py:*)"],
        ["make a", "uv run python {lictor_files}/m.py x", "make b"],
        [AgentFix("format", "make fmt")],
        "/f",
        ["make b", "medir"],
    )

    assert lines == {
        "{bash_admite}": "`make`, `uv run python /f/m.py`",
        "{comprobaciones}": "  - `make a`\n  - `uv run python /f/m.py x`\n  - `make b`\n"
        "  - `medir`",
        "{arreglos}": "- Tras tu último cambio, ejecuta `make fmt` (el control format bloquea) y "
        "después las comprobaciones.",
    }
    assert (
        "lictor_files"
        not in agent_command_lines(
            ["Bash(uv run python {lictor_files}/m.py:*)"],
            ["uv run python {lictor_files}/m.py"],
            [],
            None,
        )["{comprobaciones}"]
    )
    empty = agent_command_lines([], [], [], None)
    assert empty == {
        "{bash_admite}": "ninguna",
        "{comprobaciones}": "  - los tests del proyecto",
        "{arreglos}": "",
    }
    assert commands_list(["x"]) == "  - `x`"


def test_a_fix_naming_the_profiles_files_runs_only_with_them() -> None:
    fixes = [AgentFix("a", "uv run python {lictor_files}/f.py"), AgentFix("b", "fmt")]

    assert runnable_fixes(fixes, None) == [AgentFix("b", "fmt")]
    assert runnable_fixes(fixes, "/c") == [
        AgentFix("a", "uv run python /c/f.py"),
        AgentFix("b", "fmt"),
    ]


def test_only_a_bash_rule_allows_a_command() -> None:
    """Another tool whose rule looks the same allows nothing in Bash."""
    assert not allowed_by("uv run ruff check", ("Task(uv run ruff:*)",))
    assert bash_admits(("Task(uv run ruff:*)", "Bash(make:*)")) == "`make`"


def test_without_an_acceptance_there_is_no_empty_command() -> None:
    assert agent_commands([], "", ("Bash(:*)",), ()) == AgentCommands()


def test_a_fix_naming_the_profiles_files_is_asked_with_them() -> None:
    fix = [AgentFix("format", "uv run python {lictor_files}/fmt.py")]

    lines = agent_command_lines(["Bash(uv run python {lictor_files}/fmt.py:*)"], [], fix, "/f")

    assert "`uv run python /f/fmt.py`" in lines["{arreglos}"]


def test_what_blocks_and_the_agent_cant_run_is_named_and_nothing_else() -> None:
    lint = CommandControl(id="lint", level="L1", run="go vet ./...", report="exit-code")
    measured = CommandControl(
        id="cx", level="L1", run="gocyclo .", report="exit-code", mode="measure"
    )

    found = unrunnable_by_agent([lint, measured], "gotestsum --junitfile {out}", [], ())

    assert [line.split(" (")[0] for line in found] == [
        "agent_tools no deja al agente ejecutar el control lint",
        "agent_tools no deja al agente ejecutar la aceptación",
    ]
    assert "(`gotestsum`)" in found[1]
    allowed = ["Bash(go vet:*)", "Bash(gotestsum:*)"]
    assert unrunnable_by_agent([lint, measured], "gotestsum --junitfile {out}", allowed, ()) == ()
    assert unrunnable_by_agent([], "", [], ()) == ()


def test_a_control_whose_plugin_is_missing_isnt_the_agents_to_run() -> None:
    cov = CommandControl(
        id="cov", level="L2", run="cov run", report="exit-code", when_plugin="cov.plugin"
    )

    assert unrunnable_by_agent([cov], "", [], ()) == ()
    assert unrunnable_by_agent([cov], "", [], ("cov.plugin",)) == (
        "agent_tools no deja al agente ejecutar el control cov (`cov run`): no podrá comprobarlo "
        "antes de acabar. Añade a agent_tools una regla Bash(<prefijo>:*) que lo permita.",
    )


def test_a_report_given_alone_leaves_the_agent_without_a_check_and_says_so() -> None:
    run = "sh -c 'node --test --test-reporter-destination=\"$0\" acceptance/*.test.js' {out}"

    [told] = unrunnable_by_agent([], run, ["Bash(sh -c:*)"], ())

    assert told.startswith(f"el agente no tiene orden para comprobar la aceptación (`{run}`)")
    assert told.endswith("así que no podrá comprobarlo antes de acabar.")


def test_each_check_every_attempt_runs_is_granted_to_the_agent_exactly() -> None:
    """S17: a control a project adds reaches the agent without copying its profile's tools."""
    controls = [
        CommandControl(id="lint", level="L1", run="uv run ruff check src", report="exit-code"),
        CommandControl(
            id="imports", level="L4", run="uv run lint-imports --config x", report="exit-code"
        ),
        CommandControl(
            id="unit", level="L2", run="uv run pytest tests/unit --junitxml {out}", report="junit"
        ),
        CommandControl(
            id="secretos", level="L4", run="gitleaks dir", report="exit-code", grants_agent=False
        ),
        CommandControl(
            id="medida", level="L1", run="radon cc src", report="exit-code", mode="measure"
        ),
        CommandControl(
            id="con-plugin",
            level="L2",
            run="uv run pytest -p x",
            report="exit-code",
            when_plugin="x.plugin",
        ),
    ]
    tools = ("Bash(uv run ruff:*)",)

    granted = granted_tools(
        controls, "uv run pytest tests/acceptance --junitxml {out}", tools, ("x.plugin",)
    )

    assert granted == (
        "Bash(uv run ruff:*)",
        "Bash(uv run lint-imports --config x)",
        "Bash(uv run pytest tests/unit)",
        "Bash(uv run pytest -p x)",
        "Bash(uv run pytest tests/acceptance)",
    )
    assert granted_tools([], "", tools, ()) == tools
