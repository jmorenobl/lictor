"""What the profile's survey says of a project, as facts (ADR-0015).

The survey is a command of the profile that reads the files of HEAD, never runs them, and writes
what each verification command runs or reads, the files of tests, what those load and the other
files that use the project. Building a `SurveyReport` checks only that it can be one; which of its
facts is a problem is `blind_spots`'s to say.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from lictor.domain.reports import InvalidReport

LoadedKind = Literal["conftest", "module"]


@dataclass(frozen=True)
class LaunchedProgram:
    """The program a command launches through the environment, and where its PATH finds it.

    `path` is None when it finds none; `in_env` says whether what it found is the environment's.
    """

    name: str
    path: str | None
    in_env: bool


@dataclass(frozen=True)
class SurveyedCommand:
    """What one command runs or reads, or, when `understood` is False, why that isn't known."""

    id: str
    understood: bool
    reason: str = ""
    tests: bool = False
    paths: tuple[str, ...] = ()
    selects: bool = False
    program: LaunchedProgram | None = None


@dataclass(frozen=True)
class ProjectImport:
    """A line of a file that imports the project's code, and what it takes from it."""

    line: int
    module: str
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurveyedTests:
    """A file of tests, how many it defines, and the commands that run it."""

    path: str
    tests: int
    run_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadedFile:
    """A file the tests load without running it as a test: a `conftest.py`, or what they import."""

    path: str
    kind: LoadedKind
    by: tuple[str, ...]
    imports: tuple[ProjectImport, ...] = ()


@dataclass(frozen=True)
class CodeUser:
    """Another file that imports the project, and the commands that name a path above it."""

    path: str
    read_by: tuple[str, ...]
    imports: tuple[ProjectImport, ...]


@dataclass(frozen=True)
class SurveyReport:
    """Everything the survey saw of HEAD, and what it skipped."""

    code_dir: str
    modules: tuple[str, ...]
    commands: tuple[SurveyedCommand, ...]
    tests: tuple[SurveyedTests, ...] = ()
    loaded: tuple[LoadedFile, ...] = ()
    code_users: tuple[CodeUser, ...] = ()
    projects: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()


class _BadSurveyError(Exception):
    """Why the data can't be a survey; it becomes the `InvalidReport`'s reason."""


def _map(raw: object, what: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise _BadSurveyError(f"{what} no es un objeto")
    return raw


def _list(raw: object, what: str) -> Sequence[object]:
    if not isinstance(raw, list):
        raise _BadSurveyError(f"{what} no es una lista")
    return raw


def _text(raw: object, what: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise _BadSurveyError(f"{what} no es un texto")
    return raw


def _path(raw: object, what: str) -> str:
    """A path of the tree: relative, with no way out of it."""
    text = _text(raw, what)
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        raise _BadSurveyError(f"{what} sale del árbol: {text!r}")
    return text


def _texts(raw: object, what: str) -> tuple[str, ...]:
    return tuple(_text(item, what) for item in _list(raw, what))


def _paths(raw: object, what: str) -> tuple[str, ...]:
    return tuple(_path(item, what) for item in _list(raw, what))


def _flag(raw: object, what: str) -> bool:
    if not isinstance(raw, bool):
        raise _BadSurveyError(f"{what} no es verdadero ni falso")
    return raw


def _count(raw: object, what: str, minimum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < minimum:
        raise _BadSurveyError(f"{what} no es un número válido: {raw!r}")
    return raw


def _program(raw: object, what: str) -> LaunchedProgram | None:
    if raw is None:
        return None
    data = _map(raw, what)
    found = data.get("path")
    return LaunchedProgram(
        name=_text(data.get("name"), f"{what}.name"),
        path=None if found is None else _text(found, f"{what}.path"),
        in_env=_flag(data.get("in_env"), f"{what}.in_env") and found is not None,
    )


def _command(raw: object, index: int) -> SurveyedCommand:
    what = f"el comando {index}"
    data = _map(raw, what)
    cid = _text(data.get("id"), f"{what}.id")
    status = data.get("status")
    program = _program(data.get("program"), f"{what}.program")
    if status == "unknown":
        reason = _text(data.get("reason"), f"{what}.reason")
        return SurveyedCommand(cid, understood=False, reason=reason, program=program)
    if status != "understood":
        raise _BadSurveyError(f"{what} tiene un estado que no existe: {status!r}")
    return SurveyedCommand(
        cid,
        understood=True,
        tests=_flag(data.get("tests"), f"{what}.tests"),
        paths=_paths(data.get("paths"), f"{what}.paths"),
        selects=_flag(data.get("selects"), f"{what}.selects"),
        program=program,
    )


def _imports(raw: object, what: str) -> tuple[ProjectImport, ...]:
    found = []
    for index, item in enumerate(_list(raw, what)):
        data = _map(item, f"{what} {index}")
        found.append(
            ProjectImport(
                line=_count(data.get("line"), f"{what} {index}.line", 1),
                module=_text(data.get("module"), f"{what} {index}.module"),
                names=_texts(data.get("names", []), f"{what} {index}.names"),
            )
        )
    return tuple(found)


def _ids(raw: object, what: str, known: set[str]) -> tuple[str, ...]:
    ids = _texts(raw, what)
    strange = [cid for cid in ids if cid not in known]
    if strange:
        raise _BadSurveyError(f"{what} nombra comandos que no se le dieron: {', '.join(strange)}")
    return ids


def _test_file(raw: object, index: int, known: set[str]) -> SurveyedTests:
    what = f"el fichero de tests {index}"
    data = _map(raw, what)
    return SurveyedTests(
        path=_path(data.get("path"), f"{what}.path"),
        tests=_count(data.get("tests"), f"{what}.tests", 1),
        run_by=_ids(data.get("run_by"), f"{what}.run_by", known),
    )


def _loaded(raw: object, index: int, known: set[str]) -> LoadedFile:
    what = f"el fichero cargado {index}"
    data = _map(raw, what)
    kind = data.get("kind")
    if kind not in ("conftest", "module"):
        raise _BadSurveyError(f"{what} tiene un tipo que no existe: {kind!r}")
    by = _ids(data.get("by"), f"{what}.by", known)
    if not by:
        raise _BadSurveyError(f"{what} no lo carga ningún comando")
    return LoadedFile(
        path=_path(data.get("path"), f"{what}.path"),
        kind="conftest" if kind == "conftest" else "module",
        by=by,
        imports=_imports(data.get("imports", []), f"{what}.imports"),
    )


def _user(raw: object, index: int, known: set[str]) -> CodeUser:
    what = f"el fichero de código {index}"
    data = _map(raw, what)
    imports = _imports(data.get("imports"), f"{what}.imports")
    if not imports:
        raise _BadSurveyError(f"{what} no importa el proyecto")
    return CodeUser(
        path=_path(data.get("path"), f"{what}.path"),
        read_by=_ids(data.get("read_by"), f"{what}.read_by", known),
        imports=imports,
    )


def _unique(paths: Sequence[str], what: str) -> None:
    repeated = sorted({path for path in paths if paths.count(path) > 1})
    if repeated:
        raise _BadSurveyError(f"{what} repite {', '.join(repeated)}")


def _each[T](
    root: Mapping[str, object],
    key: str,
    parse: Callable[[object, int, set[str]], T],
    known: set[str],
) -> tuple[T, ...]:
    return tuple(parse(raw, index, known) for index, raw in enumerate(_list(root.get(key), key)))


def _commands(root: Mapping[str, object]) -> tuple[SurveyedCommand, ...]:
    raw = _list(root.get("commands"), "commands")
    commands = tuple(_command(item, index) for index, item in enumerate(raw))
    if len({command.id for command in commands}) != len(commands):
        raise _BadSurveyError("commands repite un id")
    return commands


def survey_report(data: object) -> SurveyReport | InvalidReport:
    """The facts of a survey's report, or why they can't be what the survey wrote."""
    try:
        root = _map(data, "el informe")
        code = _map(root.get("code"), "code")
        commands = _commands(root)
        known = {command.id for command in commands}
        tests = _each(root, "tests", _test_file, known)
        loaded = _each(root, "loaded", _loaded, known)
        users = _each(root, "code_users", _user, known)
        _unique(
            [*(t.path for t in tests), *(f.path for f in loaded), *(u.path for u in users)],
            "el informe",
        )
        skipped = _map(root.get("skipped"), "skipped")
        return SurveyReport(
            code_dir=_path(code.get("dir"), "code.dir"),
            modules=_texts(code.get("modules"), "code.modules"),
            commands=commands,
            tests=tests,
            loaded=loaded,
            code_users=users,
            projects=_paths(skipped.get("projects"), "skipped.projects"),
            unreadable=_paths(skipped.get("unreadable"), "skipped.unreadable"),
        )
    except _BadSurveyError as bad:
        return InvalidReport(str(bad))
