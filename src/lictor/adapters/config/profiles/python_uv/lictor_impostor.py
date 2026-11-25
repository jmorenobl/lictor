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

It alters numbers, dates, booleans, texts, collections, None and exceptions, and objects of value
(a dataclass, a `NamedTuple`, a pydantic v2 model, an attrs class: every field at once, in a copy of
the same class built without running its code; a member of an `Enum` that isn't a `Flag`: the next
or the one before). Anything else, a service or a store, is state, and is left alone. It numbers the
driver's calls of each test and says which raised unaltered and which had their exception swallowed
or replaced: one that only raised altered was made to by a value of Lictor's the driver handed back
to the interface. With `trace` it also writes, for each call, its step, the function and a hash of
each argument, so that a run with a value of the scenario changed tells whether that value reached
the interface (ADR-0016).

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
import copy
import dataclasses
import datetime
import dis
import enum
import functools
import hashlib
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
RAISED_AT = "lictor.interface.raised_at"  # the driver's calls that raised, unaltered, by index
# Those whose exception the impostor swallowed or replaced, by index and step: `3:then`.
SWALLOWED_AT = "lictor.interface.swallowed_at"
TRACE = "lictor.interface.trace"  # each call of the driver's: its step, the function, its arguments
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
    if _of_value(value):
        return _altered_object(value, mode)
    changed = _altered_primitive(value, 1 if mode == MORE else -1, mode)
    if changed is _UNALTERED:
        return changed
    try:
        same = bool(changed == value)
    except Exception:  # a comparison that fails can't say it is the same
        same = False
    return _UNALTERED if same else changed


def _altered_primitive(value: Any, step: int, mode: str) -> Any:
    """A number one more or less, a date a day, a collection an element; else `_UNALTERED`."""
    if isinstance(value, float) and not math.isfinite(value):
        return _UNALTERED
    if isinstance(value, (int, float, Fraction, Decimal)):
        return value + step
    if isinstance(value, datetime.date):  # a datetime too: a day more or less
        return value + datetime.timedelta(days=step)
    if type(value) in (str, bytes, list, tuple, dict, set, frozenset):
        return _longer(value) if mode == MORE else _shorter(value)
    return _UNALTERED


_DEPTH = [0]  # how deep the object being altered is: its fields are altered as values too
_MAX_DEPTH = 4


def _of_value(value: Any) -> bool:
    """An object of value: a record of fields, or a member of an enumeration that isn't a flag.

    A combined `Flag` has no other member to give; an `IntFlag` is altered as the integer it is.
    """
    if isinstance(value, enum.Enum):
        return not isinstance(value, enum.Flag)
    return _record_fields(value) is not None


def _altered_object(value: Any, mode: str) -> Any:
    if isinstance(value, enum.Enum):
        return _other_member(value, mode)
    return _altered_record(value, mode)


def _other_member(value: Any, mode: str) -> Any:
    """Another member of the same enumeration, or `_UNALTERED` if it has only one."""
    kind: Any = type(value)
    members = list(kind)  # without aliases, which are the same member
    if len(members) <= 1:
        return _UNALTERED
    index = members.index(value) + (1 if mode == MORE else -1)
    return members[index % len(members)]


def _record_fields(value: Any) -> list[str] | None:
    """The fields of an object of value, or None: what its equality compares, in its order."""
    if isinstance(value, type):
        return None
    if dataclasses.is_dataclass(value):
        return [f.name for f in dataclasses.fields(value)]
    named = getattr(type(value), "_fields", None)  # a namedtuple
    if isinstance(value, tuple) and isinstance(named, tuple):
        return [str(name) for name in named]
    return _declared_fields(value)


def _declared_fields(value: Any) -> list[str] | None:
    """The fields a library declares for its class: a pydantic v2 model's, an attrs class's."""
    fields = getattr(type(value), "model_fields", None)
    if isinstance(fields, dict) and hasattr(value, "__pydantic_fields_set__"):
        return list(fields)
    attrs = getattr(type(value), "__attrs_attrs__", None)
    return None if attrs is None else [a.name for a in attrs]


def _altered_record(value: Any, mode: str) -> Any:
    """A copy of the object with every field it has altered, built without running its code."""
    names = _record_fields(value) or []
    if _DEPTH[0] >= _MAX_DEPTH:
        return _UNALTERED
    _DEPTH[0] += 1
    try:
        changes: dict[str, Any] = {}
        for name in names:
            field = _field(value, name)
            changed = _UNALTERED if field is _UNALTERED else alter(field, mode)
            if changed is not _UNALTERED:
                changes[name] = changed
    finally:
        _DEPTH[0] -= 1
    if not changes:
        return _UNALTERED
    if isinstance(value, tuple):
        named: Any = value  # a namedtuple, which builds its copy without running the class's code
        return named._replace(**changes)
    try:
        new = copy.copy(value)
        for name, changed in changes.items():
            object.__setattr__(new, name, changed)
    except Exception:
        return _UNALTERED
    return new


def _field(value: Any, name: str) -> Any:
    """A field of an object of value, or `_UNALTERED` when reading it fails."""
    try:
        return getattr(value, name)
    except Exception:  # a property that raises: there is nothing to alter in it
        return _UNALTERED


def _frozen(value: Any) -> bool:
    """Whether an object of value can't be changed: then it can go back real by identity."""
    params = getattr(type(value), "__dataclass_params__", None)
    if params is not None:
        return bool(params.frozen)
    if isinstance(value, tuple):
        return True
    config = getattr(type(value), "model_config", None)
    if isinstance(config, dict) and hasattr(value, "__pydantic_fields_set__"):
        return bool(config.get("frozen"))
    return False


def _pairs(changed: Any, original: Any) -> list[tuple[Any, Any]]:
    """What the impostor produced that can go back real, the fields of an object included.

    A mutable object is in the list too: it goes back real as long as the driver hasn't changed it
    (`_Impostor.real`), so that a method called on it runs on what the interface gave.
    """
    names = _record_fields(original)
    record = bool(names) and type(changed) is type(original)
    found = [(changed, original)] if _returnable(changed) or record else []
    if names and type(changed) is type(original):
        for name in names:
            mine, theirs = _field(changed, name), _field(original, name)
            if mine is not theirs and _UNALTERED not in (mine, theirs):
                found.extend(_pairs(mine, theirs))
    return found


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
    if _record_fields(value) is not None:
        return _frozen(value)
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
        *,
        trace: bool = False,
        root: str = "",
    ) -> None:
        self.mode = mode
        self.writable = [_real(root) for root in writable]
        self.driver = [_real(root) for root in driver]
        self.steps = frozenset(steps)
        # The lines Lictor put in place of a stub, by file: no code that already exists.
        self.stubs = {_real(path): frozenset(lines) for path, lines in stubs.items()}
        self.fixtures = 0  # how many fixtures are being set up right now
        self.trace_on = trace
        self.root = root  # the clone's root, which the trace leaves out of the texts it hashes
        self.reset()

    def reset(self) -> None:
        """Count a new test from nothing, outside any step."""
        self.totals = dict.fromkeys((CALLS, ALTERED, VIA, HANDED), 0)
        self.altered_none = 0
        self.step: str | None = None
        self.produced: list[tuple[Any, Any]] = []  # what it altered, and what it was
        # The fields of each mutable object it altered, by identity, as it gave them to the driver.
        self.snapshots: dict[int, tuple[int, ...]] = {}
        self.by_step = {name: dict.fromkeys(STEPS, 0) for name in self.totals}
        self.entries: dict[str, set[str]] = {step: set() for step in STEPS}
        self.trace: list[str] = []
        self.calls_made = 0  # the driver's calls so far, which numbers each one
        self.raised_at: list[int] = []  # driver calls that raised, unaltered
        self.swallowed_at: list[str] = []  # driver calls whose exception it altered, `index:step`
        self.step_index = -1  # which step of the test is in progress, from 0

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
                if _returnable(altered):
                    return original
                names = _record_fields(altered) or []
                made = self.snapshots.get(id(altered), ())
                now = tuple(id(getattr(altered, n, None)) for n in names)
                return original if now == made else value  # the driver changed it: it is its own
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
            if self.trace_on:
                self.trace.append(_signature(self, func, args, kwargs))
            index = self.calls_made
            self.calls_made += 1
            alters = self.mode != CONTROL and step in self.steps
            if coroutine:
                return self._awaited(func(*args, **kwargs), alters, step, index)
            self.count(CALLS, step)
            if not alters:
                try:
                    return func(*args, **kwargs)
                except Exception:
                    self.raised_at.append(index)
                    raise
            return self._altered(func, args, kwargs, step, index)

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

    async def _awaited(self, awaited: Any, alters: bool, step: str | None, index: int) -> Any:
        """What the coroutine gives when awaited, altered as a plain call's result if `alters`.

        It counts as a call once awaited, from the step it was created in: one never awaited
        returned nothing to the driver. It awaits it from here even unaltered, so that what the
        coroutine calls has a frame of the impostor below it, as what a plain call calls does.
        """
        self.count(CALLS, step)
        if not alters:
            try:
                return await awaited
            except Exception:
                self.raised_at.append(index)
                raise
        return self._altered(_awaited_value, (await _settled(awaited),), {}, step, index)

    def _altered(
        self, func: Callable[..., Any], args: Any, kwargs: Any, step: str | None, index: int
    ) -> Any:
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            self.swallowed_at.append(f"{index}:{step}")
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
        pairs = _pairs(changed, result)
        self.produced.extend(pairs)
        for altered, _ in pairs:
            names = _record_fields(altered)
            if names and not _returnable(altered):
                self.snapshots[id(altered)] = tuple(id(getattr(altered, n, None)) for n in names)
        return changed


_TRACE_DEPTH = 3  # how deep the trace looks into an argument
_BY_REPR = (bool, int, float, Decimal, Fraction, datetime.date, datetime.time, datetime.timedelta)


def _norm(value: Any, root: str, depth: int = 0) -> Any:
    """What of a value the trace hashes: equal values give the same, whatever object holds them.

    The clone's root is left out of texts, since each run has its own; an object that isn't of
    value counts only by its class, since its address changes from run to run.
    """
    if depth > _TRACE_DEPTH:
        return "…"
    names = _record_fields(value)
    if names is not None:
        fields = [_norm(_field(value, n), root, depth + 1) for n in names]
        return [type(value).__qualname__, *fields]
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        return text.replace(root, "<R>") if root else text
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return _norm_collection(value, root, depth)
    if value is None or isinstance(value, (*_BY_REPR, enum.Enum)):
        return repr(value)
    return type(value).__qualname__


def _norm_collection(value: Any, root: str, depth: int) -> Any:
    if isinstance(value, dict):
        return {str(_norm(k, root, depth + 1)): _norm(v, root, depth + 1) for k, v in value.items()}
    items = [_norm(v, root, depth + 1) for v in value]
    return sorted(map(str, items)) if isinstance(value, (set, frozenset)) else items


def _signature(impostor: _Impostor, func: Callable[..., Any], args: Any, kwargs: Any) -> str:
    """One call of the driver's: its step, what it called, and a short hash of each argument."""
    root = impostor.root
    parts = [_digest(a, root) for a in args]
    parts += [f"{k}={_digest(v, root)}" for k, v in sorted(kwargs.items())]
    name = hashlib.sha256(func.__qualname__.encode()).hexdigest()[:6]
    return f"{impostor.step_index}:{name}:{'.'.join(parts)}"


def _digest(value: Any, root: str) -> str:
    text = json.dumps(_norm(value, root), default=str, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()[:8]


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
        trace=bool(data.get("trace")),
        root=str(data.get("root", "")),
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
        IMPOSTOR.step_index += 1


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
        props = item.user_properties
        for name, total in IMPOSTOR.totals.items():
            props.append((name, str(total)))
            for step in STEPS:
                props.append((f"{name}.{step}", str(IMPOSTOR.by_step[name][step])))
        props.append((ALTERED_NONE, str(IMPOSTOR.altered_none)))
        for step in STEPS:
            props.append((f"{ENTRY}.{step}", ",".join(sorted(IMPOSTOR.entries[step]))))
        props.append((RAISED_AT, ",".join(map(str, IMPOSTOR.raised_at))))
        props.append((SWALLOWED_AT, ",".join(IMPOSTOR.swallowed_at)))
        if IMPOSTOR.trace_on:
            props.append((TRACE, ",".join(IMPOSTOR.trace)))
