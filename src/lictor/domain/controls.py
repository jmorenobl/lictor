"""A verification control as the domain sees it (ADR-0009, point 9).

The configuration resolves to these, in one conversion; the run and `lictor check` get them in
the order they were declared. ADR-0008 adds a control that reads the run of another.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lictor.domain.reports import ReportFormat
from lictor.domain.stages import ATTEMPT, Stage
from lictor.domain.verdicts import VerdictRule

ControlMode = Literal["block", "measure"]


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


def controls_of_stage(
    controls: Sequence[CommandControl], stage: Stage
) -> tuple[CommandControl, ...]:
    """The controls of one stage, in the order they were declared (ADR-0007)."""
    return tuple(control for control in controls if control.stage == stage)
