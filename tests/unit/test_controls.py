"""A control as the domain sees it, and which controls run when (ADR-0007, ADR-0009)."""

from lictor.domain.controls import CommandControl, controls_of_stage
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
