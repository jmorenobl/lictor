"""Run `lictor run` in a process of its own, standing still at one point until it gets killed.

Usage: python kill_runner.py <project> <profiles> <mode> <flags dir> [<point> <marker>]

A point is `<node>:antes`, `<node>:durante` or `<node>:despues`. On reaching it the process creates
the marker and sleeps, so the test can send a real SIGKILL at exactly that moment. Without a point
it runs to the end: that is the resume.
"""

import sys
import time
from collections.abc import Callable
from functools import partial, wraps
from pathlib import Path
from typing import Any

from lictor import cli
from lictor.adapters.config import loader
from lictor.adapters.spec.gherkin_source import GherkinSource
from lictor.adapters.vcs.git_cli import GitCli
from lictor.application.usecases.build_report import BuildReport
from lictor.application.usecases.quarantine import Quarantine
from lictor.application.verification_env import VerificationEnvironment
from lictor.bootstrap import BootstrapResult, bootstrap_runtime
from lictor.domain.errors import LictorError
from lictor.domain.model import AgentStatus, AgentTask
from lictor.orchestration.graph import _GraphNodes
from tests.integration.repro.harness import Agent, good_driver, good_impl

NODES = (
    "preflight",
    "load_spec",
    "build_driver",
    "red_check",
    "pick_next",
    "implement",
    "verify",
    "commit",
    "quarantine",
    "pause",
    "report",
)
_STALL_S = 600


class _Stall:
    """Stops the process the first time it reaches the point it was given."""

    def __init__(self, point: str | None, marker: Path | None) -> None:
        self.point = point
        self.marker = marker
        self.done = False

    def at(self, point: str) -> None:
        if point == self.point and not self.done and self.marker is not None:
            self.done = True
            self.marker.touch()
            time.sleep(_STALL_S)


def _around(owner: Any, name: str, before: Callable[[], None], after: Callable[[], None]) -> None:
    original = getattr(owner, name)

    @wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        before()
        result = original(*args, **kwargs)
        after()
        return result

    setattr(owner, name, wrapper)


def _nothing() -> None:
    """Leave one side of a wrapped call alone."""


def _install(stall: _Stall) -> None:
    for node in NODES:
        _around(
            _GraphNodes,
            node,
            partial(stall.at, f"{node}:antes"),
            partial(stall.at, f"{node}:despues"),
        )
    # Each one is the moment when that node has done something and hasn't recorded it.
    during: tuple[tuple[Any, str, str, bool], ...] = (
        (GitCli, "status", "preflight", False),
        (GherkinSource, "load", "load_spec", False),
        (VerificationEnvironment, "run", "red_check", False),
        (GitCli, "advance_head", "commit", False),
        (Quarantine, "execute", "quarantine", False),
        (BuildReport, "execute", "report", True),
    )
    for owner, name, node, first in during:
        hit = partial(stall.at, f"{node}:durante")
        _around(owner, name, hit if first else _nothing, _nothing if first else hit)
    _around(cli, "_paused_exit_code", partial(stall.at, "pause:durante"), _nothing)


def _agent(mode: str, flags: Path, stall: _Stall) -> Agent:
    def driver(task: AgentTask, attempt: int) -> None:
        good_driver(task, attempt)
        stall.at("build_driver:durante")

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        flag = flags / "cuota-gastada"
        if mode == "pausa" and scenario_id == "IVA-002" and not flag.exists():
            flag.touch()
            return "rate_limited"
        if mode != "cuarentena" or scenario_id != "IVA-003":
            good_impl(task, scenario_id, attempt)
        stall.at("implement:durante")
        return None

    return Agent(driver=driver, impl=impl)


def main(args: list[str]) -> int:
    root, profiles, mode, flags = Path(args[0]), Path(args[1]), args[2], Path(args[3])
    point = args[4] if len(args) > 4 else None
    stall = _Stall(point, Path(args[5]) if len(args) > 5 else None)
    loader.DEFAULT_PROFILES_DIR = profiles
    agent = _agent(mode, flags, stall)
    _install(stall)

    def bootstrap(project_root: Path, **_kwargs: Any) -> BootstrapResult:
        return bootstrap_runtime(project_root, executor=agent.executor)

    vars(cli)["bootstrap_runtime"] = bootstrap
    try:
        return cli._execute_run(root, force_new=False)
    except LictorError as err:
        print(f"Error ({err.exit_code}): {err}", file=sys.stderr)
        return err.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
