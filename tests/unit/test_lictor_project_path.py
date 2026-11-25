"""What the path plugin of `python-uv` does with the project's `pythonpath` (F77) and plugins (F81).

Each case runs pytest in a subprocess the way verification does: no autoload, the plugins named in
`PYTEST_PLUGINS` with this one first, and `-o pythonpath=` voiding pytest's own. `-W error` makes
any warning the plugin's loading causes fail the suite, as with warnings as errors.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_project_path
from lictor.adapters.config.profiles.python_uv.lictor_project_path import declared_pythonpath

_PLUGIN_DIR = Path(lictor_project_path.__file__).parent

# Each configuration file pytest reads, with `pythonpath` naming `tests/support`.
_FORMATS: list[tuple[str, str, str]] = [
    ("pytest.ini", "pytest.ini", "[pytest]\npythonpath = tests/support\n"),
    (".pytest.ini", ".pytest.ini", "[pytest]\npythonpath = tests/support\n"),
    (
        "pyproject.toml ini_options",
        "pyproject.toml",
        '[tool.pytest.ini_options]\npythonpath = ["tests/support"]\n',
    ),
    (
        "pyproject.toml ini_options como texto",
        "pyproject.toml",
        '[tool.pytest.ini_options]\npythonpath = "tests/support"\n',
    ),
    ("pyproject.toml nativo", "pyproject.toml", '[tool.pytest]\npythonpath = ["tests/support"]\n'),
    ("pytest.toml", "pytest.toml", '[pytest]\npythonpath = ["tests/support"]\n'),
    ("tox.ini", "tox.ini", "[pytest]\npythonpath = tests/support\n"),
    ("setup.cfg", "setup.cfg", "[tool:pytest]\npythonpath = tests/support\n"),
    (
        "con --strict-config",
        "pytest.ini",
        "[pytest]\naddopts = --strict-config\npythonpath = tests/support\n",
    ),
]

_ORDER_TEST = """\
import sys
import helper


def test_support_is_behind_the_dependencies():
    packages = max(i for i, p in enumerate(sys.path) if p.endswith("site-packages"))
    support = [i for i, p in enumerate(sys.path) if p.endswith("support")]
    assert helper.VALUE == 2
    assert len(support) == 1
    assert support[0] > packages
"""


def _project(root: Path, config_name: str | None, config: str) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    (root / "tests" / "support").mkdir(parents=True)
    (root / "tests" / "support" / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    (root / "tests" / "test_pkg.py").write_text(
        "import pkg\n\n\ndef test_pkg():\n    assert pkg.X == 1\n", encoding="utf-8"
    )
    if config_name is not None:
        (root / config_name).write_text(config, encoding="utf-8")
    return root


def _pytest(
    root: Path, *args: str, plugins: str = "lictor_project_path"
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root),
        "PYTHONPATH": str(_PLUGIN_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_PLUGINS": plugins,
        "PYTEST_ADDOPTS": "-o pythonpath= -p no:cacheprovider",
    }
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-W", "error", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(("name", "file", "config"), _FORMATS, ids=[f[0] for f in _FORMATS])
def test_the_projects_pythonpath_is_appended_behind_the_dependencies(
    tmp_path: Path, name: str, file: str, config: str
) -> None:
    root = _project(tmp_path, file, config)
    (root / "tests" / "test_order.py").write_text(_ORDER_TEST, encoding="utf-8")

    done = _pytest(root)

    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize(
    ("name", "file", "config"),
    [
        ("sin fichero", None, ""),
        ("pyproject.toml sin pytest", "pyproject.toml", '[project]\nname = "x"\n'),
    ],
)
def test_a_project_without_pytest_configuration_still_runs(
    tmp_path: Path, name: str, file: str | None, config: str
) -> None:
    root = _project(tmp_path, file, config)

    done = _pytest(root)

    assert done.returncode == 0, done.stdout + done.stderr


def test_a_pythonpath_outside_the_project_stops_the_suite_saying_so(tmp_path: Path) -> None:
    root = _project(tmp_path / "proyecto", "pytest.ini", "[pytest]\npythonpath = ../fuera\n")
    (tmp_path / "fuera").mkdir()

    done = _pytest(root)

    assert done.returncode != 0
    assert "fuera del proyecto" in done.stdout + done.stderr


def test_a_plugin_another_one_imported_first_is_rewritten_without_a_warning(
    tmp_path: Path,
) -> None:
    # `xdist.looponfail` imports `xdist.plugin`: listed before it, without marking them first,
    # pytest warns it can't rewrite `xdist.plugin`, and with warnings as errors nothing starts.
    root = _project(tmp_path, "pytest.ini", "[pytest]\npythonpath = tests/support\n")

    done = _pytest(root, plugins="lictor_project_path,xdist.looponfail,xdist.plugin")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "PytestAssertRewriteWarning" not in done.stdout + done.stderr


def test_without_the_marking_the_same_list_fails(tmp_path: Path) -> None:
    # What the previous case guards against, so it can't pass because nothing ever warned.
    root = _project(tmp_path, "pytest.ini", "[pytest]\n")
    other = tmp_path / "otro"
    other.mkdir()
    (other / "solo_ruta.py").write_text(
        "import sys\n\n\ndef pytest_load_initial_conftests(early_config):\n"
        "    sys.path.append(str(early_config.invocation_params.dir / 'src'))\n",
        encoding="utf-8",
    )
    env_plugins = "solo_ruta,xdist.looponfail,xdist.plugin"
    done = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-W", "error"],
        cwd=root,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(root),
            "PYTHONPATH": str(other),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_PLUGINS": env_plugins,
            "PYTEST_ADDOPTS": "-o pythonpath= -p no:cacheprovider",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode != 0
    assert "PytestAssertRewriteWarning" in done.stdout + done.stderr


@pytest.mark.parametrize(
    ("file", "config", "expected"),
    [
        ("pytest.ini", "[pytest]\npythonpath = a b\n", ["a", "b"]),
        ("pytest.ini", "[pytest]\naddopts = -q\n", []),
        ("setup.cfg", "[tool:pytest]\npythonpath = a\n", ["a"]),
        ("setup.cfg", "[pytest]\npythonpath = a\n", []),
        ("pyproject.toml", '[tool.pytest.ini_options]\npythonpath = ["a", "b"]\n', ["a", "b"]),
        ("pyproject.toml", '[tool.pytest.ini_options]\npythonpath = "a b"\n', ["a", "b"]),
        ("pyproject.toml", "[tool.black]\nline-length = 1\n", []),
        ("pytest.toml", '[pytest]\npythonpath = ["a"]\n', ["a"]),
    ],
)
def test_declared_pythonpath_reads_each_format_as_written(
    tmp_path: Path, file: str, config: str, expected: list[str]
) -> None:
    path = tmp_path / file
    path.write_text(config, encoding="utf-8")

    assert declared_pythonpath(path) == expected
