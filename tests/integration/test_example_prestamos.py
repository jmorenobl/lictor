"""The adoption example stays what its README promises: loadable, and with three scenarios done.

The oracle reads the steps from the Gherkin and calls the example's code directly, with neither
pytest-bdd nor the example's environment, so it can't be fooled by a driver.
"""

import importlib.util
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from gherkin.parser import Parser

from lictor.adapters.spec.gherkin_source import GherkinSource

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "prestamos-biblioteca"
IMPLEMENTED = ["PRE-001", "PRE-002", "PRE-003"]
ALL = [*IMPLEMENTED, "DEV-001", "DEV-002", "DEV-003", "RES-001", "RES-002", "RES-003"]


def _library_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "biblioteca_del_ejemplo", EXAMPLE / "src" / "biblioteca" / "__init__.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _day(text: str) -> date:
    return date.fromisoformat(text)


class _World:
    """Plays the steps of the loan scenarios against the example's code."""

    def __init__(self, module: ModuleType) -> None:
        self.module = module
        self.library: Any = module.Biblioteca()
        self.loan: Any = None
        self.refusal: str | None = None
        self.steps: list[tuple[re.Pattern[str], Callable[..., None]]] = [
            (re.compile(r'que "(\w+)" tiene prestado "(\d+)" desde el "([\d-]+)"'), self.lent),
            (re.compile(r'"(\w+)" pide prestado "(\d+)" el "([\d-]+)"'), self.borrow),
            (re.compile(r'el préstamo vence el "([\d-]+)"'), self.due),
            (re.compile(r'quedan (\d+) ejemplares de "(\d+)"'), self.left),
            (re.compile(r'se rechaza por "([^"]+)"'), self.refused),
        ]

    def catalog(self, rows: list[dict[str, Any]]) -> None:
        cells = [[cell["value"] for cell in row["cells"]] for row in rows]
        for isbn, title, copies in cells[1:]:
            self.library.alta(isbn, title, int(copies))

    def play(self, step: dict[str, Any]) -> None:
        if step["text"] == "el catálogo":
            self.catalog(step["dataTable"]["rows"])
            return
        for pattern, action in self.steps:
            found = pattern.fullmatch(step["text"])
            if found:
                action(*found.groups())
                return
        raise AssertionError(f"Paso sin definir en el oráculo: {step['text']}")

    def lent(self, member: str, isbn: str, day: str) -> None:
        self.library.prestar(member, isbn, _day(day))

    def borrow(self, member: str, isbn: str, day: str) -> None:
        try:
            self.loan = self.library.prestar(member, isbn, _day(day))
        except self.module.PrestamoRechazado as exc:
            self.refusal = exc.motivo

    def due(self, day: str) -> None:
        assert self.loan.vence == _day(day)

    def left(self, copies: str, isbn: str) -> None:
        assert self.library.disponibles(isbn) == int(copies)

    def refused(self, reason: str) -> None:
        assert self.refusal == reason


def _scenarios(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    feature = Parser().parse(path.read_text(encoding="utf-8"))["feature"]
    background = [c["background"] for c in feature["children"] if "background" in c]
    scenarios = {
        tag["name"].lstrip("@"): child["scenario"]
        for child in feature["children"]
        if "scenario" in child
        for tag in child["scenario"]["tags"]
    }
    return [step for b in background for step in b["steps"]], scenarios


def test_the_features_load_with_backgrounds_and_rules() -> None:
    scenarios = GherkinSource().load(EXAMPLE / "features")
    text = (EXAMPLE / "features" / "devoluciones.feature").read_text(encoding="utf-8")

    assert sorted(s.id.value for s in scenarios) == sorted(ALL)
    assert "Antecedentes:" in text
    assert text.count("Regla:") == 2


@pytest.mark.parametrize("scenario_id", IMPLEMENTED)
def test_the_implemented_scenarios_pass_with_the_examples_code(scenario_id: str) -> None:
    background, scenarios = _scenarios(EXAMPLE / "features" / "prestamos.feature")
    world = _World(_library_module())

    for step in [*background, *scenarios[scenario_id]["steps"]]:
        world.play(step)
