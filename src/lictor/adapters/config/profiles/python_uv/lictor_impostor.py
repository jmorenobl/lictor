"""Alters what the interface returns to the driver, to prove a new test checks it (ADR-0011).

Lictor copies this file into the verification environment, so it runs where Lictor isn't installed
and must import nothing of it. Without its variable it does nothing. With it, it wraps, as each one
is imported, the functions and methods of the modules under the writable paths, outside the driver:
a call from a file of the driver runs the real function, and in every mode but the control one its
result is altered. It reports, for each test, how many calls crossed that boundary and how many of
their results it altered, in all and by the kind of Gherkin step that made them, as JUnit
properties, and decides nothing. In the control it alters nothing, and counts instead the results
every mode could alter.

It runs in the same process as the code under test, which could see it and behave otherwise, as it
could forge the whole report.
"""

import functools
import importlib.abc
import importlib.machinery
import inspect
import json
import math
import os
import sys
import types
from collections.abc import Callable, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Any, TypeGuard

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

VARIABLE = "LICTOR_IMPOSTOR"
CALLS = "lictor.interface.calls"
ALTERED = "lictor.interface.altered"
ALTERABLE = "lictor.interface.alterable"  # only in the control: what every mode could alter
STEPS = ("given", "when", "then")
CONTROL = "control"
MORE = "mas"  # one more: number + 1, one element more, the exception swallowed
LESS = "menos"  # one less: number - 1, one element less, another exception in its place


class ImpostorError(Exception):
    """Raised in place of the interface's own exception: a test expecting that one must fail."""


class _Sentinel:
    """What a call returns in place of None: equal to nothing but itself."""

    def __repr__(self) -> str:
        return "<impostor de Lictor>"


SENTINEL = _Sentinel()
_UNALTERED = object()


def _real(path: str) -> str:
    return os.path.realpath(path)


def _under(path: str, roots: Sequence[str]) -> bool:
    real = _real(path)
    return any(real == root or real.startswith(root + os.sep) for root in roots)


def _shorter(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(list(value.items())[:-1]) if value else {SENTINEL: SENTINEL}
    if isinstance(value, set | frozenset):
        return type(value)(list(value)[1:]) if value else type(value)({SENTINEL})
    if isinstance(value, str):
        return value[:-1] if value else "·"
    if isinstance(value, bytes):
        return value[:-1] if value else b"\0"
    return value[:-1] if value else type(value)([SENTINEL])


def _longer(value: Any) -> Any:
    if isinstance(value, dict):
        return {**value, SENTINEL: SENTINEL}
    if isinstance(value, set | frozenset):
        return type(value)({*value, SENTINEL})
    if isinstance(value, str):
        return value + "·"
    if isinstance(value, bytes):
        return value + b"\0"
    return type(value)([*value, SENTINEL])


def alter(value: Any, mode: str) -> Any:
    """Return another value of the same kind, or `_UNALTERED` when the kind isn't known."""
    if value is None:
        return SENTINEL
    if isinstance(value, bool):
        return not value
    step = 1 if mode == MORE else -1
    if isinstance(value, float) and not math.isfinite(value):
        return _UNALTERED
    if isinstance(value, int | float | Fraction | Decimal):
        changed: Any = value + step
    elif type(value) in (str, bytes, list, tuple, dict, set, frozenset):
        changed = _longer(value) if mode == MORE else _shorter(value)
    else:
        return _UNALTERED
    try:
        same = bool(changed == value)
    except Exception:  # a comparison that fails can't say it is the same
        same = False
    return _UNALTERED if same else changed


class _Impostor:
    """What one run of the acceptance was told: the mode, and where the boundary lies."""

    def __init__(self, mode: str, writable: Sequence[str], driver: Sequence[str]) -> None:
        self.mode = mode
        self.writable = [_real(root) for root in writable]
        self.driver = [_real(root) for root in driver]
        self.reset()

    def reset(self) -> None:
        """Count a new test from nothing, outside any step."""
        self.calls = 0
        self.altered = 0
        self.step: str | None = None
        self.by_step = {name: dict.fromkeys(STEPS, 0) for name in (CALLS, ALTERED, ALTERABLE)}

    def count(self, name: str) -> None:
        """Count one more `name` in the step in progress; outside a step only the total holds it."""
        if self.step in STEPS:
            self.by_step[name][self.step] += 1

    def wraps(self, path: str) -> bool:
        return _under(path, self.writable) and not _under(path, self.driver)

    def wrap(self, func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _under(sys._getframe(1).f_code.co_filename, self.driver):
                return func(*args, **kwargs)
            self.calls += 1
            self.count(CALLS)
            if self.mode == CONTROL:
                return self._counted(func, args, kwargs)
            return self._altered(func, args, kwargs)

        return wrapper

    def _counted(self, func: Callable[..., Any], args: Any, kwargs: Any) -> Any:
        """Run the real function, and count its result if every mode could alter it."""
        try:
            result = func(*args, **kwargs)
        except Exception:
            self.count(ALTERABLE)
            raise
        if _alterable(result):
            self.count(ALTERABLE)
        return result

    def _altered(self, func: Callable[..., Any], args: Any, kwargs: Any) -> Any:
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            self.altered += 1
            self.count(ALTERED)
            if self.mode == MORE:
                return None
            raise ImpostorError(f"{type(exc).__name__} sustituida por el impostor") from exc
        changed = alter(result, self.mode)
        if changed is _UNALTERED:
            return result
        self.altered += 1
        self.count(ALTERED)
        return changed


def _alterable(value: Any) -> bool:
    """Whether every mode would alter `value`. The control must pass as the red did, so a value
    whose own arithmetic or comparison raises counts as one no mode could alter, and raises
    nothing."""
    try:
        return all(alter(value, mode) is not _UNALTERED for mode in (MORE, LESS))
    except Exception:
        return False


def _plain(func: object) -> TypeGuard[types.FunctionType]:
    """A function to wrap: not a generator nor a coroutine, whose result is no single value."""
    return isinstance(func, types.FunctionType) and not (
        inspect.isgeneratorfunction(func)
        or inspect.iscoroutinefunction(func)
        or inspect.isasyncgenfunction(func)
    )


def _wrapped_member(impostor: _Impostor, member: object) -> object | None:
    if _plain(member):
        return impostor.wrap(member)
    if isinstance(member, staticmethod) and _plain(member.__func__):
        return staticmethod(impostor.wrap(member.__func__))
    if isinstance(member, classmethod) and _plain(member.__func__):
        return classmethod(impostor.wrap(member.__func__))
    if isinstance(member, property) and _plain(member.fget):
        return property(impostor.wrap(member.fget), member.fset, member.fdel, member.__doc__)
    return None


def _wrap_class(impostor: _Impostor, cls: type) -> None:
    for name, member in list(vars(cls).items()):
        if name.startswith("__") and name.endswith("__"):
            continue
        wrapped = _wrapped_member(impostor, member)
        if wrapped is not None:
            try:
                setattr(cls, name, wrapped)
            except (AttributeError, TypeError):
                continue


def wrap_module(impostor: _Impostor, module: types.ModuleType) -> None:
    """Wrap what the module itself defines: its functions, and its classes' methods."""
    for name, value in list(vars(module).items()):
        if getattr(value, "__module__", None) != module.__name__:
            continue
        if _plain(value):
            setattr(module, name, impostor.wrap(value))
        elif isinstance(value, type):
            _wrap_class(impostor, value)


class _Loader(importlib.abc.Loader):
    """Execute the module as usual, then wrap what it defines."""

    def __init__(self, impostor: _Impostor, inner: importlib.machinery.SourceFileLoader) -> None:
        self.impostor = impostor
        self.inner = inner

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType | None:
        return self.inner.create_module(spec)

    def exec_module(self, module: types.ModuleType) -> None:
        self.inner.exec_module(module)
        wrap_module(self.impostor, module)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class _Finder(importlib.abc.MetaPathFinder):
    """Find each module as the path does, and hand the writable ones to `_Loader`."""

    def __init__(self, impostor: _Impostor) -> None:
        self.impostor = impostor

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.origin is None:
            return None
        loader = spec.loader
        if not isinstance(loader, importlib.machinery.SourceFileLoader):
            return None
        if not self.impostor.wraps(spec.origin):
            return None
        spec.loader = _Loader(self.impostor, loader)
        return spec


def _from_environment() -> _Impostor | None:
    told = os.environ.get(VARIABLE)
    if not told:
        return None
    data = json.loads(told)
    return _Impostor(str(data["mode"]), list(data["writable"]), list(data["driver"]))


IMPOSTOR = _from_environment()
if IMPOSTOR is not None:
    # Before anything imports the code: a driver that imports a function keeps what it got.
    sys.meta_path.insert(0, _Finder(IMPOSTOR))


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Count each test on its own."""
    if IMPOSTOR is not None:
        IMPOSTOR.reset()


# Which step is in progress. They run inside the runner's handling of each step, so they do
# nothing that could raise.


@pytest.hookimpl(optionalhook=True)
def pytest_bdd_before_step(step: Any) -> None:
    if IMPOSTOR is not None:
        IMPOSTOR.step = str(getattr(step, "type", ""))


@pytest.hookimpl(optionalhook=True)
def pytest_bdd_after_step() -> None:
    if IMPOSTOR is not None:
        IMPOSTOR.step = None


@pytest.hookimpl(optionalhook=True)
def pytest_bdd_step_error() -> None:
    if IMPOSTOR is not None:
        IMPOSTOR.step = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> "Generator[None, Any, None]":
    """Record what the test's call did; the report reads the properties at teardown."""
    yield
    if IMPOSTOR is not None and call.when == "call":
        item.user_properties.append((CALLS, str(IMPOSTOR.calls)))
        item.user_properties.append((ALTERED, str(IMPOSTOR.altered)))
        counted = (CALLS, ALTERED, ALTERABLE) if IMPOSTOR.mode == CONTROL else (CALLS, ALTERED)
        for name in counted:
            for step in STEPS:
                item.user_properties.append((f"{name}.{step}", str(IMPOSTOR.by_step[name][step])))
