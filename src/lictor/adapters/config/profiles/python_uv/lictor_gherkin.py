"""Changes a value a Then reads from the Gherkin, to prove a new test compares it (ADR-0016).

Lictor copies this file into the verification environment, so it runs where Lictor isn't installed
and must import nothing of it; it runs in the project's interpreter, so it stays valid back to
Python 3.9. Without its variable it does nothing. With it, in each test it numbers, in order, the
values the Then steps receive from the Gherkin: those their parser extracts and the step's function
takes, converted, and its data table and doc string. For each one it says whether the function ever
reads it, a fact of its bytecode (a closure that captures it reads it), and with a mode other than
the control it changes only the value numbered `target`, before the step runs: a number one more
or less in its own format, a date a day, a list an element, a text, the last cell of a table.
With `targets` (scenario IDs) it deselects every other test, when the tags tell them apart.

It reports, as JUnit properties, how many values there were, which could be changed, which the
step never reads (with the step and the value, so the agent can be told), how many the parser
extracts and the function doesn't take, and whether it changed one; it decides nothing. It uses two
internal functions of pytest-bdd (`get_step_function` and `parse_step_arguments`): without them it
says nothing.

It runs in the same process as the code under test, which could see it and behave otherwise, as it
could forge the whole report.
"""

from __future__ import annotations

import datetime
import dis
import importlib
import inspect
import json
import os
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

VARIABLE = "LICTOR_GHERKIN"
VALUES = "lictor.gherkin.values"  # how many values the Then steps received from the Gherkin
ALTERABLE = "lictor.gherkin.alterable"  # the numbers of those it can change, comma-separated
UNREAD = "lictor.gherkin.unread"  # JSON: [step, parameter, value] of each one never read
UNRECEIVED = "lictor.gherkin.unreceived"  # extracted by the parser, not taken by the function
ALTERED = "lictor.gherkin.altered"  # "1" if it changed the value it was told to
CONTROL = "control"
MORE = "mas"
LESS = "menos"
DATATABLE = "datatable"
DOCSTRING = "docstring"
_SHOWN = 60  # how much of a value the report keeps
_INT = re.compile(r"-?\d+")
_DECIMAL = re.compile(r"(-?\d+)([.,])(\d+)")
_DAY_FIRST = re.compile(r"(\d{2})([-/])(\d{2})\2(\d{4})")
_UNCHANGED = object()
# What loads a local, anywhere in the name of the instruction: 3.13 joins two in one
# (`STORE_FAST_LOAD_FAST`, `LOAD_FAST_LOAD_FAST`), and 3.14 borrows (`LOAD_FAST_BORROW`).
_READS = ("LOAD_FAST", "LOAD_DEREF", "LOAD_CLOSURE")


def _shifted_number(text: str, step: int) -> str | None:
    if _INT.fullmatch(text):
        return str(int(text) + step)
    found = _DECIMAL.fullmatch(text)
    if found is None:
        return None
    whole, separator, fraction = found.groups()
    value = Decimal(f"{whole}.{fraction}") + step
    return f"{value:.{len(fraction)}f}".replace(".", separator)


def _shifted_date(text: str, step: int) -> str | None:
    found = _DAY_FIRST.fullmatch(text)
    if found is not None:
        day, separator, month, year = found.groups()
        try:
            moved = datetime.date(int(year), int(month), int(day)) + datetime.timedelta(days=step)
        except ValueError:
            return None
        return f"{moved.day:02d}{separator}{moved.month:02d}{separator}{moved.year:04d}"
    for kind in (datetime.datetime, datetime.date):
        try:
            value = kind.fromisoformat(text)
        except ValueError:
            continue
        if value.isoformat() == text:
            return (value + datetime.timedelta(days=step)).isoformat()
    return None


def altered_text(text: str, mode: str) -> str:
    """Another text like `text`: a number or a date moved in its own format, a list by one."""
    step = 1 if mode == MORE else -1
    core = text.strip()
    for shift in (_shifted_number, _shifted_date):
        changed = shift(core, step)
        if changed is not None:
            return changed
    parts = [part.strip() for part in text.split(",")]
    if len(parts) > 1:
        return ", ".join([*parts, "x9"] if mode == MORE else parts[:-1])
    if mode == MORE:
        return text + "x"
    if not text:
        return "y"
    return ("y" if text[0] != "y" else "z") + text[1:]


def alter(value: Any, mode: str) -> Any:
    """Another value of the same kind, or `_UNCHANGED` when the kind isn't known."""
    step = 1 if mode == MORE else -1
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float, Decimal)):
        return value + step
    if isinstance(value, datetime.date):  # a datetime too
        return value + datetime.timedelta(days=step)
    if isinstance(value, str):
        return altered_text(value, mode)
    if isinstance(value, list):
        return _altered_table(value, mode)
    return _UNCHANGED


def _altered_table(value: list[Any], mode: str) -> Any:
    """A data table with its last cell changed; its header, if it has one, stays."""
    if len(value) <= 1 or not all(isinstance(row, list) and row for row in value):
        return _UNCHANGED
    rows = [list(row) for row in value]
    rows[-1][-1] = altered_text(str(rows[-1][-1]), mode)
    return rows


def reads(func: Any, name: str) -> bool:
    """Whether the step's function ever reads its parameter `name`.

    What can't be told (no code, a parameter it doesn't declare) counts as read.
    """
    code = getattr(inspect.unwrap(func), "__code__", None)
    if code is None:
        return True
    declared = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
    if name not in declared or name in code.co_cellvars:
        return True
    for instruction in dis.get_instructions(code):
        if any(read in instruction.opname for read in _READS):
            names = instruction.argval
            if name in (names if isinstance(names, tuple) else (names,)):
                return True
    return False


def _received(request: Any, step: Any, step_func_args: dict[str, Any]) -> tuple[list[str], int]:
    """The step's arguments that come from the Gherkin, in order, and how many it doesn't take."""
    bdd: Any = importlib.import_module("pytest_bdd.scenario")
    context = bdd.get_step_function(request, step)
    parsed = bdd.parse_step_arguments(step=step, context=context) if context is not None else {}
    keys = [key for key in parsed if key in step_func_args]
    keys += [key for key in (DATATABLE, DOCSTRING) if key in step_func_args and key not in keys]
    return keys, len([key for key in parsed if key not in step_func_args])


class _Gherkin:
    """What one run of the acceptance was told, and what the test in progress received."""

    def __init__(self, mode: str, target: int | None, targets: list[str] | None) -> None:
        self.mode = mode
        self.target = target
        self.targets = targets
        self.reset()

    def reset(self) -> None:
        self.values = 0
        self.alterable: list[int] = []
        self.unread: list[list[str]] = []
        self.unreceived = 0
        self.altered = False

    def received(self, step: Any, step_func: Any, args: dict[str, Any], keys: list[str]) -> None:
        for key in keys:
            index = self.values
            self.values += 1
            value = args[key]
            if not reads(step_func, key):
                self.unread.append([str(step.name), key, _shown(value)])
            changed = alter(value, MORE)
            if changed is _UNCHANGED or changed == value:
                continue
            self.alterable.append(index)
            if self.mode != CONTROL and index == self.target:
                args[key] = alter(value, self.mode)
                self.altered = True


def _shown(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= _SHOWN else text[: _SHOWN - 1] + "…"


def _from_environment() -> _Gherkin | None:
    told = os.environ.get(VARIABLE)
    if not told:
        return None
    data = json.loads(told)
    target = data.get("target")
    targets = data.get("targets")
    return _Gherkin(
        str(data["mode"]),
        None if target is None else int(target),
        None if targets is None else [str(t) for t in targets],
    )


GHERKIN = _from_environment()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep only the tests of `targets`, when their tags (pytest-bdd's marks) tell them apart."""
    if GHERKIN is None or GHERKIN.targets is None:
        return
    wanted = set(GHERKIN.targets)
    kept = [item for item in items if wanted & {mark.name for mark in item.iter_markers()}]
    if not kept:
        return  # the tags don't reach the marks: every test runs, and only the targets count
    dropped = [item for item in items if item not in kept]
    config.hook.pytest_deselected(items=dropped)
    items[:] = kept


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    if GHERKIN is not None:
        GHERKIN.reset()


@pytest.hookimpl(optionalhook=True)
def pytest_bdd_before_step_call(
    request: Any, step: Any, step_func: Any, step_func_args: dict[str, Any]
) -> None:
    """Number what a Then receives from the Gherkin and, in a mode, change the one told."""
    if GHERKIN is None or getattr(step, "type", None) != "then":
        return
    try:
        keys, unreceived = _received(request, step, step_func_args)
    except Exception:  # pytest-bdd without these functions, or a step it can't parse: say nothing
        return
    GHERKIN.unreceived += unreceived
    GHERKIN.received(step, step_func, step_func_args, keys)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, Any, None]:
    """Record what the test's Then steps received; the report reads the properties at teardown."""
    yield
    if GHERKIN is not None and call.when == "call":
        props = item.user_properties
        props.append((VALUES, str(GHERKIN.values)))
        props.append((ALTERABLE, ",".join(map(str, GHERKIN.alterable))))
        props.append((UNREAD, json.dumps(GHERKIN.unread, ensure_ascii=False)))
        props.append((UNRECEIVED, str(GHERKIN.unreceived)))
        props.append((ALTERED, "1" if GHERKIN.altered else "0"))
