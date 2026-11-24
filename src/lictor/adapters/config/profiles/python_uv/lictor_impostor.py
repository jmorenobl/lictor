"""Alters what the interface returns to the driver, to prove a new test checks it (ADR-0011).

Lictor copies this file into the verification environment, so it runs where Lictor isn't installed
and must import nothing of it; it runs in the project's interpreter, so it stays valid back to
Python 3.9. Without its variable it does nothing. With it, it wraps, as each one is imported, the
functions, coroutine functions and methods of the modules under the writable paths, outside the
driver: a call from a file of the driver runs the real function, and in every mode but the control
one its result is altered (a coroutine's when it is awaited), but only when the call was made from
a step of a kind the variable names (`steps`: Lictor alters the When and the Then, never what a
Given or a fixture prepares). A fixture counts as outside any step even when a step requests it.
What the driver hands back to the interface of what it altered goes back real: the alteration is
the driver's to see.

It also counts the calls the driver made through a library, never altering them: the project's
own code calling the interface after the driver entered it through a library (a runner that
invokes a command), and a library calling a function the driver handed it. It reports, for each
test, how many calls of each kind there were and how many of their results it altered, in all and
by the kind of Gherkin step that made them, how many of those altered results were None, and the
libraries the driver called into, as JUnit properties, and decides nothing. A function whose stub
Lictor replaced (`stubs`, by file and line) isn't wrapped: what it returns is a constant of
Lictor's, not code that already exists (ADR-0012).

It runs in the same process as the code under test, which could see it and behave otherwise, as it
could forge the whole report.
"""

from __future__ import annotations

import asyncio.coroutines
import datetime
import dis
import functools
import importlib.abc
import importlib.machinery
import inspect
import json
import math
import os
import sys
import sysconfig
import types
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator
    from typing import TypeGuard  # only read by the type checker: 3.9 lacks it

VARIABLE = "LICTOR_IMPOSTOR"
CALLS = "lictor.interface.calls"
ALTERED = "lictor.interface.altered"
ALTERED_NONE = "lictor.interface.altered_none"  # altered results that were None: an order's
VIA = "lictor.interface.via"  # the project calling it, entered through a library: never altered
HANDED = "lictor.interface.handed"  # a library calling what the driver handed it: never altered
ENTRY = "lictor.interface.entry"  # by step: the libraries the driver called into, to name them
STEPS = ("given", "when", "then")
CONTROL = "control"
MORE = "mas"  # one more: number + 1, one element more, the exception swallowed
LESS = "menos"  # one less: number - 1, one element less, another exception in its place


class ImpostorError(Exception):
    """Raised in place of the interface's own exception: a test expecting that one must fail."""


class AlteredByLictor:
    """What a call returns in place of None: equal to nothing but itself.

    It also fills an empty collection. Its name is what a test that trips on it reads in the
    error (`'AlteredByLictor' object has no attribute ...`), so it says where it came from.
    """

    def __repr__(self) -> str:
        return "<impostor de Lictor>"


SENTINEL = AlteredByLictor()
_UNALTERED = object()
_HERE = __file__
_SHARED_INTS = range(-5, 257)  # the integers CPython keeps one object of
_RELAY = "_relayed"  # the frame that awaits a coroutine the code created, and knows who did


@functools.cache
def _real(path: str) -> str:
    return os.path.realpath(path)


# Python's own library: a module there only runs what it is given (`asyncio.run`).
_STDLIB = [_real(sysconfig.get_paths()["stdlib"])]


def _under(path: str, roots: Sequence[str]) -> bool:
    real = _real(path)
    return any(real == root or real.startswith(root + os.sep) for root in roots)


def _of_its_form(element: Any) -> Any:
    """An element like `element` that isn't equal to it when its kind is known, else itself.

    A collection grows with it: a test that reads each element (`[p.isbn for p in x]`) then fails
    by its assertion, not by an error on a foreign value, and a set of primitives still changes.
    """
    changed = alter(element, MORE)
    return element if changed is _UNALTERED else changed


def _shorter(value: Any) -> Any:
    """One element less: the first of a sequence, so that what its first one was changes too."""
    if isinstance(value, dict):
        return dict(list(value.items())[:-1]) if value else {SENTINEL: SENTINEL}
    if isinstance(value, (set, frozenset)):
        return type(value)(list(value)[1:]) if value else type(value)({SENTINEL})
    if isinstance(value, str):
        return value[:-1] if value else "·"
    if isinstance(value, bytes):
        return value[:-1] if value else b"\0"
    return value[1:] if value else type(value)([SENTINEL])


def _longer(value: Any) -> Any:
    """One element more, of the collection's own form; at the start of a sequence."""
    if isinstance(value, dict):
        return {**value, SENTINEL: _of_its_form(list(value.values())[-1]) if value else SENTINEL}
    if isinstance(value, (set, frozenset)):
        return type(value)({*value, SENTINEL})
    if isinstance(value, str):
        return value + "·"
    if isinstance(value, bytes):
        return value + b"\0"
    return type(value)([_of_its_form(value[-1]), *value] if value else [SENTINEL])


def alter(value: Any, mode: str) -> Any:
    """Return another value of the same kind, or `_UNALTERED` when the kind isn't known."""
    if value is None:
        return SENTINEL
    if isinstance(value, bool):
        return not value
    step = 1 if mode == MORE else -1
    if isinstance(value, float) and not math.isfinite(value):
        return _UNALTERED
    if isinstance(value, (int, float, Fraction, Decimal)):
        changed: Any = value + step
    elif isinstance(value, datetime.date):  # a datetime too: a day more or less
        changed = value + datetime.timedelta(days=step)
    elif type(value) in (str, bytes, list, tuple, dict, set, frozenset):
        changed = _longer(value) if mode == MORE else _shorter(value)
    else:
        return _UNALTERED
    try:
        same = bool(changed == value)
    except Exception:  # a comparison that fails can't say it is the same
        same = False
    return _UNALTERED if same else changed


def _returnable(value: Any) -> bool:
    """Whether a value the impostor produced can go back real when the driver hands it back.

    Only an immutable one: what the driver did to a list it got is the driver's, and handing the
    original back would undo it. And only one Python doesn't share, told apart by identity.
    """
    if isinstance(value, bool) or value is None:
        return False
    if type(value) is int:
        return value not in _SHARED_INTS
    if isinstance(value, (float, Decimal, Fraction, datetime.date)):
        return True
    if type(value) in (str, bytes, tuple, frozenset):
        return len(value) > 1
    return False


class _Impostor:
    """What one run of the acceptance was told: the mode, and where the boundary lies."""

    def __init__(
        self,
        mode: str,
        writable: Sequence[str],
        driver: Sequence[str],
        steps: Sequence[str],
        stubs: Mapping[str, Sequence[int]],
    ) -> None:
        self.mode = mode
        self.writable = [_real(root) for root in writable]
        self.driver = [_real(root) for root in driver]
        self.steps = frozenset(steps)
        # The lines Lictor put in place of a stub, by file: no code that already exists.
        self.stubs = {_real(path): frozenset(lines) for path, lines in stubs.items()}
        self.fixtures = 0  # how many fixtures are being set up right now
        self.reset()

    def reset(self) -> None:
        """Count a new test from nothing, outside any step."""
        self.totals = dict.fromkeys((CALLS, ALTERED, VIA, HANDED), 0)
        self.altered_none = 0
        self.step: str | None = None
        self.produced: list[tuple[Any, Any]] = []  # what it altered, and what it was
        self.by_step = {name: dict.fromkeys(STEPS, 0) for name in self.totals}
        self.entries: dict[str, set[str]] = {step: set() for step in STEPS}

    def where(self) -> str | None:
        """The kind of step a call is made from; a fixture's setup is outside any step."""
        return self.step if self.fixtures == 0 and self.step in STEPS else None

    def count(self, name: str, step: str | None) -> None:
        """Count one more `name` in `step`; outside a step only the total holds it."""
        self.totals[name] += 1
        if step is not None:
            self.by_step[name][step] += 1

    def wraps(self, path: str) -> bool:
        return _under(path, self.writable) and not _under(path, self.driver)

    def is_project(self, frame: Any) -> bool:
        """Code of the project, also what Python generates for it (a dataclass's __init__)."""
        if _under(frame.f_code.co_filename, self.writable):
            return True
        module_file = frame.f_globals.get("__file__")
        return isinstance(module_file, str) and _under(module_file, self.writable)

    def is_stub(self, func: Callable[..., Any]) -> bool:
        """Whether Lictor replaced this function's stub: what it returns proves nothing."""
        code = getattr(func, "__code__", None)
        if code is None:
            return False
        lines = self.stubs.get(_real(code.co_filename), frozenset())
        return any(line in lines for _, line in dis.findlinestarts(code))

    def through(self, frame: Any) -> str | None:
        """The library the driver called into, when this call reached the interface through it.

        It walks up from the caller to the first frame of the driver. A frame of the impostor
        means the call is inside one the driver made, already counted, except the one that awaits
        a coroutine the code created: a coroutine runs from the event loop, and the walk goes on
        from the frame that created it.
        """
        seen: list[Any] = []  # from the caller up to the driver, the driver's not included
        while frame is not None:
            path = frame.f_code.co_filename
            if path == _HERE:
                if frame.f_code.co_name != _RELAY:
                    return None
                frame = frame.f_locals.get("creator")
                continue
            if _under(path, self.driver):
                return self._entry(seen)
            seen.append(frame)
            frame = frame.f_back
        return None

    def _entry(self, seen: Sequence[Any]) -> str | None:
        """The library the driver called into, or None when it called the project itself.

        It names the first module, from the driver down, outside Python's own library.
        """
        if not seen or self.is_project(seen[-1]):
            return None
        names: list[tuple[str, str]] = []
        for frame in reversed(seen):
            if self.is_project(frame):
                break
            names.append((frame.f_code.co_filename, str(frame.f_globals.get("__name__", "?"))))
        outside = [name for path, name in names if not _under(path, _STDLIB)]
        return (outside or [names[0][1]])[0]

    def real(self, value: Any) -> Any:
        """What the interface gave, for a value of the impostor's that the driver hands back."""
        for altered, original in self.produced:
            if value is altered:
                return original
        return value

    def wrap(self, func: Callable[..., Any]) -> Callable[..., Any]:
        if self.is_stub(func):
            return func
        coroutine = inspect.iscoroutinefunction(func)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            caller = sys._getframe(1)
            if not _under(caller.f_code.co_filename, self.driver):
                self._through_a_library(caller)
                if coroutine:
                    return self._relayed(func(*args, **kwargs), caller)
                return func(*args, **kwargs)
            args = tuple(self.real(arg) for arg in args)
            kwargs = {name: self.real(arg) for name, arg in kwargs.items()}
            step = self.where()
            alters = self.mode != CONTROL and step in self.steps
            if coroutine:
                return self._awaited(func(*args, **kwargs), alters, step)
            self.count(CALLS, step)
            if not alters:
                return func(*args, **kwargs)
            return self._altered(func, args, kwargs, step)

        if coroutine:
            _mark_coroutine(wrapper)
        return wrapper

    def _through_a_library(self, caller: Any) -> None:
        """Count a call that isn't the driver's when the driver reached it through a library.

        The project calling the interface means the driver entered the project through that
        library (typer keeps the command it decorates before the impostor wraps it); a library
        calling it means the driver handed it the function. Neither is altered.
        """
        entry = self.through(caller)
        if entry is None:
            return
        step = self.where()
        self.count(VIA if self.is_project(caller) else HANDED, step)
        if step is not None:
            self.entries[step].add(entry)

    async def _relayed(self, awaited: Any, creator: Any) -> Any:
        """Await a coroutine the code created, from a frame that says who created it."""
        return await awaited

    async def _awaited(self, awaited: Any, alters: bool, step: str | None) -> Any:
        """What the coroutine gives when awaited, altered as a plain call's result if `alters`.

        It counts as a call once awaited, from the step it was created in: one never awaited
        returned nothing to the driver. It awaits it from here even unaltered, so that what the
        coroutine calls has a frame of the impostor below it, as what a plain call calls does.
        """
        self.count(CALLS, step)
        if not alters:
            return await awaited
        return self._altered(_awaited_value, (await _settled(awaited),), {}, step)

    def _altered(self, func: Callable[..., Any], args: Any, kwargs: Any, step: str | None) -> Any:
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            self.count(ALTERED, step)
            if self.mode == MORE:
                return None
            raise ImpostorError(f"{type(exc).__name__} sustituida por el impostor") from exc
        changed = alter(result, self.mode)
        if changed is _UNALTERED:
            return result
        self.count(ALTERED, step)
        if result is None:
            self.altered_none += 1
        if _returnable(changed):
            self.produced.append((changed, result))
        return changed


class _Raised:
    """What a coroutine raised, carried out of its await to be altered like a plain call's."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc


async def _settled(awaited: Any) -> Any:
    try:
        return await awaited
    except Exception as exc:
        return _Raised(exc)


def _awaited_value(value: Any) -> Any:
    if isinstance(value, _Raised):
        raise value.exc
    return value


def _mark_coroutine(wrapper: Callable[..., Any]) -> None:
    """Keep the wrapper a coroutine function to whoever asks: a library may call it otherwise.

    Before 3.12 only `asyncio.iscoroutinefunction` can be told; `inspect`'s says no.
    """
    mark = getattr(inspect, "markcoroutinefunction", None)
    if mark is not None:
        mark(wrapper)
        return
    marker = getattr(asyncio.coroutines, "_is_coroutine", None)
    if marker is not None:
        wrapper._is_coroutine = marker  # type: ignore[attr-defined]


def _plain(func: object) -> TypeGuard[types.FunctionType]:
    """A function or coroutine function to wrap: a generator's result is no single value."""
    return isinstance(func, types.FunctionType) and not (
        inspect.isgeneratorfunction(func) or inspect.isasyncgenfunction(func)
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
    # No default for `steps` nor `stubs`: what Lictor doesn't say, it mustn't be guessed here.
    return _Impostor(
        str(data["mode"]),
        list(data["writable"]),
        list(data["driver"]),
        list(data["steps"]),
        {str(path): list(lines) for path, lines in dict(data["stubs"]).items()},
    )


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


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup() -> Generator[None, Any, None]:
    """A fixture prepares, as a Given does, even when a step requests it: nothing it gets is
    altered. pytest-bdd resolves a step's fixtures after telling which step is in progress."""
    if IMPOSTOR is not None:
        IMPOSTOR.fixtures += 1
    try:
        yield
    finally:
        if IMPOSTOR is not None:
            IMPOSTOR.fixtures -= 1


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
) -> Generator[None, Any, None]:
    """Record what the test's call did; the report reads the properties at teardown."""
    yield
    if IMPOSTOR is not None and call.when == "call":
        for name, total in IMPOSTOR.totals.items():
            item.user_properties.append((name, str(total)))
            for step in STEPS:
                item.user_properties.append((f"{name}.{step}", str(IMPOSTOR.by_step[name][step])))
        item.user_properties.append((ALTERED_NONE, str(IMPOSTOR.altered_none)))
        for step in STEPS:
            entries = ",".join(sorted(IMPOSTOR.entries[step]))
            item.user_properties.append((f"{ENTRY}.{step}", entries))
