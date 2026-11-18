"""A control as the domain sees it, which run when, and how one reads another (ADR-0007/8/9)."""

from dataclasses import replace

import pytest

from lictor.domain.controls import (
    REPORT_PATHS_NOTE,
    AgentMeasure,
    CommandControl,
    CommandPart,
    Control,
    EffectiveCommand,
    ReaderControl,
    ReadStep,
    RunStep,
    agent_measures,
    agent_measures_text,
    check_selection,
    command_controls,
    controls_of_stage,
    effective_command,
    executed_as,
    lent_by_check,
    reader_of,
    reader_problems,
    refactor_measures,
    refactor_measures_text,
    verification_steps,
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


def test_the_refactor_measures_read_whole() -> None:
    assert refactor_measures_text([("a", "x"), ("b", "y")]) == (
        "- La verificación compara lo que estos controles dejan sin probar con lo que dejaban en "
        "el verde, y descarta el refactor si deja más. Quitar código que ya estaba probado no "
        "cuenta. Mídelo antes y después de cambiar nada:\n"
        "  - a: `x`.\n"
        "  - b: `y`."
    )
