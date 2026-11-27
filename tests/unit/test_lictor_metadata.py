"""What `lictor_metadata` reads of a project's `pyproject.toml`, and why it reads nothing (F82).

The distribution is what installing the project would leave: its name, its version as the backend
writes it and its entry points, never `pytest11`. Whatever it can't read without running anything
is a reason, never an exception and never a version made up.
"""

from pathlib import Path

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_metadata
from lictor.adapters.config.profiles.python_uv.lictor_metadata import Metadata, read, record

HATCH = '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
SETUPTOOLS = '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n'
FLIT = '[build-system]\nrequires = ["flit_core"]\nbuild-backend = "flit_core.buildapi"\n'
DYNAMIC = '[project]\nname = "demo-pkg"\ndynamic = ["version"]\n'


def _project(root: Path, pyproject: str, files: dict[str, str] | None = None) -> Path:
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    for rel, text in (files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _read(root: Path) -> Metadata:
    found = read(root)
    assert isinstance(found, Metadata), found
    return found


def test_a_static_version_is_read_and_normalized_as_the_backends_write_it(tmp_path: Path) -> None:
    root = _project(tmp_path, HATCH + '[project]\nname = "Demo.Pkg"\nversion = "1.0.0-dev"\n')

    found = _read(root)

    assert (found.name, found.version) == ("Demo.Pkg", "1.0.0.dev0")
    assert found.metadata_text() == "Metadata-Version: 2.1\nName: Demo.Pkg\nVersion: 1.0.0.dev0\n"


def test_an_invalid_version_registers_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path, HATCH + '[project]\nname = "x"\nversion = "no es una"\n')

    assert read(root) == "la versión no es una no es válida"


def test_scripts_and_groups_are_entry_points_and_pytest11_never(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        HATCH
        + '[project]\nname = "x"\nversion = "1"\n'
        + '[project.scripts]\nx = "x.cli:main"\n'
        + '[project.gui-scripts]\nxg = "x.gui:main"\n'
        + '[project.entry-points."x.plugins"]\nuno = "x.uno:Uno"\ndos = "x.dos"\n'
        + '[project.entry-points.pytest11]\nx = "x.plugin"\n'
        + "[project.entry-points.empty]\n",
    )

    found = _read(root)

    assert found.entry_points == {
        "console_scripts": {"x": "x.cli:main"},
        "gui_scripts": {"xg": "x.gui:main"},
        "x.plugins": {"uno": "x.uno:Uno", "dos": "x.dos"},
    }
    assert found.entry_points_text() == (
        "[console_scripts]\nx = x.cli:main\n\n[gui_scripts]\nxg = x.gui:main\n\n"
        "[x.plugins]\nuno = x.uno:Uno\ndos = x.dos\n"
    )
    assert record(root) == {
        "name": "x",
        "version": "1",
        "groups": {"console_scripts": 1, "gui_scripts": 1, "x.plugins": 2},
        "unread": [],
    }


def test_without_entry_points_there_is_no_file_of_them(tmp_path: Path) -> None:
    found = _read(_project(tmp_path, HATCH + '[project]\nname = "x"\nversion = "1"\n'))

    assert (found.entry_points, found.entry_points_text()) == ({}, "")


def test_entry_points_left_to_the_backend_are_named_as_unread(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        SETUPTOOLS
        + '[project]\nname = "x"\nversion = "1"\ndynamic = ["scripts", "entry-points"]\n',
    )

    assert _read(root).unread == ("scripts", "entry-points")


@pytest.mark.parametrize(
    ("pyproject", "reason"),
    [
        ('[project]\nname = "x"\nversion = "1"\n', "pyproject.toml no tiene [build-system]"),
        (
            HATCH + '[project]\nname = "x"\nversion = "1"\n[tool.uv]\npackage = false\n',
            "tool.uv.package = false",
        ),
    ],
    ids=["sin build-system", "tool.uv.package false"],
)
def test_what_uv_wouldnt_install_has_no_metadata(
    tmp_path: Path, pyproject: str, reason: str
) -> None:
    found = read(_project(tmp_path, pyproject))

    assert found == f"{reason}: {lictor_metadata.NOT_INSTALLED}"
    # Not a problem to fix: installed, it would have no metadata either.
    assert record(tmp_path) == {"not_installed": found}


def test_uv_package_true_installs_it_without_build_system(tmp_path: Path) -> None:
    root = _project(tmp_path, '[project]\nname = "x"\nversion = "1"\n[tool.uv]\npackage = true\n')

    assert _read(root).name == "x"


@pytest.mark.parametrize(
    ("pyproject", "reason"),
    [
        ("[tool.x]\n", "pyproject.toml no tiene [project]"),
        ("project = 1\n", "pyproject.toml no tiene [project]"),
        (HATCH + '[project]\nversion = "1"\n', "[project] no tiene un nombre"),
        (HATCH + '[project]\nname = " "\nversion = "1"\n', "[project] no tiene un nombre"),
        (HATCH + '[project]\nname = "x"\n', "[project] no tiene una versión"),
        (HATCH + '[project]\nname = "x"\nversion = 1\n', "[project] no tiene una versión"),
        (
            HATCH + '[project]\nname = "x"\nversion = "1"\ndynamic = "version"\n',
            "[project].dynamic no es una lista de textos",
        ),
        (
            HATCH + '[project]\nname = "x"\nversion = "1"\nscripts = "x"\n',
            "el grupo console_scripts de los entry points no es una tabla de textos",
        ),
        (
            HATCH + '[project]\nname = "x"\nversion = "1"\n[project.scripts]\nx = 1\n',
            "el grupo console_scripts de los entry points no es una tabla de textos",
        ),
        (
            HATCH + '[project]\nname = "x"\nversion = "1"\nentry-points = "x"\n',
            "[project.entry-points] no es una tabla",
        ),
        (
            HATCH + '[project]\nname = " demo\\nVersion: 9 "\nversion = "1"\n',
            "[project].name no es un nombre de distribución válido: ' demo\\nVersion: 9 '",
        ),
        (
            HATCH + '[project]\nname = "a b/c"\nversion = "1"\n',
            "[project].name no es un nombre de distribución válido: 'a b/c'",
        ),
    ],
)
def test_what_isnt_understood_registers_nothing_and_says_why(
    tmp_path: Path, pyproject: str, reason: str
) -> None:
    assert read(_project(tmp_path, pyproject)) == reason
    assert record(tmp_path) == {"problem": reason, "fix": lictor_metadata.FIX}


def test_a_pyproject_that_cant_be_read_is_a_reason(tmp_path: Path) -> None:
    found = read(_project(tmp_path, "[project\n"))

    assert isinstance(found, str)
    assert found.startswith("pyproject.toml no se puede leer: ")


def test_without_pyproject_there_is_nothing(tmp_path: Path) -> None:
    assert read(tmp_path) == "no hay pyproject.toml"


def test_without_a_toml_reader_there_is_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, HATCH + '[project]\nname = "x"\nversion = "1"\n')
    monkeypatch.setattr(lictor_metadata, "toml_module", lambda: None)

    assert read(root) == "no hay con qué leer pyproject.toml en este intérprete"


def test_without_packaging_the_version_stays_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path, HATCH + '[project]\nname = "x"\nversion = "1.0.0-dev"\n')
    real = lictor_metadata.importlib.import_module

    def without_packaging(name: str) -> object:
        if name.startswith("packaging"):
            raise ImportError(name)
        return real(name)

    monkeypatch.setattr(lictor_metadata.importlib, "import_module", without_packaging)

    assert _read(root).version == "1.0.0-dev"
    _project(tmp_path, HATCH + '[project]\nname = "x"\nversion = "1\\nName: y"\n')
    assert read(root) == "la versión '1\\nName: y' no es válida"


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ('__version__ = "1.2.3"\n', "1.2.3"),
        ("VERSION = '2.0'\n", "2.0"),
        ('version = "3.0"\n', "3.0"),
        ('__version__ = "v4.0"\n', "4.0"),
        ('"""Doc."""\n\nfrom x import y\n__version__ = "5.0"  # comentario\n', "5.0"),
    ],
    ids=["dunder", "VERSION", "sin distinguir mayúsculas", "con v", "tras otras líneas"],
)
def test_hatch_reads_its_default_pattern(tmp_path: Path, text: str, version: str) -> None:
    root = _project(
        tmp_path,
        HATCH + DYNAMIC + '[tool.hatch.version]\npath = "src/demo/__about__.py"\n',
        {"src/demo/__about__.py": text},
    )

    assert _read(root).version == version


@pytest.mark.parametrize(
    "text",
    ['    __version__ = "1"\n', '__version__: str = "1"\n', "__version__ = VERSION\n"],
    ids=["sangrado", "anotado", "no es un texto"],
)
def test_hatch_doesnt_read_what_its_pattern_doesnt(tmp_path: Path, text: str) -> None:
    root = _project(
        tmp_path,
        HATCH + DYNAMIC + '[tool.hatch.version]\npath = "v.py"\n',
        {"v.py": text},
    )

    assert read(root) == "v.py no tiene la versión que busca hatch"


def test_hatch_reads_its_own_pattern(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        HATCH
        + DYNAMIC
        + "[tool.hatch.version]\n"
        + "path = \"VERSION.txt\"\npattern = 'release (?P<version>[0-9.]+)'\n",
        {"VERSION.txt": "release 7.1\n"},
    )

    assert _read(root).version == "7.1"


@pytest.mark.parametrize(
    ("hatch", "files", "reason"),
    [
        ('source = "vcs"\n', {}, lictor_metadata.GIT),
        (
            'source = "code"\npath = "v.py"\n',
            {"v.py": "__version__ = '1'\n"},
            "hatch saca la versión con source = 'code', que ejecuta código",
        ),
        (
            "",
            {},
            "la versión es dinámica, y tool.hatch.version no dice de qué fichero",
        ),
        (
            'path = "../fuera.py"\n',
            {},
            "la versión se lee de ../fuera.py, que está fuera del proyecto",
        ),
        ('path = "v.py"\npattern = "("\n', {"v.py": ""}, "el pattern de tool.hatch.version no es"),
        ('path = "v.py"\npattern = "x"\n', {"v.py": "x"}, "v.py no tiene la versión que busca"),
        (
            "path = \"v.py\"\npattern = 'a|(?P<version>b)'\n",
            {"v.py": "a"},
            "v.py no tiene la versión que busca",
        ),
        ('path = "v\\u0000.py"\n', {}, "la versión se lee de 'v\\x00.py', que no se puede leer"),
    ],
    ids=[
        "vcs",
        "code",
        "sin path",
        "fuera del clon",
        "pattern roto",
        "pattern sin grupo",
        "grupo que no participa",
        "byte nulo",
    ],
)
def test_hatch_registers_nothing_it_cant_read_without_running(
    tmp_path: Path, hatch: str, files: dict[str, str], reason: str
) -> None:
    root = _project(tmp_path, HATCH + DYNAMIC + "[tool.hatch.version]\n" + hatch, files)

    found = read(root)

    assert isinstance(found, str)
    assert found.startswith(reason)


def test_a_file_that_cant_be_read_is_a_reason(tmp_path: Path) -> None:
    root = _project(tmp_path, HATCH + DYNAMIC + '[tool.hatch.version]\npath = "v.py"\n')

    found = read(root)

    assert isinstance(found, str)
    assert found.startswith("la versión se lee de 'v.py', que no se puede leer: ")


@pytest.mark.parametrize(
    ("setuptools", "files", "version"),
    [
        ('attr = "demo.__version__"', {"src/demo/__init__.py": '__version__ = "1.1"\n'}, "1.1"),
        ('attr = "demo.__version__"', {"demo/__init__.py": '__version__ = "1.2"\n'}, "1.2"),
        ('attr = "demo.about.VERSION"', {"src/demo/about.py": "VERSION = (1, 3, 0)\n"}, "1.3.0"),
        (
            'attr = "demo.__version__"',
            {"src/demo/__init__.py": 'import os\n__version__: str = "1.4"\n__version__ = "9"\n'},
            "1.4",
        ),
        ('file = "VERSION"', {"VERSION": "  1.5\n"}, "1.5"),
    ],
    ids=["en src", "en la raíz", "tupla", "la primera, anotada", "file"],
)
def test_setuptools_reads_a_literal_attr_or_a_file(
    tmp_path: Path, setuptools: str, files: dict[str, str], version: str
) -> None:
    root = _project(
        tmp_path,
        SETUPTOOLS + DYNAMIC + "[tool.setuptools.dynamic]\nversion = {" + setuptools + "}\n",
        files,
    )

    assert _read(root).version == version


def test_setuptools_follows_package_dir(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        SETUPTOOLS
        + DYNAMIC
        + '[tool.setuptools]\npackage-dir = {"" = "lib", "demo.extra" = "otro/extra"}\n'
        + '[tool.setuptools.dynamic]\nversion = {attr = "demo.__version__"}\n',
        {"lib/demo/__init__.py": '__version__ = "2.1"\n', "src/demo/__init__.py": "x = 1\n"},
    )

    assert _read(root).version == "2.1"


def test_setuptools_follows_the_package_dir_of_the_package(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        SETUPTOOLS
        + DYNAMIC
        + '[tool.setuptools]\npackage-dir = {"" = "lib", "demo" = "codigo/demo"}\n'
        + '[tool.setuptools.dynamic]\nversion = {attr = "demo.v.VERSION"}\n',
        {"codigo/demo/v.py": 'VERSION = "2.2"\n'},
    )

    assert _read(root).version == "2.2"


@pytest.mark.parametrize(
    ("pyproject", "files", "reason"),
    [
        ("[tool.setuptools_scm]\n", {}, lictor_metadata.GIT),
        ("", {}, "la versión es dinámica, y tool.setuptools.dynamic.version no da"),
        (
            '[tool.setuptools.dynamic]\nversion = {file = ["A", "B"]}\n',
            {},
            "tool.setuptools.dynamic.version la lee de varios ficheros, y Lictor solo lee uno",
        ),
        (
            '[tool.setuptools.dynamic]\nversion = {attr = "demo.__version__"}\n',
            {"src/demo/__init__.py": "from ._v import __version__\n"},
            "src/demo/__init__.py no asigna a __version__ un texto",
        ),
        (
            '[tool.setuptools.dynamic]\nversion = {attr = "demo.__version__"}\n',
            {"src/demo/__init__.py": "__version__ = get_version()\n"},
            "src/demo/__init__.py no asigna a __version__ un texto",
        ),
        (
            '[tool.setuptools.dynamic]\nversion = {attr = "demo.__version__"}\n',
            {"src/demo/__init__.py": "def f(:\n"},
            "src/demo/__init__.py no asigna a __version__ un texto",
        ),
        (
            '[tool.setuptools.dynamic]\nversion = {attr = "demo.__version__"}\n',
            {"src/demo/__init__.py": "if True:\n    __version__ = '1'\n"},
            "src/demo/__init__.py no asigna a __version__ un texto",
        ),
        (
            '[tool.setuptools.dynamic]\nversion = {attr = "demo.__version__"}\n',
            {},
            "la versión se lee de demo.__version__, y no se encuentra el módulo",
        ),
        (
            '[tool.setuptools.dynamic]\nversion = {attr = "demo.__version__"}\n',
            {"src/demo/__init__.py": "__version__ = ()\n"},
            "src/demo/__init__.py no asigna a __version__ un texto",
        ),
    ],
    ids=[
        "setuptools-scm",
        "sin dynamic",
        "varios ficheros",
        "importado",
        "llamada",
        "no se puede leer",
        "no en el nivel del módulo",
        "sin módulo",
        "tupla vacía",
    ],
)
def test_setuptools_registers_nothing_it_cant_read_without_running(
    tmp_path: Path, pyproject: str, files: dict[str, str], reason: str
) -> None:
    root = _project(tmp_path, SETUPTOOLS + DYNAMIC + pyproject, files)

    found = read(root)

    assert isinstance(found, str)
    assert found.startswith(reason)


@pytest.mark.parametrize(
    ("pyproject", "file"),
    [("", "src/demo_pkg/__init__.py"), ('[tool.flit.module]\nname = "otro"\n', "otro.py")],
    ids=["por el nombre del proyecto", "tool.flit.module"],
)
def test_flit_reads_the_modules_version(tmp_path: Path, pyproject: str, file: str) -> None:
    root = _project(tmp_path, FLIT + DYNAMIC + pyproject, {file: '__version__ = "3.1"\n'})

    assert _read(root).version == "3.1"


@pytest.mark.parametrize(
    ("pyproject", "reason"),
    [
        (
            '[build-system]\nbuild-backend = "pdm.backend"\n'
            + DYNAMIC
            + '[tool.pdm.version]\nsource = "scm"\n',
            lictor_metadata.GIT,
        ),
        (
            '[build-system]\nbuild-backend = "pdm.backend"\n' + DYNAMIC,
            "la versión es dinámica, y Lictor no sabe leerla de pdm.backend",
        ),
        (
            "tool = 1\n[build-system]\n" + DYNAMIC,
            "la versión es dinámica, y Lictor no sabe leerla de su backend",
        ),
    ],
    ids=["pdm scm", "pdm", "sin backend"],
)
def test_other_backends_register_nothing(tmp_path: Path, pyproject: str, reason: str) -> None:
    assert read(_project(tmp_path, pyproject)) == reason


def test_names_are_compared_as_importlib_metadata_does() -> None:
    assert lictor_metadata.normalized("Demo.Pkg") == "demo_pkg"
    assert lictor_metadata.normalized("demo--pkg_x") == "demo_pkg_x"


@pytest.mark.parametrize(
    ("available", "expected"),
    [({"tomllib", "tomli"}, "tomllib"), ({"tomli"}, "tomli"), (set(), None)],
)
def test_toml_comes_from_tomllib_or_else_tomli(
    monkeypatch: pytest.MonkeyPatch, available: set[str], expected: str | None
) -> None:
    def only(name: str) -> str:
        if name not in available:
            raise ImportError(name)
        return name

    monkeypatch.setattr(lictor_metadata.importlib, "import_module", only)

    assert lictor_metadata.toml_module() == expected
