"""The project's own distribution as installing it would leave it, read from `pyproject.toml` (F82).

The verification environment never installs the project, on purpose: an editable install would
import the agent's tree, not the clone's. Code that reads its own metadata (`version("agentix")`, a
group of entry points of its own) then fails or finds nothing. `lictor_project_path` registers in
memory, inside pytest, the distribution this module reads; the survey reports the same reading, so
`doctor` tells what isn't registered and why (ADR-0019).

It reads and never runs: `tomllib` (or the `tomli` pytest needs before 3.11), `re` and `ast`, never
the build backend nor the project's code, and it never raises. What it can't read is a reason,
and nothing is registered: a version made up would let a test that compares it pass.

It runs in the project's interpreter, which may be older than Lictor's: valid back to Python 3.9.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from typing import Any

# hatchling's default (1.33.0), matched with MULTILINE.
HATCH_PATTERN = r"(?i)^(__version__|VERSION) *= *(['\"])v?(?P<version>.+?)\2"
GIT = "la versión sale de git"
NOT_INSTALLED = "uv no instala el proyecto, así que fuera de Lictor tampoco tiene metadata"
# What the person can do about any reason: the forms this module reads.
FIX = "dale a pyproject.toml un [build-system] y una versión estática, o leída de un fichero"
# The groups of `[project]` that are entry points, and the group each one becomes.
SCRIPT_TABLES = {"scripts": "console_scripts", "gui-scripts": "gui_scripts"}
# What the profile's plugins come from: a project's own is never announced as one.
PLUGIN_GROUP = "pytest11"


class Metadata:
    """What `importlib.metadata` gets to see of the project.

    `entry_points` maps each group to its entries (`name` → `module:attr`); `unread` names what the
    project leaves to its backend (`dynamic`) and is therefore missing from them.
    """

    def __init__(
        self,
        name: str,
        version: str,
        entry_points: dict[str, dict[str, str]],
        unread: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.version = version
        self.entry_points = entry_points
        self.unread = unread

    def metadata_text(self) -> str:
        return f"Metadata-Version: 2.1\nName: {self.name}\nVersion: {self.version}\n"

    def entry_points_text(self) -> str:
        sections = []
        for group, entries in self.entry_points.items():
            lines = [f"{name} = {value}" for name, value in entries.items()]
            sections.append("\n".join([f"[{group}]", *lines]))
        return "\n\n".join(sections) + "\n" if sections else ""

    def record(self) -> dict[str, Any]:
        """What the survey reports of it."""
        return {
            "name": self.name,
            "version": self.version,
            "groups": {group: len(entries) for group, entries in self.entry_points.items()},
            "unread": list(self.unread),
        }


class _UnreadableError(Exception):
    """Why the project's metadata isn't registered; it becomes the reason `read` returns."""


def normalized(name: str) -> str:
    """The name `importlib.metadata` compares: PEP 503, with `_` as separator."""
    return re.sub(r"[-_.]+", "_", name).lower()


def toml_module() -> Any:
    """`tomllib` from Python 3.11, or before it the `tomli` pytest installs; None without both."""
    for name in ("tomllib", "tomli"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


def read(root: Path) -> Metadata | str:
    """The distribution installing the project at `root` would give, or why there is none."""
    try:
        return _read(root)
    except _UnreadableError as reason:
        return str(reason)


def record(root: Path) -> dict[str, Any]:
    """The survey's fact: the distribution, or why it isn't registered and how to change it."""
    found = read(root)
    return {"problem": found, "fix": FIX} if isinstance(found, str) else found.record()


def _read(root: Path) -> Metadata:
    data = _pyproject(root)
    project = _table(data, "project", "pyproject.toml no tiene [project]")
    name = project.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _UnreadableError("[project] no tiene un nombre")
    _installed_by_uv(data)
    dynamic = _texts(project.get("dynamic", []), "[project].dynamic")
    version = _dynamic_version(data, root, name) if "version" in dynamic else project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise _UnreadableError("[project] no tiene una versión")
    unread = tuple(key for key in (*SCRIPT_TABLES, "entry-points") if key in dynamic)
    return Metadata(name, _normalized_version(version.strip()), _entry_points(project), unread)


def _pyproject(root: Path) -> dict[str, Any]:
    path = root / "pyproject.toml"
    toml = toml_module()
    if toml is None:
        raise _UnreadableError("no hay con qué leer pyproject.toml en este intérprete")
    try:
        data = toml.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _UnreadableError("no hay pyproject.toml") from None
    except (OSError, UnicodeDecodeError, toml.TOMLDecodeError) as exc:
        raise _UnreadableError(f"pyproject.toml no se puede leer: {exc}") from None
    return dict(data)


def _table(data: dict[str, Any], key: str, missing: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise _UnreadableError(missing)
    return value


def _get(data: Any, *keys: str) -> Any:
    """The value under `keys`, or None if any level is missing or isn't a table."""
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _texts(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _UnreadableError(f"{what} no es una lista de textos")
    return value


def _installed_by_uv(data: dict[str, Any]) -> None:
    """uv installs a project with `[build-system]`, unless `tool.uv.package` says otherwise."""
    package = _get(data, "tool", "uv", "package")
    if package is False:
        raise _UnreadableError(f"tool.uv.package = false: {NOT_INSTALLED}")
    if package is not True and not isinstance(data.get("build-system"), dict):
        raise _UnreadableError(f"pyproject.toml no tiene [build-system]: {NOT_INSTALLED}")


def _entry_points(project: dict[str, Any]) -> dict[str, dict[str, str]]:
    groups = {group: project.get(key, {}) for key, group in SCRIPT_TABLES.items()}
    others = project.get("entry-points", {})
    if not isinstance(others, dict):
        raise _UnreadableError("[project.entry-points] no es una tabla")
    groups.update(others)
    entry_points = {}
    for group, entries in groups.items():
        if not isinstance(entries, dict) or not all(isinstance(v, str) for v in entries.values()):
            raise _UnreadableError(
                f"el grupo {group} de los entry points no es una tabla de textos"
            )
        if entries and group != PLUGIN_GROUP:
            entry_points[group] = dict(entries)
    return entry_points


def _normalized_version(version: str) -> str:
    """The version as the backends write it (`1.0.0-dev`: `1.0.0.dev0`), if `packaging` is here."""
    try:
        packaging_version = importlib.import_module("packaging.version")
    except ImportError:
        return version
    try:
        return str(packaging_version.Version(version))
    except packaging_version.InvalidVersion:
        raise _UnreadableError(f"la versión {version} no es válida") from None


def _dynamic_version(data: dict[str, Any], root: Path, name: str) -> str:
    """The version a backend reads from a literal in a file; anything else is a reason."""
    backend = _get(data, "build-system", "build-backend")
    tool = data.get("tool")
    if not isinstance(tool, dict):
        tool = {}
    if backend == "hatchling.build":
        return _hatch_version(tool.get("hatch"), root)
    if backend in ("setuptools.build_meta", "setuptools.build_meta:__legacy__"):
        return _setuptools_version(tool, root)
    if backend == "flit_core.buildapi":
        module = _get(tool, "flit", "module", "name")
        return _module_version(root, str(module or name.replace("-", "_")), "__version__", {})
    if _get(tool, "pdm", "version", "source") == "scm":
        raise _UnreadableError(GIT)
    raise _UnreadableError(
        f"la versión es dinámica, y Lictor no sabe leerla de {backend or 'su backend'}"
    )


def _hatch_version(hatch: Any, root: Path) -> str:
    source = _get(hatch, "version", "source")
    if source == "vcs":
        raise _UnreadableError(GIT)
    if source not in (None, "regex"):
        raise _UnreadableError(f"hatch saca la versión con source = {source!r}, que ejecuta código")
    path = _get(hatch, "version", "path")
    if not isinstance(path, str):
        raise _UnreadableError(
            "la versión es dinámica, y tool.hatch.version no dice de qué fichero"
        )
    pattern = _get(hatch, "version", "pattern")
    if not isinstance(pattern, str):
        pattern = HATCH_PATTERN
    try:
        found = re.search(pattern, _file_text(root, path), re.MULTILINE)
    except re.error as exc:
        raise _UnreadableError(
            f"el pattern de tool.hatch.version no es una expresión válida: {exc}"
        ) from None
    if found is None or "version" not in found.groupdict():
        raise _UnreadableError(f"{path} no tiene la versión que busca hatch")
    return found.group("version")


def _setuptools_version(tool: dict[str, Any], root: Path) -> str:
    if "setuptools_scm" in tool:
        raise _UnreadableError(GIT)
    dynamic = _get(tool, "setuptools", "dynamic", "version")
    if isinstance(_get(dynamic, "file"), str):
        return _file_text(root, dynamic["file"]).strip()
    attr = _get(dynamic, "attr")
    if not isinstance(attr, str) or "." not in attr:
        raise _UnreadableError(
            "la versión es dinámica, y tool.setuptools.dynamic.version no da un fichero ni un attr"
        )
    module, _, name = attr.rpartition(".")
    package_dir = _get(tool, "setuptools", "package-dir")
    return _module_version(root, module, name, package_dir if isinstance(package_dir, dict) else {})


def _file_text(root: Path, rel: str) -> str:
    try:
        path, top = (root / rel).resolve(), root.resolve()
        if path != top and top not in path.parents:
            raise _UnreadableError(f"la versión se lee de {rel}, que está fuera del proyecto")
        return path.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        raise _UnreadableError(f"la versión se lee de {rel}, que no se puede leer: {exc}") from None


def _module_version(root: Path, module: str, name: str, package_dir: dict[str, Any]) -> str:
    """The literal a module assigns to `name` at its top level, as setuptools' `attr` reads it."""
    for path in _module_files(module, package_dir):
        try:
            source = _file_text(root, path)
        except _UnreadableError:
            continue
        value = _assigned(source, name)
        if isinstance(value, str):
            return value
        if isinstance(value, tuple) and value:
            return ".".join(str(part) for part in value)
        raise _UnreadableError(
            f"{path} no asigna a {name} un texto que se pueda leer sin ejecutarlo"
        )
    raise _UnreadableError(f"la versión se lee de {module}.{name}, y no se encuentra el módulo")


def _module_files(module: str, package_dir: dict[str, Any]) -> list[str]:
    """Where a module may be: under `package-dir` if the project declares it, else `src` or root."""
    parts = module.split(".")
    bases = [f"src/{module.replace('.', '/')}", module.replace(".", "/")]
    for prefix in sorted(package_dir, key=len, reverse=True):
        head = prefix.split(".") if prefix else []
        if parts[: len(head)] == head and isinstance(package_dir[prefix], str):
            bases = ["/".join([package_dir[prefix], *parts[len(head) :]]).lstrip("/")]
            break
    return [path for base in bases for path in (f"{base}.py", f"{base}/__init__.py")]


def _assigned(source: str, name: str) -> Any:
    """The first literal assigned to `name` among the module's own statements, or None."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(_named(t, name) for t in node.targets):
            return _literal(node.value)
        if isinstance(node, ast.AnnAssign) and node.value is not None and _named(node.target, name):
            return _literal(node.value)
    return None


def _named(target: ast.expr, name: str) -> bool:
    return isinstance(target, ast.Name) and target.id == name


def _literal(node: ast.expr) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
