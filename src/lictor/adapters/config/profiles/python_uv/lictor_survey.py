"""What each verification command sees of the project, read from its files (ADR-0015).

Lictor copies this file into the verification environment and runs it in a clean clone of HEAD,
before a run and in `doctor`. It imports nothing of Lictor and nothing of the project: it reads
the files with `ast`, the commands with `shlex` and the configuration of pytest as pytest reads it,
and writes facts. Which of them is a problem is Lictor's to judge, not this module's.

    python -I -B -m lictor_survey src --in commands.json --out report.json

`src` is where the project's code lives. `commands.json` holds `{"commands": [{"id", "run"}],
"detect": [...]}`: every command a verification runs, and the files that make a directory a
project of its own. The report says, for each command, whether it is understood (and then the
paths it runs or reads, and whether it selects among its tests), not known, and the program it
launches through `uv run`, with where the environment finds it; each file of tests and who runs
it; each file the tests load (a `conftest.py`, or a module of the repository they import) with
what of the project it imports; each other file that imports the project, with who reads it; and
what it skipped. When in doubt a command is "unknown": a false warning teaches not to read them.

It runs in the project's interpreter, which may be older than Lictor's: valid back to Python 3.9.
"""

from __future__ import annotations

import ast
import configparser
import fnmatch
import importlib
import json
import os
import shlex
import shutil
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any


def _toml_module() -> Any:
    """`tomllib` from Python 3.11, or before it the `tomli` pytest installs; None without both."""
    for name in ("tomllib", "tomli"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


_toml = _toml_module()
SKIPPED_DIRS = {".git", ".lictor", "__pycache__", "node_modules", "venv", "build", "dist"}
DEFAULT_FILES = ["test_*.py", "*_test.py"]
# What may run anything, tests included: what they do can't be read off their line.
RUNS_ANYTHING = {
    "make",
    "tox",
    "nox",
    "sh",
    "bash",
    "zsh",
    "dash",
    "invoke",
    "just",
    "poe",
    "hatch",
    "pdm",
    "uv",
    "uvx",
    "env",
    "xargs",
    "unittest",
    "trial",
    "nosetests",
    "ward",
    "behave",
}
# Options of pytest, and of its usual plugins, that take the next token as their value.
VALUED = {
    "-k",
    "-m",
    "-n",
    "-p",
    "-o",
    "-W",
    "--junitxml",
    "--junit-xml",
    "--deselect",
    "--ignore",
    "--basetemp",
    "--maxfail",
    "--tb",
    "--durations",
    "--durations-min",
    "--dist",
    "--timeout",
    "--timeout-method",
    "--import-mode",
    "--log-level",
    "--log-file",
    "--cov-report",
    "--cov-fail-under",
    "--capture",
    "--color",
    "--code-highlight",
    "--junit-prefix",
    "--maxprocesses",
    "--max-worker-restart",
    "--asyncio-mode",
    "-r",
}
# Options that take no value. Anything else followed by a word is a value nobody knows.
FLAGS = {
    "-q",
    "-qq",
    "-v",
    "-vv",
    "-vvv",
    "-x",
    "-s",
    "-l",
    "-ra",
    "-rA",
    "-rf",
    "--quiet",
    "--verbose",
    "--exitfirst",
    "--strict-markers",
    "--strict-config",
    "--strict",
    "--lf",
    "--last-failed",
    "--ff",
    "--failed-first",
    "--sw",
    "--stepwise",
    "--nf",
    "--new-first",
    "--no-header",
    "--no-summary",
    "--showlocals",
    "--disable-warnings",
    "--disable-pytest-warnings",
    "--cov-branch",
    "--no-cov",
    "--no-cov-on-fail",
    "--runxfail",
    "--setup-show",
    "--doctest-modules",
    "--full-trace",
    "--force-sugar",
    "--cache-clear",
    "--lfnf",
}
# Those that make what pytest runs something this module can't read.
OPAQUE = {
    "-c",
    "--config-file",
    "--rootdir",
    "--confcutdir",
    "--pyargs",
    "--ignore-glob",
    "--continue-on-collection-errors",
    "--co",
    "--collect-only",
    "--cov",
    "--inifile",
}
SELECTS = {"-k", "-m", "--deselect", "--lf", "--last-failed", "--sw", "--stepwise"}
UV_VALUED = {
    "--with",
    "--with-editable",
    "--with-requirements",
    "--python",
    "-p",
    "--package",
    "--group",
    "--extra",
    "--env-file",
    "--directory",
    "--project",
    "--index",
    "--index-url",
    "--extra-index-url",
    "--no-group",
    "--only-group",
    "--cache-dir",
    "--color",
    "--config-file",
}


class UnknownError(Exception):
    """What a command does can't be read off it; the message says why."""


def _under(path: str, roots: Iterable[str]) -> bool:
    for given in roots:
        root = given.rstrip("/")
        if root in ("", "."):
            return True
        if path == root or path.startswith(root + "/"):
            return True
    return False


def _norm(path: str) -> str:
    parts = PurePosixPath(path.split("::", 1)[0]).parts
    clean = [part for part in parts if part not in (".", "")]
    return "/".join(clean) if clean else "."


def _walked(directory: str) -> bool:
    return directory not in SKIPPED_DIRS and not directory.startswith(".")


def _joined(rel: str, name: str) -> str:
    return name if rel == "." else f"{rel}/{name}"


class Tree:
    """The files of a project, as they are in the clone: nothing ignored is there."""

    def __init__(self, root: Path, detect: Sequence[str]) -> None:
        self.root = root
        self.projects: list[str] = []
        self.files: list[str] = []
        for current, dirs, names in os.walk(root):
            rel = Path(current).relative_to(root).as_posix()
            if rel != "." and any(name in names for name in detect):
                self.projects.append(rel)
                dirs[:] = []
                continue
            dirs[:] = sorted(d for d in dirs if _walked(d))
            self.files.extend(_joined(rel, name) for name in sorted(names) if name.endswith(".py"))
        self.unreadable: list[str] = []
        self._trees: dict[str, ast.Module | None] = {}

    def parse(self, rel: str) -> ast.Module | None:
        if rel not in self._trees:
            try:
                self._trees[rel] = ast.parse((self.root / rel).read_bytes(), filename=rel)
            except (SyntaxError, ValueError, OSError):
                self._trees[rel] = None
                self.unreadable.append(rel)
        return self._trees[rel]

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()


class PytestConfig:
    """The configuration pytest would read: its first file that has one, as pytest orders them."""

    def __init__(self, tree: Tree, start: str) -> None:
        self.testpaths: list[str] = []
        self.python_files: list[str] = list(DEFAULT_FILES)
        self.addopts: list[str] = []
        self.source: str | None = None
        directory = PurePosixPath(start)
        candidates = [directory, *directory.parents] if start != "." else [PurePosixPath(".")]
        for current in candidates:
            if self._read_dir(tree, current.as_posix()):
                return

    def _read_dir(self, tree: Tree, directory: str) -> bool:
        def rel(name: str) -> str:
            return name if directory in (".", "") else f"{directory}/{name}"

        for name in ("pytest.ini", ".pytest.ini"):
            if tree.exists(rel(name)):
                self._ini(tree, rel(name), "pytest", required=False)
                return True
        if tree.exists(rel("pyproject.toml")) and self._toml(tree, rel("pyproject.toml")):
            return True
        if tree.exists(rel("tox.ini")) and self._ini(tree, rel("tox.ini"), "pytest", True):
            return True
        return tree.exists(rel("setup.cfg")) and self._ini(
            tree, rel("setup.cfg"), "tool:pytest", required=True
        )

    def _ini(self, tree: Tree, rel: str, section: str, required: bool) -> bool:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string((tree.root / rel).read_text(encoding="utf-8"))
        except (configparser.Error, OSError, UnicodeDecodeError) as exc:
            raise UnknownError(f"no se puede leer {rel}: {exc}") from exc
        if not parser.has_section(section):
            if required:
                return False
            self.source = rel
            return True
        values = parser[section]
        self._set(rel, values.get("testpaths"), values.get("python_files"), values.get("addopts"))
        return True

    def _toml(self, tree: Tree, rel: str) -> bool:
        if _toml is None:
            text = (tree.root / rel).read_text(encoding="utf-8", errors="replace")
            if "[tool.pytest" in text:
                raise UnknownError(f"no hay con qué leer {rel} en este intérprete")
            return False
        try:
            data = _toml.loads((tree.root / rel).read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            raise UnknownError(f"no se puede leer {rel}: {exc}") from exc
        pytest_table = data.get("tool", {}).get("pytest")
        if not isinstance(pytest_table, dict):
            return False
        table = pytest_table.get("ini_options", pytest_table)
        if not isinstance(table, dict):
            return False
        self._set(rel, table.get("testpaths"), table.get("python_files"), table.get("addopts"))
        return True

    def _set(self, rel: str, testpaths: Any, python_files: Any, addopts: Any) -> None:
        self.source = rel
        if testpaths:
            self.testpaths = _words(testpaths)
        if python_files:
            self.python_files = _words(python_files)
        if addopts:
            self.addopts = list(addopts) if isinstance(addopts, list) else shlex.split(addopts)


def _words(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else str(value).split()


def _launch(tokens: list[str]) -> tuple[list[str], str | None]:
    """The command `uv run` launches, and its program; the same tokens when it isn't one."""
    if tokens[:2] == ["uv", "run"]:
        rest = tokens[2:]
        index = 0
        while index < len(rest) and rest[index].startswith("-"):
            option = rest[index]
            index += 2 if option in UV_VALUED else 1
            if option == "--":
                break
        launched = rest[index:]
        if not launched:
            raise UnknownError("uv run sin programa")
        return launched, launched[0]
    return tokens, None


class Command:
    """What one command runs or reads, or why that can't be known."""

    def __init__(self, cid: str, run: str, tree: Tree) -> None:
        self.id = cid
        self.status = "understood"
        self.reason = ""
        self.tests = False
        self.paths: list[str] = []
        self.ignored: list[str] = []
        self.selects = False
        self.program: dict[str, Any] | None = None
        self.python_files: list[str] = list(DEFAULT_FILES)
        self.rootdir = "."
        try:
            tokens = [_placeholder(t) for t in shlex.split(run)]
            launched, program = _launch(tokens)
            if program is not None:
                self.program = _resolve(program)
            self._read(launched, tree)
        except (UnknownError, ValueError) as exc:
            self.status, self.reason = "unknown", str(exc)
            self.tests, self.paths = False, []

    def _read(self, tokens: list[str], tree: Tree) -> None:
        if not tokens:
            raise UnknownError("no ejecuta nada")
        name = os.path.basename(tokens[0])
        if name in ("pytest", "py.test"):
            self._pytest(tokens[1:], tree)
        elif name.startswith("python"):
            self._python(tokens[1:], tree)
        else:
            _refuse_runners(name, tokens[1:2])
            self.paths = _named_paths(tokens[1:], tree)

    def _python(self, args: list[str], tree: Tree) -> None:
        module, rest = _module_run(args)
        if module == "pytest":
            self._pytest(rest, tree)
        elif module is not None and module.startswith("lictor_"):
            self.paths = _named_paths(rest, tree)
        else:
            raise UnknownError("python ejecuta un programa que no se lee aquí")

    def _pytest(self, args: list[str], tree: Tree) -> None:
        self.tests = True
        named = self._options(args)
        start = _common_dir([p for p in named if tree.exists(p)]) if named else "."
        config = PytestConfig(tree, start)
        if config.source is not None:
            self.rootdir = str(PurePosixPath(config.source).parent)
        self._options(config.addopts, from_config=True)
        self.python_files = config.python_files
        self.paths = named or (config.testpaths or ["."])

    def _options(self, args: list[str], from_config: bool = False) -> list[str]:
        """Read pytest's options, keeping what it runs, ignores and selects; return its paths."""
        named: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token.startswith("-"):
                index = self._option(args, index)
                continue
            if not from_config:
                named.append(_norm(token))
            index += 1
        return named

    def _note_ignored(self, option: str, value: str, following: str | None) -> None:
        path = _ignored_path(option, value, following)
        if path is not None:
            self.ignored.append(path)

    def _option(self, args: list[str], index: int) -> int:
        """Read the option at `index`, and return where the next one starts."""
        token = args[index]
        option, _, value = token.partition("=")
        following = args[index + 1] if index + 1 < len(args) else None
        if option in OPAQUE:
            raise UnknownError(f"{option} cambia lo que ejecuta pytest")
        self.selects = self.selects or option in SELECTS
        self._note_ignored(option, value, following)
        alone = not value and not _attached(token)
        if option in VALUED and alone:
            return index + 2
        if alone and option not in VALUED | FLAGS and _is_value(following):
            raise UnknownError(f"no se sabe si {option} lleva un valor")
        return index + 1


def _module_run(args: list[str]) -> tuple[str | None, list[str]]:
    """The module `python -m` runs, after the interpreter's own flags, and its arguments."""
    index = 0
    while index < len(args) and args[index].startswith("-") and args[index] != "-m":
        if args[index] == "-c":
            raise UnknownError("python -c ejecuta código que no se lee aquí")
        index += 1
    if index + 1 < len(args) and args[index] == "-m":
        return args[index + 1], args[index + 2 :]
    return None, []


def _refuse_runners(name: str, first: list[str]) -> None:
    """A program that may run tests, or anything else, can't be read off its line."""
    if name == "coverage" and first == ["run"]:
        raise UnknownError("coverage run ejecuta otro programa")
    if name in RUNS_ANYTHING:
        raise UnknownError(f"{name} puede ejecutar cualquier cosa")


def _named_paths(args: list[str], tree: Tree) -> list[str]:
    """The arguments of a program that runs no tests which are paths of the tree."""
    return [_norm(t) for t in args if not t.startswith("-") and tree.exists(t)]


def _ignored_path(option: str, value: str, following: str | None) -> str | None:
    if option != "--ignore":
        return None
    given = value or following
    return _norm(given) if given else None


def _is_value(token: str | None) -> bool:
    return token is not None and not token.startswith("-")


def _placeholder(token: str) -> str:
    """A report's path stays a value of its option: dropping it would give that option the next."""
    return "<informe>" if "{out}" in token or "{in}" in token else token


def _attached(token: str) -> bool:
    """A short option with its value glued on: `-kfoo`, `-pno:cacheprovider`, `-rA`."""
    return not token.startswith("--") and token[:2] in VALUED and token[2:] != ""


def _common_dir(paths: list[str]) -> str:
    dirs = [p if not p.endswith(".py") else str(PurePosixPath(p).parent) for p in paths]
    if not dirs:
        return "."
    common = os.path.commonpath(dirs) if len(dirs) > 1 else dirs[0]
    return common or "."


def _resolve(program: str) -> dict[str, Any]:
    """Where the commands' PATH finds a program, and whether that is inside this environment."""
    found = shutil.which(program)
    if found is None:
        return {"name": program, "path": None, "in_env": False}
    prefix = os.path.realpath(sys.prefix)
    real = os.path.realpath(found)
    in_env = os.path.abspath(found).startswith(os.path.abspath(sys.prefix) + os.sep)
    return {"name": program, "path": found, "in_env": in_env or real.startswith(prefix + os.sep)}


def _is_test_file(rel: str, patterns: Sequence[str]) -> bool:
    name = PurePosixPath(rel).name
    return any(fnmatch.fnmatch(rel if "/" in p else name, p) for p in patterns)


def _count_tests(module: ast.Module) -> int:
    count = 0
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            count += 1
        elif isinstance(node, ast.ClassDef) and (
            node.name.startswith("Test") or _is_testcase(node)
        ):
            count += sum(
                isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name.startswith("test")
                for m in node.body
            )
    return count


def _is_testcase(node: ast.ClassDef) -> bool:
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if name.endswith("TestCase"):
            return True
    return False


def _code_modules(tree: Tree, code: str) -> set[str]:
    base = tree.root / code
    if not base.is_dir():
        return set()
    modules = set()
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and not entry.name.startswith((".", "_")) and any(entry.rglob("*.py")):
            modules.add(entry.name)
        elif entry.suffix == ".py" and not entry.name.startswith("_"):
            modules.add(entry.stem)
    return modules


def _imports(module: ast.Module) -> list[tuple[int, str, list[str], int]]:
    """Every import: its line, the module, the names it takes and its level (0 if absolute)."""
    found: list[tuple[int, str, list[str], int]] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name, [], 0))
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            found.append((node.lineno, node.module or "", names, node.level))
    return found


def _project_imports(module: ast.Module, modules: set[str]) -> list[dict[str, Any]]:
    return [
        {"line": line, "module": name, "names": names}
        for line, name, names, level in _imports(module)
        if level == 0 and name.split(".")[0] in modules
    ]


def _basedir(rel: str, tree: Tree) -> str:
    """The first directory up from a test that isn't a package: pytest puts it in `sys.path`."""
    directory = PurePosixPath(rel).parent
    while directory.as_posix() not in (".", "") and tree.exists(f"{directory}/__init__.py"):
        directory = directory.parent
    return directory.as_posix()


def _module_file(base: str, dotted: str, tree: Tree) -> str | None:
    parts = dotted.split(".") if dotted else []
    stem = "/".join(([] if base in (".", "") else [base]) + parts)
    for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
        if candidate and tree.exists(candidate):
            return candidate.lstrip("/")
    return None


def _package(rel: str, level: int) -> str:
    """The directory a relative import of `level` dots starts from."""
    package = PurePosixPath(rel).parent
    for _ in range(level - 1):
        package = package.parent
    return package.as_posix()


def _repo_imports(rel: str, module: ast.Module, tree: Tree, code: str) -> list[str]:
    """The files of the repository, outside the code, that `rel` imports."""
    found: set[str] = set()
    bases = [_basedir(rel, tree), "."]
    for _line, name, names, level in _imports(module):
        roots = [_package(rel, level)] if level else bases
        found.update(_resolved(roots, _targets(name, names), tree))
    return sorted(p for p in found if not _under(p, [code]) and p != rel)


def _targets(name: str, names: list[str]) -> list[str]:
    """What an import may name as a module: its own, and each name it takes from it."""
    return [name] + [f"{name}.{n}" if name else n for n in names if n != "*"]


def _resolved(roots: list[str], targets: list[str], tree: Tree) -> set[str]:
    found = {_module_file(root, target, tree) for root in roots for target in targets}
    return {path for path in found if path is not None}


def survey(
    root: Path, code: str, commands: Sequence[dict[str, str]], detect: Sequence[str]
) -> dict[str, Any]:
    """Every fact of the report: the commands, the tests, what they load and the code's users."""
    tree = Tree(root, detect)
    modules = _code_modules(tree, code)
    parsed = [Command(c["id"], c["run"], tree) for c in commands]
    tests = _test_files(tree, code, parsed)
    loaded = _loaded_files(tree, code, parsed, tests)
    for entry in loaded.values():
        module = tree.parse(entry["path"])
        entry["imports"] = _project_imports(module, modules) if module is not None else []
    return {
        "code": {"dir": code, "modules": sorted(modules)},
        "commands": [_command_record(c) for c in parsed],
        "tests": sorted(tests.values(), key=lambda e: e["path"]),
        "loaded": sorted(loaded.values(), key=lambda e: e["path"]),
        "code_users": _code_users(tree, code, modules, parsed, set(tests) | set(loaded)),
        "skipped": {"projects": tree.projects, "unreadable": sorted(set(tree.unreadable))},
    }


def _test_files(tree: Tree, code: str, parsed: list[Command]) -> dict[str, dict[str, Any]]:
    """Each file of tests outside the code, with how many it defines and which commands run it."""
    patterns = sorted({p for c in parsed if c.tests for p in c.python_files} | set(DEFAULT_FILES))
    tests: dict[str, dict[str, Any]] = {}
    for rel in tree.files:
        count = _tests_in(tree, rel, code, patterns)
        if count:
            run_by = [c.id for c in parsed if c.tests and _runs(c, rel)]
            tests[rel] = {"path": rel, "tests": count, "run_by": run_by}
    return tests


def _tests_in(tree: Tree, rel: str, code: str, patterns: Sequence[str]) -> int:
    """How many tests a file outside the code defines, if it is named as a file of tests."""
    if _under(rel, [code]) or not _is_test_file(rel, patterns):
        return 0
    module = tree.parse(rel)
    return _count_tests(module) if module is not None else 0


def _loaded_files(
    tree: Tree, code: str, parsed: list[Command], tests: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """The `conftest.py` each command loads, and the repository's modules its tests import."""
    loaded = _conftests(tree, parsed)
    queue = [(rel, cid) for rel, e in tests.items() for cid in e["run_by"]]
    queue += [(rel, cid) for rel, e in loaded.items() for cid in e["by"]]
    seen: set[tuple[str, str]] = set()
    while queue:
        rel, cid = queue.pop()
        if (rel, cid) in seen:
            continue
        seen.add((rel, cid))
        for target in _imported_files(tree, rel, code, tests):
            _mark(loaded, target, "module", cid)
            queue.append((target, cid))
    return loaded


def _conftests(tree: Tree, parsed: list[Command]) -> dict[str, dict[str, Any]]:
    """Each `conftest.py` and the commands that load it."""
    loaded: dict[str, dict[str, Any]] = {}
    conftests = [rel for rel in tree.files if PurePosixPath(rel).name == "conftest.py"]
    for command in (c for c in parsed if c.tests):
        for rel in (r for r in conftests if _loads(command, r)):
            _mark(loaded, rel, "conftest", command.id)
    return loaded


def _imported_files(tree: Tree, rel: str, code: str, tests: dict[str, dict[str, Any]]) -> list[str]:
    """The files of the repository `rel` imports that aren't tests themselves."""
    module = tree.parse(rel)
    if module is None:
        return []
    return [path for path in _repo_imports(rel, module, tree, code) if path not in tests]


def _code_users(
    tree: Tree, code: str, modules: set[str], parsed: list[Command], taken: set[str]
) -> list[dict[str, Any]]:
    """The other files that import the project, with the commands that name a path above them."""
    users = []
    for rel in tree.files:
        module = None if _under(rel, [code]) or rel in taken else tree.parse(rel)
        imports = _project_imports(module, modules) if module is not None else []
        if imports:
            readers = [c.id for c in parsed if not c.tests and _under(rel, c.paths)]
            users.append({"path": rel, "read_by": readers, "imports": imports})
    return users


def _runs(command: Command, rel: str) -> bool:
    if _under(rel, command.ignored):
        return False
    for path in command.paths:
        if rel == path:
            return True
        if _under(rel, [path]) and _is_test_file(rel, command.python_files):
            return True
    return False


def _loads(command: Command, rel: str) -> bool:
    """A `conftest.py` pytest loads: above what it runs, up to its root, or inside it."""
    if _under(rel, command.ignored):
        return False
    directory = str(PurePosixPath(rel).parent)
    for path in command.paths:
        if _under(rel, [path]):
            return True
        if _under(path, [directory]) and _under(directory, [command.rootdir]):
            return True
    return False


def _mark(loaded: dict[str, dict[str, Any]], rel: str, kind: str, cid: str) -> None:
    entry = loaded.setdefault(rel, {"path": rel, "kind": kind, "by": []})
    if cid not in entry["by"]:
        entry["by"].append(cid)


def _command_record(command: Command) -> dict[str, Any]:
    record: dict[str, Any] = {"id": command.id, "status": command.status}
    if command.status == "unknown":
        record["reason"] = command.reason
    else:
        record.update(tests=command.tests, paths=command.paths, selects=command.selects)
    record["program"] = command.program
    return record


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    try:
        code = args[0]
        given = json.loads(Path(args[args.index("--in") + 1]).read_text(encoding="utf-8"))
        out = Path(args[args.index("--out") + 1])
    except (IndexError, ValueError, OSError) as exc:
        print(
            f"Uso: lictor_survey <código> --in <entrada.json> --out <informe.json> ({exc})",
            file=sys.stderr,
        )
        return 2
    report = survey(Path.cwd(), code, given.get("commands", []), given.get("detect", []))
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
