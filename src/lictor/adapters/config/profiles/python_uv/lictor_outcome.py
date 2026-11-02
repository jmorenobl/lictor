"""Tells, for each failed test, how it failed and which Gherkin scenario it executed.

Lictor copies this file into the verification environment, so it runs where Lictor isn't installed
and must import nothing of it. It reports facts as JUnit properties and decides nothing: which
failures count as a valid red is up to the profile. It runs in the same process as the code under
test, which could forge them, as it could forge the whole report.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

FAILURE = "lictor.failure"
ORIGIN = "lictor.origin"
SCENARIO = "lictor.scenario"
FEATURE = "lictor.feature"


def _relative(path: object, root: Path) -> str:
    """Return `path` relative to the project, or "" if it lies outside it."""
    try:
        return Path(str(path)).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


def failure_kind(excinfo: pytest.ExceptionInfo[BaseException], when: str) -> str:
    """Classify by the exception's class, never by its text."""
    if when != "call":
        return "error"
    if excinfo.errisinstance(NotImplementedError):
        return "not_implemented"
    if excinfo.errisinstance((AssertionError, pytest.fail.Exception)):
        return "assertion"
    return "error"


@pytest.hookimpl(optionalhook=True)
def pytest_bdd_before_scenario(request: pytest.FixtureRequest, feature: Any, scenario: Any) -> None:
    """Record the scenario a test executes: its tags and its feature file."""
    root = request.config.rootpath
    request.node.user_properties.append((SCENARIO, " ".join(sorted(scenario.tags))))
    request.node.user_properties.append((FEATURE, _relative(feature.filename, root)))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> "Generator[None, Any, None]":
    """Record the first failure of a test; the report reads the properties at teardown."""
    outcome = yield
    report = outcome.get_result()
    recorded = any(name == FAILURE for name, _ in item.user_properties)
    if report.failed and call.excinfo is not None and not recorded:
        origin = _relative(call.excinfo.traceback[-1].path, item.config.rootpath)
        item.user_properties.append((FAILURE, failure_kind(call.excinfo, call.when)))
        item.user_properties.append((ORIGIN, origin))
