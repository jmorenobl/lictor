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
"""

from __future__ import annotations

import configparser
import importlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

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
    inipath = early_config.inipath
    if inipath is None:
        return
    for entry in declared_pythonpath(Path(inipath)):
        sys.path.append(str(_inside(root, Path(inipath).parent / entry, entry)))


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
    for name in ("tomllib", "tomli"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise _usage_error(f"no hay con qué leer {inipath.name} en este intérprete")


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
