"""What the path plugin of `python-uv` does with the project's `pythonpath` (F77) and plugins (F81).

Each case runs pytest in a subprocess the way verification does: no autoload, the plugins named in
`PYTEST_PLUGINS` with this one first, and `-o pythonpath=` voiding pytest's own. `-W error` makes
any warning the plugin's loading causes fail the suite, as with warnings as errors.
"""

import os
import shutil
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
    root: Path,
    *args: str,
    plugins: str = "lictor_project_path",
    extra_path: Path | None = None,
    python: str = sys.executable,
) -> subprocess.CompletedProcess[str]:
    paths = [str(_PLUGIN_DIR), *([str(extra_path)] if extra_path is not None else [])]
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root),
        "PYTHONPATH": os.pathsep.join(paths),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_PLUGINS": plugins,
        "PYTEST_ADDOPTS": "-o pythonpath= -p no:cacheprovider",
    }
    return subprocess.run(
        [python, "-B", "-m", "pytest", "-q", "-W", "error", *args],
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


# F82 (ADR-0019): the project's own distribution, registered in memory from its `pyproject.toml`.
_PACKAGED = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "Demo.Pkg"
version = "1.0.0-dev"

[project.scripts]
demo = "pkg:main"

[project.entry-points."demo.plugins"]
uno = "pkg:uno"

[project.entry-points.pytest11]
demo = "pkg.plugin"

[tool.pytest.ini_options]
filterwarnings = ["error"]
"""

_METADATA_TEST = """\
import sys
from importlib.metadata import distribution, distributions, entry_points, version


def _group(name):
    if sys.version_info >= (3, 10):
        return list(entry_points(group=name))
    return list(entry_points().get(name, []))


def test_the_project_has_its_metadata():
    import pkg

    assert pkg.VERSION == "1.0.0.dev0"
    assert version("demo_pkg") == version("Demo-Pkg") == "1.0.0.dev0"
    assert distribution("demo.pkg").metadata["Name"] == "Demo.Pkg"
    assert [e.load()() for e in _group("demo.plugins")] == [1]
    assert [e.name for e in _group("console_scripts") if e.value == "pkg:main"] == ["demo"]
    assert not [e for e in _group("pytest11") if e.name == "demo"]
    assert len([d for d in distributions() if d.metadata["Name"] == "Demo.Pkg"]) == 1
"""

_PKG = (
    "from importlib.metadata import version\n\n"
    'VERSION = version("demo-pkg")\n\n\n'
    "def uno():\n    return 1\n"
)


def _packaged(root: Path, pyproject: str = _PACKAGED, test: str = _METADATA_TEST) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text(_PKG, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_metadata.py").write_text(test, encoding="utf-8")
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return root


def test_the_project_reads_its_own_metadata_inside_pytest(tmp_path: Path) -> None:
    done = _pytest(_packaged(tmp_path))

    assert done.returncode == 0, done.stdout + done.stderr


def test_xdist_workers_register_it_too(tmp_path: Path) -> None:
    done = _pytest(_packaged(tmp_path), "-n", "2", plugins="lictor_project_path,xdist.plugin")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 passed" in done.stdout


_NOT_FOUND_TEST = """\
from importlib.metadata import PackageNotFoundError, version

import pytest


def test_there_is_no_metadata():
    with pytest.raises(PackageNotFoundError):
        version("demo-pkg")
"""


@pytest.mark.parametrize(
    "pyproject",
    [
        _PACKAGED.replace('[build-system]\nrequires = ["hatchling"]\n', "[tool.x]\n").replace(
            'build-backend = "hatchling.build"\n', ""
        ),
        _PACKAGED + "\n[tool.uv]\npackage = false\n",
        _PACKAGED.replace('version = "1.0.0-dev"', "version = 1"),
    ],
    ids=["sin build-system", "tool.uv.package false", "versión que no es un texto"],
)
def test_what_uv_wouldnt_install_or_cant_be_read_has_no_metadata(
    tmp_path: Path, pyproject: str
) -> None:
    root = _packaged(tmp_path, pyproject, _NOT_FOUND_TEST)
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")

    done = _pytest(root)

    assert done.returncode == 0, done.stdout + done.stderr


def test_a_distribution_the_environment_has_wins(tmp_path: Path) -> None:
    root = _packaged(tmp_path / "p")
    installed = tmp_path / "site" / "demo_pkg-9.0.dist-info"
    installed.mkdir(parents=True)
    (installed / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: demo-pkg\nVersion: 9.0\n", encoding="utf-8"
    )
    (installed / "entry_points.txt").write_text("[demo.plugins]\nnueve = x:y\n", encoding="utf-8")
    (root / "tests" / "test_metadata.py").write_text(
        "import sys\n"
        "from importlib.metadata import distributions, entry_points, version\n\n\n"
        "def test_the_installed_one():\n"
        '    assert version("demo-pkg") == "9.0"\n'
        "    names = [d.metadata['Name'] for d in distributions()]\n"
        "    assert names.count('demo-pkg') == 1 and 'Demo.Pkg' not in names\n"
        "    eps = entry_points(group='demo.plugins') if sys.version_info >= (3, 10) else "
        "entry_points()['demo.plugins']\n"
        "    assert [e.name for e in eps] == ['nueve']\n",
        encoding="utf-8",
    )
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")

    done = _pytest(root, extra_path=installed.parent)

    assert done.returncode == 0, done.stdout + done.stderr


def test_a_nested_pytest_main_leaves_one_finder(tmp_path: Path) -> None:
    root = _packaged(tmp_path / "p")
    other = tmp_path / "sin_proyecto"
    other.mkdir()
    (other / "test_nada.py").write_text("def test_nada():\n    pass\n", encoding="utf-8")
    (root / "tests" / "test_nested.py").write_text(
        "import os\nimport sys\n\nimport pytest\n\n"
        "from importlib.metadata import entry_points, version\n\n\n"
        "def _finders():\n"
        "    return [f for f in sys.meta_path if type(f).__name__ == 'ProjectFinder']\n\n\n"
        "def test_nested_runs():\n"
        "    assert pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/test_metadata.py']) == 0\n"
        "    assert len(_finders()) == 1\n"
        f"    os.chdir({str(other)!r})\n"
        "    try:\n"
        "        assert pytest.main(['-q', '-p', 'no:cacheprovider']) == 0\n"
        "    finally:\n"
        f"        os.chdir({str(root)!r})\n"
        "    assert len(_finders()) == 1\n"
        '    assert version("demo-pkg") == "1.0.0.dev0"\n'
        "    eps = entry_points(group='demo.plugins') if sys.version_info >= (3, 10) else "
        "entry_points()['demo.plugins']\n"
        "    assert [e.name for e in eps] == ['uno']\n",
        encoding="utf-8",
    )

    done = _pytest(root, "tests/test_nested.py")

    assert done.returncode == 0, done.stdout + done.stderr


_BACKPORT_TEST = """\
import importlib_metadata


def test_the_backport_sees_it_too():
    assert importlib_metadata.version("demo-pkg") == "1.0.0.dev0"
    assert [e.name for e in importlib_metadata.entry_points(group="demo.plugins")] == ["uno"]
"""


@pytest.fixture(scope="module")
def python_3_9(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A Python 3.9 with pytest and the backport 3.10.1, which asks every distribution for
    `_normalized_name` and calls `matches` on the entry points of its own class (I4)."""
    uv = shutil.which("uv")
    offline = {**os.environ, "UV_PYTHON_DOWNLOADS": "never", "UV_OFFLINE": "1"}
    found = subprocess.run(
        [uv or "uv", "python", "find", "3.9"],
        check=False,
        capture_output=True,
        text=True,
        env=offline,
    )
    if uv is None or found.returncode != 0:
        pytest.skip("no hay un Python 3.9 instalado")
    venv = tmp_path_factory.mktemp("py39") / "venv"
    steps = [
        [uv, "venv", "-q", "-p", found.stdout.strip(), str(venv)],
        [
            uv,
            "pip",
            "install",
            "-q",
            "-p",
            str(venv / "bin" / "python"),
            "pytest",
            "packaging",
            "importlib_metadata==3.10.1",
        ],
    ]
    for step in steps:
        if subprocess.run(step, check=False, capture_output=True, env=offline).returncode != 0:
            pytest.skip("no se pudo preparar Python 3.9 con pytest sin red")
    return str(venv / "bin" / "python")


def test_python_3_9_and_its_backport_see_the_metadata(tmp_path: Path, python_3_9: str) -> None:
    root = _packaged(tmp_path)
    (root / "tests" / "test_backport.py").write_text(_BACKPORT_TEST, encoding="utf-8")

    done = _pytest(root, python=python_3_9)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "2 passed" in done.stdout


def test_a_search_of_other_paths_doesnt_find_the_project(tmp_path: Path) -> None:
    root = _packaged(tmp_path / "p")
    (tmp_path / "vacio").mkdir()
    (root / "tests" / "test_metadata.py").write_text(
        "from importlib.metadata import distributions\n\n\n"
        "def test_only_where_its_src_is_looked_at():\n"
        f"    found = distributions(path=[{str(tmp_path / 'vacio')!r}])\n"
        "    assert [d.metadata['Name'] for d in found] == []\n"
        "    assert 'Demo.Pkg' in [d.metadata['Name'] for d in distributions()]\n",
        encoding="utf-8",
    )

    done = _pytest(root)

    assert done.returncode == 0, done.stdout + done.stderr
