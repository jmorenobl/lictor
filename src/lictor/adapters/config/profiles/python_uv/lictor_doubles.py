"""What a stub returns in a run of the stubs' impostors, besides None and True (ADR-0012).

Lictor copies this file into the verification environment, so it runs where Lictor isn't installed
and must import nothing of it. A stub `raise NotImplementedError` becomes
`return __import__("lictor_doubles").stub("ONE")` (or `"EMPTY"`, or `"True"`), so that a test that
reads an attribute, an index or each element of what the interface returns reaches its own
assertion instead of failing on `None.vence`. In an `__init__` every constant is None, the only
value Python lets it return (F68). It is also a pytest plugin, only to count each test's operations
from nothing.

- **What a test does with a double gives another double:** an attribute, an index, a call or an
  arithmetic operation gives a child of the same kind, one level deeper. Each name and each key
  has its own child.
- **It equals nothing but itself.** `float()` gives nan, `int()` a number no scenario expects, and
  formatting gives its repr. `ONE` says yes to every order (`<`, `>`…), `EMPTY` no.
- **It is finite:** deeper than `DEPTH`, or after `BUDGET` operations in one test, it raises
  `LictorDoubleExhaustedError`, so neither a chain of attributes nor a loop that doesn't go deeper
  (`while r.pending:`) goes on forever.
- **It has no special names** (`__dataclass_fields__`…), which pytest asks for when it explains a
  failure, and it copies and pickles as itself.

It runs in the same process as the code under test, which could see it and behave otherwise.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterator

import pytest

DEPTH = 8
BUDGET = 10_000
_UNLIKELY = -7_919_000_013  # what int() gives: no scenario expects it


class _Budget:
    """How many operations the doubles have answered in the test in progress."""

    spent = 0


class LictorDoubleExhaustedError(Exception):
    """A test followed a double further than any real result goes."""


def _the_one() -> ReturnedByLictor:
    return ONE


def _the_empty() -> ReturnedByLictor:
    return EMPTY


class ReturnedByLictor:
    """What a stub returns in place of its result: equal to no value a test expects."""

    __slots__ = ("_children", "_empty", "_level")
    _empty: bool
    _level: int
    _children: dict[object, ReturnedByLictor]

    def __init__(self, empty: bool, level: int = 0) -> None:
        object.__setattr__(self, "_empty", empty)
        object.__setattr__(self, "_level", level)
        object.__setattr__(self, "_children", {})

    def _spend(self) -> None:
        _Budget.spent += 1
        if _Budget.spent > BUDGET:
            raise LictorDoubleExhaustedError(
                "demasiadas operaciones sobre un valor que puso Lictor en lugar de un stub"
            )

    def _child(self, key: object) -> ReturnedByLictor:
        self._spend()
        if self._level >= DEPTH:
            raise LictorDoubleExhaustedError(f"{self!r} no tiene nada más dentro")
        children = self._children
        try:
            child = children.get(key)
        except TypeError:  # a key a dict can't hold
            key, child = "?", children.get("?")
        if child is None:
            child = ReturnedByLictor(self._empty, self._level + 1)
            children[key] = child
        return child

    def __getattr__(self, name: str) -> ReturnedByLictor:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return self._child(("attr", name))

    def __setattr__(self, name: str, value: object) -> None:
        return None

    def __getitem__(self, key: object) -> ReturnedByLictor:
        return self._child(("item", key))

    def __call__(self, *args: object, **kwargs: object) -> ReturnedByLictor:
        return self._child("call")

    def _operation(self, other: object = None) -> ReturnedByLictor:
        return self._child("op")

    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = _operation
    __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = _operation
    __mod__ = __rmod__ = __pow__ = __rpow__ = _operation

    def __neg__(self) -> ReturnedByLictor:
        return self._operation()

    __pos__ = __abs__ = __neg__

    def __round__(self, ndigits: object = None) -> ReturnedByLictor:
        return self._operation()

    def _order(self, other: object) -> bool:
        self._spend()
        return not self._empty

    __lt__ = __le__ = __gt__ = __ge__ = _order

    def __float__(self) -> float:
        return math.nan

    def __int__(self) -> int:
        return _UNLIKELY

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __copy__(self) -> ReturnedByLictor:
        return self

    def __deepcopy__(self, memo: object) -> ReturnedByLictor:
        return self

    def __reduce__(self) -> tuple[object, tuple[()]]:
        return (_the_empty if self._empty else _the_one, ())

    def __iter__(self) -> Iterator[ReturnedByLictor]:
        return iter(() if self._empty else (self._child("iter"),))

    def __len__(self) -> int:
        self._spend()
        return 0 if self._empty else 1

    def __bool__(self) -> bool:
        self._spend()
        return not self._empty

    def __repr__(self) -> str:
        return "<doble vacío de Lictor>" if self._empty else "<doble de Lictor>"


ONE = ReturnedByLictor(empty=False)
EMPTY = ReturnedByLictor(empty=True)


_CONSTANTS: dict[str, object] = {"True": True, "ONE": ONE, "EMPTY": EMPTY}


def stub(name: str) -> object:
    """What a stub returns in this run: the constant `name`, or None from an `__init__`.

    An `__init__` that returns anything but None raises `TypeError` where it is called, a Given or
    a When, so no test could reach its Then: the driver was rejected whatever its tests (F68). Its
    object then has no attributes, and its methods, stubs too, return the constant.
    """
    if sys._getframe(1).f_code.co_name == "__init__":
        return None
    return _CONSTANTS[name]


def reset() -> None:
    """Count the operations of a new test from nothing."""
    _Budget.spent = 0


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Count each test's operations from nothing."""
    reset()
