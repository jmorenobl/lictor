"""The profile's impostor of the Gherkin (ADR-0016): what it reads of a step, and what it changes.

pytest-bdd isn't in Lictor's environment, so its two functions are given here as the runner gives
them; the plugin runs with them in the `repro` cases, in a project's own environment.
"""

import json
import sys
import types
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_gherkin
from lictor.adapters.config.profiles.python_uv.lictor_gherkin import (
    LESS,
    MORE,
    alter,
    altered_text,
    reads,
)


def _compares(total: int) -> None:
    assert total == 12


def _ignores(total: int) -> None:
    assert True


def _after_a_store(pedido: Any, codigo: str) -> None:
    """3.13 joins the store of `b` and the load of `codigo` in one instruction, on one line: what
    pytest's rewriting of an assert leaves (`STORE_FAST_LOAD_FAST ('@py_assert9', 'codigo')`)."""
    assert [(b := pedido), codigo] != b


def _in_a_closure(total: int) -> None:
    def check() -> bool:
        return total == 12

    assert check()


def _in_a_comprehension(filas: list[str]) -> None:
    assert [fila.upper() for fila in filas] == ["A"]


def _only_in_the_message(total: int) -> None:
    assert True, total  # a known limit: the message reads it too


def _wrapped(func: Callable[..., None]) -> Callable[..., None]:
    def wrapper(*args: Any, **kwargs: Any) -> None:
        func(*args, **kwargs)

    wrapper.__wrapped__ = func
    return wrapper


@pytest.mark.parametrize(
    ("func", "name", "read"),
    [
        (_compares, "total", True),
        (_ignores, "total", False),
        (_after_a_store, "codigo", True),
        (_after_a_store, "pedido", True),
        (_in_a_closure, "total", True),
        (_in_a_comprehension, "filas", True),
        (_only_in_the_message, "total", True),
        (_wrapped(_ignores), "total", False),  # what a decorator wraps is what is read
        (_compares, "otro", True),  # a parameter it doesn't declare can't be told: read
        (len, "x", True),  # nor code it doesn't have
    ],
)
def test_whether_a_step_reads_each_value(func: Callable[..., None], name: str, read: bool) -> None:
    assert reads(func, name) is read


@pytest.mark.parametrize(
    ("text", "more", "less"),
    [
        ("12", "13", "11"),
        ("-3", "-2", "-4"),
        ("12,50", "13,50", "11,50"),
        ("0.25", "1.25", "-0.75"),
        (" 7 ", "8", "6"),
        ("18/12/2026", "19/12/2026", "17/12/2026"),
        ("31-12-2026", "01-01-2027", "30-12-2026"),
        ("2026-12-18", "2026-12-19", "2026-12-17"),
        ("2026-12-18T10:00:00", "2026-12-19T10:00:00", "2026-12-17T10:00:00"),
        ("pan, leche", "pan, leche, x9", "pan"),
        ("alta", "altax", "ylta"),
        ("y", "yx", "z"),
        ("", "x", "y"),
        ("31/02/2026", "31/02/2026x", "y1/02/2026"),  # not a date: a text
    ],
)
def test_a_text_changes_in_its_own_format(text: str, more: str, less: str) -> None:
    assert (altered_text(text, MORE), altered_text(text, LESS)) == (more, less)


@pytest.mark.parametrize(
    ("value", "more", "less"),
    [
        (True, False, False),
        (12, 13, 11),
        (Decimal("1.5"), Decimal("2.5"), Decimal("0.5")),
        (date(2026, 12, 18), date(2026, 12, 19), date(2026, 12, 17)),
        (datetime(2026, 12, 18, 9), datetime(2026, 12, 19, 9), datetime(2026, 12, 17, 9)),
        ([["a", "b"], ["1", "2"]], [["a", "b"], ["1", "3"]], [["a", "b"], ["1", "1"]]),
    ],
)
def test_a_converted_value_changes_by_its_kind(value: Any, more: Any, less: Any) -> None:
    assert (alter(value, MORE), alter(value, LESS)) == (more, less)


@pytest.mark.parametrize("value", [None, object(), [["solo"]], [["a"], []], ["a", "b"]])
def test_what_it_doesn_t_know_how_to_change_stays(value: Any) -> None:
    assert alter(value, MORE) is lictor_gherkin._UNCHANGED


def _step(name: str, kind: str = "then") -> SimpleNamespace:
    return SimpleNamespace(type=kind, name=name)


@pytest.fixture
def bdd(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, object]]:
    """pytest-bdd's two functions, parsing each step name as `parsed` says."""
    parsed: dict[str, dict[str, object]] = {}
    module = types.ModuleType("pytest_bdd.scenario")
    module.get_step_function = lambda request, step: SimpleNamespace()
    module.parse_step_arguments = lambda step, context: parsed[step.name]
    monkeypatch.setitem(sys.modules, "pytest_bdd.scenario", module)
    return parsed


def _told(monkeypatch: pytest.MonkeyPatch, **told: Any) -> lictor_gherkin._Gherkin:
    gherkin = lictor_gherkin._Gherkin(told["mode"], told.get("target"), told.get("targets"))
    monkeypatch.setattr(lictor_gherkin, "GHERKIN", gherkin)
    return gherkin


def _then(name: str, func: Callable[..., None], args: dict[str, Any]) -> None:
    lictor_gherkin.pytest_bdd_before_step_call(None, _step(name), func, args)


def _two_values(pedido: Any, total: int, moneda: str) -> None:
    assert pedido.total == total


def test_it_numbers_the_values_of_the_then_and_names_the_one_unread(
    monkeypatch: pytest.MonkeyPatch, bdd: dict[str, dict[str, object]]
) -> None:
    gherkin = _told(monkeypatch, mode="control")
    bdd["el total es 12 €"] = {"total": 12, "moneda": "€", "verbo": "es"}
    args = {"pedido": object(), "total": 12, "moneda": "€"}

    _then("el total es 12 €", _two_values, args)
    lictor_gherkin.pytest_bdd_before_step_call(None, _step("x", "when"), _ignores, {"total": 1})

    assert gherkin.values == 2
    assert gherkin.alterable == [0, 1]
    assert gherkin.unread == [["el total es 12 €", "moneda", "€"]]
    assert gherkin.unreceived == 1  # `verbo`: an alternative of a regex, which is no veto
    assert not gherkin.altered
    assert args["total"] == 12


@pytest.mark.parametrize(("mode", "total"), [(MORE, 13), (LESS, 11)])
def test_a_mode_changes_only_the_value_it_is_told(
    monkeypatch: pytest.MonkeyPatch, bdd: dict[str, dict[str, object]], mode: str, total: int
) -> None:
    gherkin = _told(monkeypatch, mode=mode, target=0)
    bdd["el total es 12 €"] = {"total": 12, "moneda": "€"}
    args = {"pedido": object(), "total": 12, "moneda": "€"}

    _then("el total es 12 €", _two_values, args)

    assert (args["total"], args["moneda"], gherkin.altered) == (total, "€", True)


def test_a_table_and_a_doc_string_count_after_the_parsed_values(
    monkeypatch: pytest.MonkeyPatch, bdd: dict[str, dict[str, object]]
) -> None:
    gherkin = _told(monkeypatch, mode=MORE, target=2)
    bdd["las líneas"] = {"n": 2}

    def lines(n: int, datatable: list[list[str]], docstring: str) -> None:
        assert n
        assert datatable
        assert docstring

    args = {"n": 2, "datatable": [["ref"], ["pan"]], "docstring": "hola"}
    _then("las líneas", lines, args)

    assert gherkin.values == 3
    assert args["docstring"] == "holax"


def test_without_pytest_bdd_s_functions_it_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gherkin = _told(monkeypatch, mode="control")
    monkeypatch.setitem(sys.modules, "pytest_bdd.scenario", types.ModuleType("vacío"))

    _then("el total es 12", _compares, {"total": 12})

    assert gherkin.values == 0


def _item(*tags: str) -> SimpleNamespace:
    return SimpleNamespace(iter_markers=lambda: [SimpleNamespace(name=t) for t in tags])


def test_targets_keep_only_their_tests_when_the_tags_tell_them_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _told(monkeypatch, mode=MORE, target=0, targets=["ENR-005"])
    deselected: list[Any] = []

    def told_deselected(items: list[Any]) -> None:
        deselected.extend(items)

    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=told_deselected))
    wanted, other = _item("ENR-005", "lento"), _item("ENR-001")
    items = [wanted, other]

    lictor_gherkin.pytest_collection_modifyitems(config, items)

    assert (items, deselected) == ([wanted], [other])


def test_tags_that_don_t_reach_the_marks_run_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    _told(monkeypatch, mode=MORE, target=0, targets=["ENR-005"])
    items = [_item(), _item("x")]
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=None))

    lictor_gherkin.pytest_collection_modifyitems(config, items)

    assert len(items) == 2


def test_what_it_reports_of_each_test(monkeypatch: pytest.MonkeyPatch) -> None:
    gherkin = _told(monkeypatch, mode=MORE, target=0)
    gherkin.values, gherkin.alterable, gherkin.altered = 2, [0, 1], True
    gherkin.unread = [["paso", "x", "«1»"]]
    item = SimpleNamespace(user_properties=[])
    hook = lictor_gherkin.pytest_runtest_makereport(item, SimpleNamespace(when="call"))
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(None)

    assert dict(item.user_properties) == {
        "lictor.gherkin.values": "2",
        "lictor.gherkin.alterable": "0,1",
        "lictor.gherkin.unread": json.dumps([["paso", "x", "«1»"]], ensure_ascii=False),
        "lictor.gherkin.unreceived": "0",
        "lictor.gherkin.altered": "1",
    }


def test_a_long_value_is_cut_in_the_report() -> None:
    shown = lictor_gherkin._shown("x" * 100)

    assert len(shown) == 60
    assert shown.endswith("…")
