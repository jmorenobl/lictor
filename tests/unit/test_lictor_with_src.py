"""The profile's launcher that runs a tool with `src` at the end of the import path (S19)."""

import importlib
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_with_src


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A project whose `src` has a package nothing else installs."""
    package = tmp_path / "src" / "paquete_de_prueba_s19"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("NOMBRE = 'paquete'\n", encoding="utf-8")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    yield tmp_path
    sys.modules.pop("paquete_de_prueba_s19", None)


def _tool(found: dict[str, object]) -> Callable[[], object]:
    def tool() -> object:
        package = importlib.import_module("paquete_de_prueba_s19")
        found["nombre"] = package.NOMBRE
        found["argv"] = list(sys.argv)
        found["last"] = sys.path[-1]
        return None

    return tool


def test_the_tool_finds_the_project_in_src_behind_everything_else(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found: dict[str, object] = {}
    monkeypatch.setattr(lictor_with_src, "console_script", lambda name: _tool(found))

    code = lictor_with_src.main(["lint-imports", "--config", "pyproject.toml"], project)

    assert code == 0
    assert found == {
        "nombre": "paquete",
        "argv": ["lint-imports", "--config", "pyproject.toml"],
        "last": str(project / "src"),
    }


@pytest.mark.parametrize(
    ("result", "code"), [(None, 0), (0, 0), (3, 3), ("roto", 1), (SystemExit(4), 4)]
)
def test_the_tool_s_result_is_its_exit_code(
    project: Path, monkeypatch: pytest.MonkeyPatch, result: object, code: int
) -> None:
    def tool() -> object:
        if isinstance(result, SystemExit):
            raise result
        return result

    monkeypatch.setattr(lictor_with_src, "console_script", lambda name: tool)

    assert lictor_with_src.main(["herramienta"], project) == code


def test_without_a_command_or_with_an_unknown_one_it_says_so(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert lictor_with_src.main([], project) == 2
    assert lictor_with_src.main(["no-existe-ninguna-orden-asi"], project) == 2
    told = capsys.readouterr().err
    assert lictor_with_src.USAGE in told
    assert "no hay ninguna orden «no-existe-ninguna-orden-asi» en el entorno" in told


def test_a_console_script_of_the_environment_is_found() -> None:
    assert lictor_with_src.console_script("pytest") is not None
    assert lictor_with_src.console_script("no-existe-ninguna-orden-asi") is None
