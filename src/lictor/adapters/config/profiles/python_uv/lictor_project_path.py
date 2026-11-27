"""Adds the project sources, and the project's own `pythonpath`, to the end of the path.

Lictor copies this file into the verification environment, so it runs where Lictor isn't installed
and must import nothing of it. The hook runs after the interpreter's startup and after the plugins
are imported, and `append` keeps every project directory behind `site-packages`: project code can't
replace a dependency or run during startup.

The profile voids pytest's own `pythonpath` (`-o pythonpath=`), which pytest applies before
importing the plugins and in front of `site-packages`. What the project declares there is read
here from the file pytest chose (`config.inipath`, public since pytest 6.1) and appended after
`src`, so a suite whose tests import from `tests/support` still finds it (F77).

The module is the first plugin of the list, so it can also mark every other one for assertion
rewriting before any is imported, as pytest does with the plugins it loads by itself: otherwise a
plugin imported by an earlier one (`xdist.looponfail` imports `xdist.plugin`) warns that it can't be
rewritten, and a project whose warnings are errors can't even start (F81).

It also registers, at the end of `sys.meta_path`, the project's own distribution as installing it
would leave it, read by `lictor_metadata` from the clone's `pyproject.toml` without running anything
(F82, ADR-0019): `version("<project>")` and the project's own groups of entry points work inside
pytest, with the same reach as `src`. Only what uv would install is registered, never `pytest11`,
and never over a distribution of the same name the environment already has.
"""

from __future__ import annotations

import configparser
import importlib
import os
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from lictor.adapters.config.profiles.python_uv import lictor_metadata
else:
    # Its sibling in the environment; in Lictor's own tests, the module of its package.
    lictor_metadata = importlib.import_module(
        f"{__package__}.lictor_metadata" if __package__ else "lictor_metadata"
    )

PLUGIN_VARIABLE = "PYTEST_PLUGINS"
# The first pytest that reads `[tool.pytest]` and `pytest.toml` as its own configuration.
NATIVE_TOML_SINCE = 9


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    """Mark the plugins still to come for rewriting before the next one is imported."""
    # Before pytest 9 its `pythonpath` is a plugin of its own, and with `-o pythonpath=` and no
    # configuration file it fails an assertion (`inipath is not None`) before 8.1. Its only work
    # is what the profile voids, so blocking it changes nothing else; from 9 it no longer exists.
    pluginmanager.set_blocked("python_path")
    names = [name for name in os.environ.get(PLUGIN_VARIABLE, "").split(",") if name]
    pending = [name for name in names if name not in sys.modules]
    if pending:
        pluginmanager.rewrite_hook.mark_rewrite(*pending)


def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    """Make the sources importable for the conftests and the tests that come next."""
    root = early_config.invocation_params.dir
    sys.path.append(str(root / "src"))
    _append_declared(root, early_config.inipath)
    register_metadata(root)


def _append_declared(root: Path, inipath: Any) -> None:
    if inipath is None:
        return
    for entry in declared_pythonpath(Path(inipath)):
        sys.path.append(str(_inside(root, Path(inipath).parent / entry, entry)))


def register_metadata(root: Path) -> None:
    """Make the project's distribution visible to `importlib.metadata`, if it would have one.

    Registering is idempotent: a nested `pytest.main` of the same project replaces its finder
    instead of adding another (in 3.9 `entry_points()` gave every entry twice), and one of another
    project, or of none, leaves the outer one alone.
    """
    found = lictor_metadata.read(root)
    if isinstance(found, str):
        return
    key = lictor_metadata.normalized(found.name)
    for finder in [f for f in sys.meta_path if isinstance(f, ProjectFinder) and f.key == key]:
        sys.meta_path.remove(finder)
    try:
        importlib_metadata.distribution(found.name)
    except importlib_metadata.PackageNotFoundError:
        sys.meta_path.append(ProjectFinder(found, root))


class ProjectFiles:
    """What a distribution reads of its files, from what `lictor_metadata` read: no file behind it.

    It is a mixin, not a `Distribution`: the finder joins it to the `Distribution` of the library
    that asks, so the backport (`importlib_metadata` on 3.9) gets its own entry points, with the
    methods it calls on them, and not the standard library's.
    """

    def __init__(self, found: lictor_metadata.Metadata, root: Path) -> None:
        self._found = found
        self._root = root

    @property
    def name(self) -> str:
        return self._found.name

    @property
    def _normalized_name(self) -> str:
        # The backport (3.10 on Python 3.9) asks every distribution for it.
        return lictor_metadata.normalized(self._found.name)

    def read_text(self, filename: str) -> str | None:
        if filename == "METADATA":
            return self._found.metadata_text()
        if filename == "entry_points.txt":
            return self._found.entry_points_text()
        return None

    def locate_file(self, path: Any) -> Path:
        return self._root / "src" / str(path)


_DISTRIBUTIONS: dict[type, type] = {}


def _distribution_class(context: Any) -> type:
    """`ProjectFiles` joined to the `Distribution` of the library whose `Context` asks."""
    library = sys.modules.get(type(context).__module__)
    base = getattr(library, "Distribution", importlib_metadata.Distribution)
    if base not in _DISTRIBUTIONS:
        _DISTRIBUTIONS[base] = type("ProjectDistribution", (ProjectFiles, base), {})
    return _DISTRIBUTIONS[base]


class ProjectFinder(importlib_metadata.DistributionFinder):
    """Finds the project's distribution by its normalized name, and no module."""

    def __init__(self, found: lictor_metadata.Metadata, root: Path) -> None:
        self.found = found
        self.root = root
        self.key = lictor_metadata.normalized(found.name)

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> None:
        return None

    def find_distributions(self, context: Any = None) -> list[Any]:
        name = getattr(context, "name", None)
        if name is not None and lictor_metadata.normalized(name) != self.key:
            return []
        return [_distribution_class(context)(self.found, self.root)]


def declared_pythonpath(inipath: Path) -> list[str]:
    """Return the `pythonpath` entries of the configuration file pytest chose, as written."""
    if inipath.suffix == ".toml":
        value = _toml_value(inipath)
    else:
        section = "tool:pytest" if inipath.name == "setup.cfg" else "pytest"
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(inipath.read_text(encoding="utf-8"))
        value = parser.get(section, "pythonpath", fallback=None)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(entry) for entry in value]
    return str(value).split()


def _toml_value(inipath: Path) -> Any:
    toml = _toml_module(inipath)
    data = toml.loads(inipath.read_text(encoding="utf-8"))
    if inipath.name == "pyproject.toml":
        table = data.get("tool", {}).get("pytest", {})
        if isinstance(table.get("ini_options"), dict):
            table = table["ini_options"]
        elif _pytest_major() < NATIVE_TOML_SINCE:
            return None
    else:
        table = data.get("pytest", {})
    return table.get("pythonpath") if isinstance(table, dict) else None


def _pytest_major() -> int:
    return int(pytest.__version__.split(".")[0])


def _toml_module(inipath: Path) -> Any:
    """`tomllib` from Python 3.11, or before it the `tomli` pytest itself needs to read the file."""
    toml = lictor_metadata.toml_module()
    if toml is None:
        raise _usage_error(f"no hay con qué leer {inipath.name} en este intérprete")
    return toml


def _inside(root: Path, path: Path, entry: str) -> Path:
    resolved = path.resolve()
    if resolved != root.resolve() and root.resolve() not in resolved.parents:
        raise _usage_error(
            f"el pythonpath del proyecto nombra {entry}, que está fuera del proyecto: Lictor solo "
            "añade al final del path directorios del árbol que verifica"
        )
    return resolved


def _usage_error(message: str) -> Exception:
    return pytest.UsageError(message)
