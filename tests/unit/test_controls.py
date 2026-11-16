"""A control as the domain sees it, which run when, and how one reads another (ADR-0007/8/9)."""

from dataclasses import replace

import pytest

from lictor.domain.controls import (
    CommandControl,
    CommandPart,
    Control,
    EffectiveCommand,
    ReaderControl,
    ReadStep,
    RunStep,
    check_selection,
    command_controls,
    controls_of_stage,
    effective_command,
    executed_as,
    lent_by_check,
    reader_of,
    reader_problems,
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

    assert executed_as(UNIT, lent) == (
        "unit se ejecutó como: pytest --junitxml {out} --cov --json={out}"
    )
    assert executed_as(UNIT, own) is None


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

    assert without == (
        RunStep(UNIT, EffectiveCommand((CommandPart(UNIT.run, "unit"),))),
        ReadStep(COVERAGE, "cov.plugin"),
    )


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
