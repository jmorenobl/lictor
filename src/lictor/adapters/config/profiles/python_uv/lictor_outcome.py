"""Tells, for each failed test, how it failed, in which kind of step, and which scenario it ran.

Lictor copies this file into the verification environment, so it runs where Lictor isn't installed
and must import nothing of it; it runs in the project's interpreter, so it stays valid back to
Python 3.9. It reports facts as JUnit properties and decides nothing: which
failures count as a valid red is up to the profile. It runs in the same process as the code under
test, which could forge them, as it could forge the whole report.
"""

from __future__ import annotations

import ast
import contextlib
import inspect
import os
import sys
import tokenize
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from types import CodeType, TracebackType

FAILURE = "lictor.failure"
ORIGIN = "lictor.origin"
FRAME = "lictor.frame"
LINE = "lictor.line"
SCENARIO = "lictor.scenario"
FEATURE = "lictor.feature"
STEP = "lictor.step"
STATEMENT = "lictor.statement"

# The exception a Gherkin step raised, the kind of that step and the code of its function, real
# one first. The report records them only for that same exception: the hook also sees an `xfail`
# raised in a step, which fails nothing.
_STEP_ERROR = pytest.StashKey[tuple[BaseException, str, tuple[Any, ...]]]()


def _relative(path: object, root: Path) -> str:
    """Return `path` relative to the project, or "" if it lies outside it.

    pytest gives a file that exists as an absolute path; one that doesn't, as the relative path a
    compiled extension names its source by (`src/lxml/parser.pxi`), is of no place, and resolving
    it against the working directory would put it inside the project.
    """
    candidate = Path(str(path))
    if not candidate.is_absolute():
        return ""
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


def _impostor_error() -> type[BaseException] | None:
    """The error the profile's impostor raises in place of the interface's, if it loaded."""
    module = sys.modules.get("lictor_impostor")
    error = getattr(module, "ImpostorError", None)
    return error if isinstance(error, type) and issubclass(error, BaseException) else None


def failure_kind(excinfo: pytest.ExceptionInfo[BaseException], when: str) -> str:
    """Classify by the exception's class, never by its text."""
    if when != "call":
        return "error"
    if excinfo.errisinstance(NotImplementedError):
        return "not_implemented"
    impostor = _impostor_error()
    if impostor is not None and excinfo.errisinstance(impostor):
        return "impostor"
    if excinfo.errisinstance((AssertionError, pytest.fail.Exception)):
        return "assertion"
    return "error"


# Lictor sets it in every run of the acceptance, and only there (S10 of 0.25.0).
ACCEPTANCE_VARIABLE = "LICTOR_ACCEPTANCE"


def _in_the_acceptance() -> bool:
    return os.environ.get(ACCEPTANCE_VARIABLE) == "1"


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_timeout_set_timer(item: pytest.Item, settings: Any) -> bool | None:
    """In the acceptance, pytest-timeout sets no timer: Lictor cuts it by its own timeout.

    Its cut fails the test with `pytest.fail`, which reads as an assertion: a stub that hangs
    would pass for a valid red (S10). The hook is pytest-timeout's own, with `firstresult`, so
    returning True leaves its timer unset; without the plugin it is never called.
    """
    return True if _in_the_acceptance() else None


@pytest.hookimpl(tryfirst=True, optionalhook=True)
def pytest_timeout_cancel_timer(item: pytest.Item) -> bool | None:
    """The timer set above, which is none, is cancelled as none: pytest-timeout's stays idle."""
    return True if _in_the_acceptance() else None


@pytest.hookimpl(optionalhook=True)
def pytest_bdd_before_scenario(request: pytest.FixtureRequest, feature: Any, scenario: Any) -> None:
    """Record the scenario a test executes: its tags and its feature file."""
    root = request.config.rootpath
    request.node.user_properties.append((SCENARIO, " ".join(sorted(scenario.tags))))
    request.node.user_properties.append((FEATURE, _relative(feature.filename, root)))


@pytest.hookimpl(optionalhook=True)
def pytest_bdd_step_error(
    request: pytest.FixtureRequest, step: Any, step_func: Any, exception: BaseException
) -> None:
    """Keep the step a failure was raised in: `Given`, `When` or `Then`, «And» and «But» resolved,
    and the code of its function, to find its frame.

    It runs inside the runner's handling of that failure, so it does nothing that could raise.
    """
    request.node.stash[_STEP_ERROR] = (
        exception,
        str(getattr(step, "type", "")),
        _codes(step_func),
    )


def _codes(function: Any) -> tuple[Any, ...]:
    """The code of a step's function: the one a decorator wraps first, then its own."""
    codes: list[Any] = []
    with contextlib.suppress(Exception):  # a cycle of `__wrapped__`, or no code: the other may do
        codes.append(inspect.unwrap(function).__code__)
    own = getattr(function, "__code__", None)
    return tuple(codes + ([own] if own is not None and own not in codes else []))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, Any, None]:
    """Record the first failure of a test; the report reads the properties at teardown."""
    outcome = yield
    report = outcome.get_result()
    recorded = any(name == FAILURE for name, _ in item.user_properties)
    if report.failed and call.excinfo is not None and not recorded:
        root = item.config.rootpath
        origin = _relative(call.excinfo.traceback[-1].path, root)
        item.user_properties.append((FAILURE, failure_kind(call.excinfo, call.when)))
        item.user_properties.append((ORIGIN, origin))
        frame, line = _last_frame_inside(call.excinfo, root)
        item.user_properties.append((FRAME, frame))
        item.user_properties.append((LINE, line))
        item.user_properties.append((STEP, _failed_step(item, call.excinfo.value)))
        item.user_properties.append((STATEMENT, _failed_statements(item, call.excinfo, root)))


def _failed_step(item: pytest.Item, failure: BaseException) -> str:
    """The kind of step that raised the test's failure, or "" if no step raised it."""
    raised = item.stash.get(_STEP_ERROR, None)
    return raised[1] if raised is not None and raised[0] is failure else ""


def _failed_statements(
    item: pytest.Item, excinfo: pytest.ExceptionInfo[BaseException], root: Path
) -> str:
    """The statements the failure was raised in, from the step's own, or "" (F85).

    One per frame from the step function's, while each lies in the project: `file:line:fed`,
    joined by `;`, where `line` is the first of the innermost statement holding the frame's line
    and `fed` the lines of its function it feeds. It stops at a frame it can't name exactly: one
    outside the project, without source, or on a line with two statements.
    """
    raised = item.stash.get(_STEP_ERROR, None)
    if raised is None or raised[0] is not excinfo.value:
        return ""
    try:
        return ";".join(_chain(_frames(excinfo.tb), raised[2], root))
    except Exception:  # it runs while pytest reports the failure: nothing here may raise
        return ""


def _frames(tb: TracebackType | None) -> list[tuple[CodeType, int]]:
    found: list[tuple[CodeType, int]] = []
    while tb is not None:
        found.append((tb.tb_frame.f_code, tb.tb_lineno))
        tb = tb.tb_next
    return found


def _chain(frames: list[tuple[CodeType, int]], codes: tuple[Any, ...], root: Path) -> list[str]:
    """Each frame's statement from the step's own, up to the first outside the project; none at
    all if a frame of the project can't be named, since a shorter chain joins more easily."""
    start = next(
        (i for code in codes for i, (frame, _) in enumerate(frames) if frame is code), None
    )
    chain: list[str] = []
    for code, line in frames[start:] if start is not None else []:
        inside = _relative(code.co_filename, root)
        if not inside:
            break
        named = _statement_at(code.co_filename, line) if line else None
        if named is None:
            return []
        first, fed = named
        chain.append(f"{inside}:{first}:{','.join(str(n) for n in sorted(fed))}")
    return chain


_TREES: dict[str, ast.Module | None] = {}


def _tree(path: str) -> ast.Module | None:
    if path not in _TREES:
        try:
            with tokenize.open(path) as source:
                _TREES[path] = ast.parse(source.read(), path)
        except (OSError, SyntaxError, ValueError):
            _TREES[path] = None
    return _TREES[path]


_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_FUNCTIONS, ast.ClassDef)
# What holds statements without being one: a handler of `try`, a case of `match` (from 3.10).
_HOLDERS: tuple[type, ...] = (ast.excepthandler,) + (
    (ast.match_case,) if hasattr(ast, "match_case") else ()
)


def _child_statements(node: ast.AST) -> Iterator[ast.stmt]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            yield child
        elif isinstance(child, _HOLDERS):
            yield from _child_statements(child)


def _statement_at(path: str, line: int) -> tuple[int, frozenset[int]] | None:
    """The first line of the innermost statement holding `line`, and the lines it feeds."""
    tree = _tree(path)
    found = _innermost(tree, line) if tree is not None else None
    if found is None:
        return None
    statement, function = found
    fed = _fed_by(statement, function) if function is not None else frozenset()
    return statement.lineno, fed


def _innermost(tree: ast.Module, line: int) -> tuple[ast.stmt, ast.AST | None] | None:
    """The innermost statement holding `line` and the function it is in, or None: none holds it,
    or two do on that line (`a; b`, `if x: assert y`), and which one raised can't be told by it."""
    statement: ast.stmt | None = None
    function: ast.AST | None = None
    holding = _holding(tree, line)
    while len(holding) == 1:
        statement = holding[0]
        function = statement if isinstance(statement, _FUNCTIONS) else function
        holding = _holding(statement, line)
        if holding and holding[0].lineno == statement.lineno:
            return None  # its body starts on its own line
    if holding or statement is None:
        return None
    return statement, function


def _holding(node: ast.AST, line: int) -> list[ast.stmt]:
    """The statements right inside `node` that hold `line`."""
    return [s for s in _child_statements(node) if s.lineno <= line <= (s.end_lineno or s.lineno)]


def _flat(statements: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Every statement of a function in the order of its source, without those of nested ones."""
    for statement in statements:
        yield statement
        if not isinstance(statement, _SCOPES):
            yield from _flat(list(_child_statements(statement)))


def _fed_by(statement: ast.stmt, function: ast.AST) -> frozenset[int]:
    """The statements after this one in its function that load a name it binds, followed from
    each to the next; a name bound again without being loaded stops there."""
    order = list(_flat(list(_child_statements(function))))
    position = next((i for i, s in enumerate(order) if s is statement), None)
    if position is None:
        return frozenset()
    live = _binds(statement)
    fed: set[int] = set()
    for later in order[position + 1 :]:
        if not live:
            break
        bound = _binds(later)
        if _loads(later) & live:
            fed.add(later.lineno)
            live |= bound
        else:
            live -= bound
    return frozenset(fed)


# What evaluates nothing by itself: a definition, and a `try` (from 3.11, `try` with `except*`).
_HEADLESS: tuple[type, ...] = (*_SCOPES, ast.Try) + (
    (ast.TryStar,) if hasattr(ast, "TryStar") else ()
)


def _header(statement: ast.stmt) -> list[ast.AST]:
    """What a statement evaluates by itself: a compound one, only its head."""
    if isinstance(statement, _HEADLESS):
        return []
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        return [statement.iter]
    if isinstance(statement, (ast.While, ast.If)):
        return [statement.test]
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return [item.context_expr for item in statement.items]
    if isinstance(statement, ast.Assert):
        return [statement.test]  # its message is evaluated only once the test failed
    subject = getattr(statement, "subject", None)  # `match`
    return [subject] if subject is not None else [statement]


def _names(nodes: list[ast.AST], context: type) -> set[str]:
    return {
        node.id
        for root in nodes
        for node in ast.walk(root)
        if isinstance(node, ast.Name) and isinstance(node.ctx, context)
    }


def _loads(statement: ast.stmt) -> set[str]:
    loaded = _names(_header(statement), ast.Load)
    if isinstance(statement, ast.AugAssign) and isinstance(statement.target, ast.Name):
        loaded.add(statement.target.id)  # `x += 1` reads `x`
    return loaded


def _binds(statement: ast.stmt) -> set[str]:
    """The simple names a statement binds: assigned, unpacked, `:=`, a `for` or a `with`."""
    targets: list[ast.AST] = []
    if isinstance(statement, ast.Assign):
        targets = list(statement.targets)
    elif isinstance(statement, (ast.AnnAssign, ast.AugAssign, ast.For, ast.AsyncFor)):
        targets = [statement.target]
    elif isinstance(statement, (ast.With, ast.AsyncWith)):
        targets = [item.optional_vars for item in statement.items if item.optional_vars]
    walrus = {
        node.target.id
        for root in _header(statement)
        for node in ast.walk(root)
        if isinstance(node, ast.NamedExpr)
    }
    return _names(targets, ast.Store) | walrus


def _last_frame_inside(excinfo: pytest.ExceptionInfo[BaseException], root: Path) -> tuple[str, str]:
    """The innermost frame of the failure inside the project, the test's own, and its line.

    It differs from the origin when pytest raises for the test, as `pytest.raises` that saw
    nothing does: the origin is then inside pytest, and so outside the project. The line counts
    from 1; both are "" when no frame lies inside the project.
    """
    for entry in reversed(excinfo.traceback):
        inside = _relative(entry.path, root)
        if inside:
            return inside, str(entry.lineno + 1)
    return "", ""
