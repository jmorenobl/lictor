"""What breaks in the public API against the run's base, and what is said of it (ADR-0017).

`test_lictor_api.py` fixes what breaks between two real trees; these fix each rule of the domain
on reports written by hand, what a report must hold to be one, and how a comparison reaches the
verdict, the journal, the refactor and the progress lines.
"""

from typing import Any

import pytest

from lictor.domain.api import (
    IMPLEMENTER_API,
    NAMED,
    ApiBase,
    all_breaks,
    api_breaks,
    api_report,
    break_from_record,
    break_line,
    break_record,
    breaks_from_record,
    broken_reason,
    compare_api,
    describe_api,
    is_accepted,
    named_breaks,
    param_breaks,
)
from lictor.domain.controls import CommandControl
from lictor.domain.model import Event
from lictor.domain.progress import RunProgress, api_start_names, api_start_record, api_warning
from lictor.domain.refactor import (
    Measure,
    measures_from_record,
    measures_record,
    oracle_measures,
    refactor_regressions,
)
from lictor.domain.reports import (
    ApiBreak,
    ApiComparison,
    ApiParam,
    ApiReport,
    ApiSymbol,
    InvalidReport,
    MissingReport,
)
from lictor.domain.results import (
    CommandEntry,
    Executed,
    Judged,
    Ran,
    RecordedApi,
    RecordedExecuted,
    RecordedJudged,
    RecordedSkipped,
    control_from_record,
    control_record,
)
from lictor.domain.verdicts import Execution, Failed, Passed, api_verdict

_SHA = "a" * 40
_API = CommandControl(id="api", level="L1", run="api {out}", report="api", mode="measure")
_RAN = Ran(("api",), Execution(0, 60), "log")


def _p(name: str, kind: Any = "positional_or_keyword", default: bool = False) -> ApiParam:
    return ApiParam(name, kind, default)


def _s(
    name: str,
    kind: Any = "function",
    params: tuple[ApiParam, ...] | None = (),
    *,
    origin: str | None = None,
    is_async: bool = False,
) -> ApiSymbol:
    return ApiSymbol(name, kind, origin or name, "src/p.py", 1, is_async, params)


def _keys(breaks: Any) -> set[tuple[str, str, str]]:
    return {(b.name, b.change, b.param) for b in breaks}


# Reading a report.


def _raw(**changes: object) -> dict[str, object]:
    symbol: dict[str, object] = {
        "name": "p.f",
        "kind": "function",
        "origin": "p.f",
        "file": "src/p.py",
        "line": 3,
        "async": True,
        "params": [{"name": "x", "kind": "keyword", "default": True}],
    }
    return {"symbols": [{**symbol, **changes}]}


def test_a_report_reads_every_fact_of_a_name() -> None:
    report = api_report(_raw())

    assert report == ApiReport(
        (ApiSymbol("p.f", "function", "p.f", "src/p.py", 3, True, (_p("x", "keyword", True),)),)
    )


def test_a_name_without_params_or_async_is_not_called_and_not_async() -> None:
    symbol = {"name": "p.f", "kind": "function", "origin": "p.f", "file": "src/p.py", "line": 3}

    found = api_report({"symbols": [symbol]})

    assert found == ApiReport((ApiSymbol("p.f", "function", "p.f", "src/p.py", 3),))


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ([], "no tiene 'symbols'"),
        ({"symbols": {}}, "no tiene 'symbols'"),
        ({"symbols": [1]}, "el nombre 1 no es un objeto"),
        (_raw(name=""), "el nombre 1 no tiene 'name' y 'origin'"),
        (_raw(name=1), "el nombre 1 no tiene 'name' y 'origin'"),
        (_raw(origin=""), "el nombre 1 no tiene 'name' y 'origin'"),
        (_raw(origin=None), "el nombre 1 no tiene 'name' y 'origin'"),
        (_raw(kind="lambda"), "el nombre 1 (p.f) tiene una clase desconocida: 'lambda'"),
        (_raw(file="/src/p.py"), "el nombre 1 (p.f) está fuera del árbol medido"),
        (_raw(file="src/../../p.py"), "el nombre 1 (p.f) está fuera del árbol medido"),
        (_raw(file=""), "el nombre 1 (p.f) está fuera del árbol medido"),
        (_raw(file=3), "el nombre 1 (p.f) está fuera del árbol medido"),
        (_raw(line=0), "el nombre 1 (p.f) está fuera del árbol medido"),
        (_raw(line=True), "el nombre 1 (p.f) está fuera del árbol medido"),
        (_raw(line="3"), "el nombre 1 (p.f) está fuera del árbol medido"),
        (_raw(**{"async": 1}), "el nombre 1 (p.f) tiene un 'async' que no es booleano"),
        (_raw(params="x"), "el nombre 1 (p.f) tiene unos parámetros que no son una lista"),
        (_raw(params=[1]), "el nombre 1 (p.f) tiene un parámetro 1 que no es válido: 1"),
        (
            _raw(params=[{"name": "", "kind": "keyword", "default": True}]),
            "el nombre 1 (p.f) tiene un parámetro 1 que no es válido",
        ),
        (
            _raw(params=[{"kind": "keyword", "default": True}]),
            "el nombre 1 (p.f) tiene un parámetro 1 que no es válido",
        ),
        (
            _raw(params=[{"name": "x", "kind": "otro", "default": True}]),
            "el nombre 1 (p.f) tiene un parámetro 1 que no es válido",
        ),
        (
            _raw(params=[{"name": "x", "kind": "keyword", "default": 1}]),
            "el nombre 1 (p.f) tiene un parámetro 1 que no es válido",
        ),
        (
            _raw(params=[{"name": "x", "kind": "keyword"}]),
            "el nombre 1 (p.f) tiene un parámetro 1 que no es válido",
        ),
    ],
)
def test_a_report_that_cant_be_one_says_why(data: object, reason: str) -> None:
    found = api_report(data)

    assert isinstance(found, InvalidReport)
    assert found.reason.startswith(reason)


def _symbols(*extra: object) -> dict[str, object]:
    raw = _raw()
    first = raw["symbols"]
    assert isinstance(first, list)
    return {"symbols": [*first, *extra]}


def test_a_name_twice_is_no_report() -> None:
    first = _symbols()["symbols"]
    assert isinstance(first, list)
    twice = _symbols(*first)

    assert api_report(twice) == InvalidReport("el nombre p.f está dos veces")


def test_the_second_name_is_numbered_as_such() -> None:
    assert api_report(_symbols(2)) == InvalidReport("el nombre 2 no es un objeto")


# The parameters of a call.


@pytest.mark.parametrize(
    ("before", "after", "breaks"),
    [
        ((_p("a"),), (_p("a"),), set()),
        ((_p("a"),), (), {("f", "param_removed", "a")}),
        ((_p("a"),), (_p("a"), _p("b", default=True)), set()),
        ((_p("a"),), (_p("a"), _p("b")), {("f", "param_new", "b")}),
        ((_p("a"),), (_p("a"), _p("b", "keyword")), {("f", "param_new", "b")}),
        ((_p("a"),), (_p("a"), _p("rest", "var_positional")), set()),
        ((_p("a"),), (_p("a"), _p("kw", "var_keyword")), set()),
        ((_p("a", default=True),), (_p("a"),), {("f", "param_default", "a")}),
        ((_p("a"),), (_p("a", default=True),), set()),
        ((_p("a", "keyword"),), (_p("a"),), set()),
        ((_p("a", "positional"),), (_p("a"),), set()),
        ((_p("a"),), (_p("a", "keyword"),), {("f", "param_kind", "a")}),
        ((_p("a"),), (_p("a", "positional"),), {("f", "param_kind", "a")}),
        ((_p("a", "keyword"),), (_p("a", "positional"),), {("f", "param_kind", "a")}),
        ((_p("a", "positional"),), (_p("a", "keyword"),), {("f", "param_kind", "a")}),
        ((_p("a", "positional"),), (_p("b", "positional"),), set()),
        (
            (_p("a", "positional"),),
            (_p("b"),),
            {("f", "param_removed", "a"), ("f", "param_new", "b")},
        ),
        (
            (_p("a", "positional"), _p("b", "positional")),
            (_p("x", "positional"),),
            {("f", "param_removed", "b")},
        ),
        (
            (_p("a"), _p("b")),
            (_p("b"), _p("a")),
            {("f", "param_moved", "a"), ("f", "param_moved", "b")},
        ),
        ((_p("a"), _p("b")), (_p("b"),), {("f", "param_removed", "a"), ("f", "param_moved", "b")}),
        ((_p("a", "keyword"), _p("b", "keyword")), (_p("b", "keyword"), _p("a", "keyword")), set()),
        ((_p("a"), _p("b")), (_p("a"), _p("b", "keyword", True)), {("f", "param_kind", "b")}),
        (
            (_p("a", default=True),),
            (_p("a", "keyword"),),
            {("f", "param_kind", "a"), ("f", "param_default", "a")},
        ),
        (
            (_p("a", default=True), _p("b")),
            (_p("b"), _p("a")),
            {("f", "param_moved", "a"), ("f", "param_moved", "b"), ("f", "param_default", "a")},
        ),
        ((_p("args", "var_positional"),), (), {("f", "param_removed", "args")}),
        (
            (_p("args", "var_positional"), _p("b", "keyword")),
            (),
            {("f", "param_removed", "args"), ("f", "param_removed", "b")},
        ),
        ((_p("kw", "var_keyword"),), (), {("f", "param_removed", "kw")}),
        ((_p("args", "var_positional"),), (_p("other", "var_positional"),), set()),
        (
            (_p("args", "var_positional"),),
            (_p("kw", "var_keyword"),),
            {("f", "param_removed", "args")},
        ),
        (
            (_p("args", "var_positional"),),
            (_p("args"),),
            {("f", "param_removed", "args"), ("f", "param_new", "args")},
        ),
    ],
)
def test_what_a_call_that_worked_can_no_longer_do(
    before: tuple[ApiParam, ...], after: tuple[ApiParam, ...], breaks: set[tuple[str, str, str]]
) -> None:
    assert _keys(param_breaks("f", before, after)) == breaks


def test_each_break_of_a_parameter_says_it() -> None:
    found = param_breaks(
        "f",
        (_p("a", default=True), _p("b"), _p("c"), _p("rest", "var_positional")),
        (_p("c"), _p("a", "keyword"), _p("d")),
    )

    assert [b.detail for b in found] == [
        "el parámetro a pasa a ser solo por nombre",
        "el parámetro a pierde su valor por defecto",
        "pierde el parámetro b",
        "el parámetro c pasa de la posición 3 a la 1",
        "pierde *args",
        "parámetro nuevo d sin valor por defecto",
    ]


def test_losing_kwargs_says_it() -> None:
    (lost,) = param_breaks("f", (_p("kw", "var_keyword"),), ())

    assert lost.detail == "pierde **kwargs"


# What breaks.


def test_a_removed_name_is_told_and_what_it_held_goes_with_it() -> None:
    base = ApiReport((_s("p", "module", None), _s("p.C", "class"), _s("p.C.m", "method")))

    found = all_breaks(base, ApiReport((_s("p", "module", None),)))

    assert [(b.name, b.change, b.detail) for b in found] == [("p.C", "removed", "ya no existe")]


def test_a_removed_module_takes_its_names() -> None:
    base = ApiReport((_s("p", "module", None), _s("p.sub", "module", None), _s("p.sub.f")))

    found = all_breaks(base, ApiReport((_s("p", "module", None),)))

    assert _keys(found) == {("p.sub", "removed", "")}


def test_a_class_that_stops_being_one_takes_its_members() -> None:
    base = ApiReport((_s("p.C", "class"), _s("p.C.m", "method"), _s("p.C.k", "constant")))

    found = all_breaks(base, ApiReport((_s("p.C"),)))

    assert [(b.name, b.change, b.detail) for b in found] == [
        ("p.C", "kind", "pasa de clase a función")
    ]


def test_a_member_whose_class_kept_being_one_is_told() -> None:
    base = ApiReport((_s("p.C", "class"), _s("p.C.m", "method")))

    found = all_breaks(base, ApiReport((_s("p.C", "class"),)))

    assert _keys(found) == {("p.C.m", "removed", "")}


def test_a_member_whose_parent_stays_a_container_of_another_kind_is_told() -> None:
    base = ApiReport((_s("p.C", "class", None), _s("p.C.m", "method")))

    found = all_breaks(base, ApiReport((_s("p.C", "module", None),)))

    assert _keys(found) == {("p.C", "kind", ""), ("p.C.m", "removed", "")}


def test_a_name_under_one_that_wasnt_in_the_base_is_its_own() -> None:
    found = all_breaks(ApiReport((_s("p.x.f"),)), ApiReport(()))

    assert _keys(found) == {("p.x.f", "removed", "")}


def test_a_kind_change_hides_the_rest_of_its_changes() -> None:
    base = ApiReport((_s("p.f", "function", (_p("a"),)),))
    after = ApiReport((_s("p.f", "method", (), is_async=True),))

    found = all_breaks(base, after)

    assert [(b.change, b.detail) for b in found] == [("kind", "pasa de función a método")]


@pytest.mark.parametrize(
    ("before", "after", "detail"),
    [(False, True, "pasa a async"), (True, False, "deja de ser async")],
)
def test_async_is_told_both_ways(before: bool, after: bool, detail: str) -> None:
    found = all_breaks(
        ApiReport((_s("p.f", is_async=before),)), ApiReport((_s("p.f", is_async=after),))
    )

    assert [(b.change, b.detail) for b in found] == [("async", detail)]


@pytest.mark.parametrize(("before", "after"), [(None, ()), ((_p("a"),), None), (None, None)])
def test_a_signature_unknown_on_either_side_isnt_compared(
    before: tuple[ApiParam, ...] | None, after: tuple[ApiParam, ...] | None
) -> None:
    found = all_breaks(
        ApiReport((_s("p.C", "class", before),)), ApiReport((_s("p.C", "class", after),))
    )

    assert found == ()


def test_one_object_reached_two_ways_breaks_once_by_its_shortest_name() -> None:
    base = ApiReport(
        (
            _s("p.a.b.f", params=(_p("x"),), origin="p.a.b.f"),
            _s("p.zz", params=(_p("x"),), origin="p.a.b.f"),
            _s("p.aa", params=(_p("x"),), origin="p.a.b.f"),
        )
    )
    after = ApiReport(tuple(_s(s.name, origin=s.origin) for s in base.symbols))

    (found,) = all_breaks(base, after)

    assert (found.name, found.aliases) == ("p.aa", ("p.a.b.f", "p.zz"))


def test_two_objects_with_one_name_each_break_apart() -> None:
    base = ApiReport((_s("p.f", origin="p.x.f"), _s("p.g", origin="p.y.g")))

    found = all_breaks(base, ApiReport(()))

    assert [(b.name, b.aliases) for b in found] == [("p.f", ()), ("p.g", ())]


def test_a_re_export_removed_breaks_only_the_name_that_went() -> None:
    base = ApiReport((_s("p.f", origin="p.m.f"), _s("p.m.f", origin="p.m.f")))

    found = all_breaks(base, ApiReport((_s("p.m.f", origin="p.m.f"),)))

    assert [(b.name, b.aliases) for b in found] == [("p.f", ())]


def test_breaks_come_in_order_of_name() -> None:
    base = ApiReport((_s("p.b"), _s("p.a", params=(_p("x"), _p("y")))))

    found = all_breaks(base, ApiReport((_s("p.a"),)))

    assert [(b.name, b.param) for b in found] == [("p.a", "x"), ("p.a", "y"), ("p.b", "")]


def test_two_breaks_of_one_name_are_two() -> None:
    found = all_breaks(
        ApiReport((_s("p.f", params=(_p("a"),)),)),
        ApiReport((_s("p.f", params=(_p("b"),), is_async=True),)),
    )

    assert _keys(found) == {
        ("p.f", "async", ""),
        ("p.f", "param_removed", "a"),
        ("p.f", "param_new", "b"),
    }


# What the person accepts.


@pytest.mark.parametrize(
    ("accepted", "taken"),
    [
        (["p.C.m"], True),
        (["p.C"], True),
        (["p"], True),
        (["p.alias"], True),
        (["p.alias.m"], False),
        (["p.C.mm"], False),
        (["p.Cx"], False),
        (["p.C.m.x"], False),
        ([], False),
    ],
)
def test_a_break_is_accepted_by_its_name_an_alias_or_what_holds_it(
    accepted: list[str], taken: bool
) -> None:
    item = ApiBreak("p.C.m", "removed", aliases=("p.alias",))

    assert is_accepted(item, accepted) is taken


def test_api_breaks_leaves_out_what_the_person_accepts() -> None:
    base = ApiReport((_s("p.f"), _s("p.g")))

    found = api_breaks(base, ApiReport(()), ["p.g"])

    assert _keys(found) == {("p.f", "removed", "")}
    assert _keys(api_breaks(base, ApiReport(()))) == {
        ("p.f", "removed", ""),
        ("p.g", "removed", ""),
    }


def test_two_breaks_are_one_whatever_their_words() -> None:
    assert ApiBreak("p.f", "removed", detail="a", aliases=("x",)) == ApiBreak("p.f", "removed")
    assert ApiBreak("p.f", "removed") != ApiBreak("p.f", "removed", "x")
    assert ApiBreak("p.f", "removed") != ApiBreak("p.g", "removed")
    assert ApiBreak("p.f", "removed") != ApiBreak("p.f", "kind")


# Comparing with the base.


def test_without_a_base_or_a_report_there_is_nothing_to_compare() -> None:
    report = ApiReport((_s("p.f"),))
    base = ApiBase(_SHA, ApiReport(()))

    assert compare_api(report, None) is report
    assert compare_api(MissingReport(), base) == MissingReport()
    assert compare_api(InvalidReport("x"), base) == InvalidReport("x")


def test_a_base_that_couldnt_be_measured_is_a_problem_naming_it() -> None:
    compared = compare_api(ApiReport(()), ApiBase(_SHA, "no terminó"))

    assert compared == ApiComparison(
        ApiReport(()), _SHA, problem=f"No se pudo medir la API de la base {'a' * 12}: no terminó"
    )


def test_a_comparison_splits_what_the_person_accepts() -> None:
    base = ApiBase(_SHA, ApiReport((_s("p.f"), _s("p.g"))), accepted=("p.g",))

    compared = compare_api(ApiReport(()), base)

    assert isinstance(compared, ApiComparison)
    assert compared.report == ApiReport(())
    assert _keys(compared.broken) == {("p.f", "removed", "")}
    assert _keys(compared.accepted) == {("p.g", "removed", "")}
    assert _keys(compared.breaks) == {("p.f", "removed", ""), ("p.g", "removed", "")}
    assert compared.problem is None
    assert compared.base == _SHA


# The verdict.


def _comparison(*broken: ApiBreak, accepted: tuple[ApiBreak, ...] = ()) -> ApiComparison:
    return ApiComparison(ApiReport((_s("p.f"),)), _SHA, broken, accepted)


def test_facts_without_a_base_pass() -> None:
    assert api_verdict(ApiReport(()), Execution(0, 60)) == Passed()


def test_nothing_broken_passes_and_what_is_accepted_too() -> None:
    accepted = (ApiBreak("p.g", "removed", detail="ya no existe"),)

    assert api_verdict(_comparison(), Execution(0, 60)) == Passed()
    assert api_verdict(_comparison(accepted=accepted), Execution(0, 60)) == Passed()


def test_what_breaks_fails_naming_it_and_what_to_do() -> None:
    verdict = api_verdict(
        _comparison(ApiBreak("p.f", "removed", detail="ya no existe")), Execution(0, 60)
    )

    assert verdict == Failed(
        f"Se rompe un nombre de la API pública de la base ({'a' * 12}):\n- `p.f`: ya no existe"
    )


def test_a_base_that_couldnt_be_measured_never_passes() -> None:
    verdict = api_verdict(ApiComparison(ApiReport(()), _SHA, problem="sin base"), Execution(0, 60))

    assert verdict == Failed("sin base")


@pytest.mark.parametrize(
    ("report", "execution", "reason"),
    [
        (MissingReport(), Execution(3, 60), "Sin informe de API (código de salida 3)"),
        (InvalidReport("roto"), Execution(0, 60), "El informe de API no es válido: roto"),
        (ApiReport(()), Execution(2, 60), "El comando de API falló con el código 2"),
        (ApiReport(()), Execution(0, 60, timed_out=True), "Se cortó a los 60 s, su tiempo límite"),
    ],
)
def test_an_api_command_that_didnt_end_well_fails(
    report: ApiReport | MissingReport | InvalidReport, execution: Execution, reason: str
) -> None:
    assert api_verdict(report, execution) == Failed(reason)


def test_the_reason_counts_several_and_names_the_first_ones() -> None:
    many = tuple(ApiBreak(f"p.f{i:02}", "removed", detail="ya no existe") for i in range(NAMED + 2))

    reason = broken_reason(_comparison(*many))

    lines = reason.splitlines()
    assert lines[0] == f"Se rompen {NAMED + 2} nombres de la API pública de la base ({'a' * 12}):"
    assert lines[1] == "- `p.f00`: ya no existe"
    assert lines[NAMED] == f"- `p.f{NAMED - 1}`: ya no existe"
    assert lines[NAMED + 1] == "- y 2 más"
    assert len(lines) == NAMED + 2


def test_exactly_named_breaks_are_all_named() -> None:
    many = [ApiBreak(f"p.f{i}", "removed", detail="d") for i in range(NAMED)]

    assert len(named_breaks(many)) == NAMED
    assert break_line(many[0]) == "`p.f0`: d"


@pytest.mark.parametrize(
    ("args", "phrase"),
    [
        ((1, None, 0, 0), "1 nombre público"),
        ((7, None, 3, 1), "7 nombres públicos"),
        ((7, _SHA, 0, 0), f"7 nombres públicos; nada roto respecto a {'a' * 12}"),
        ((7, _SHA, 1, 0), f"7 nombres públicos; 1 rotura respecto a {'a' * 12}"),
        ((7, _SHA, 2, 0), f"7 nombres públicos; 2 roturas respecto a {'a' * 12}"),
        ((7, _SHA, 0, 1), f"7 nombres públicos; 1 aceptada respecto a {'a' * 12}"),
        ((7, _SHA, 2, 3), f"7 nombres públicos; 2 roturas, 3 aceptadas respecto a {'a' * 12}"),
    ],
)
def test_what_a_measure_says_in_one_phrase(args: tuple[Any, ...], phrase: str) -> None:
    assert describe_api(*args) == phrase


def test_a_measure_without_comparison_says_why() -> None:
    assert describe_api(2, _SHA, 0, 0, "sin base") == "2 nombres públicos; sin comparar: sin base"


def test_the_implementer_hears_the_fixed_sentence() -> None:
    assert IMPLEMENTER_API == (
        "- No quites ni cambies de forma incompatible la API pública que ya existía; lo que "
        "rompas sale en el informe."
    )


# Records.


def test_a_break_reads_back_as_it_was_written() -> None:
    item = ApiBreak("p.f", "param_kind", "a", "el parámetro a pasa a ser solo por nombre")

    back = break_from_record(break_record(item))

    assert back == item
    assert back is not None
    assert back.detail == item.detail


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"change": "removed"},
        {"name": 1, "change": "removed"},
        {"name": "p.f", "change": "otro"},
        {"name": "p.f", "change": "removed", "param": 1},
        {"name": "p.f", "change": "removed", "detail": None},
    ],
)
def test_a_break_that_doesnt_follow_its_record_reads_as_none(raw: object) -> None:
    assert break_from_record(raw) is None


def test_a_break_without_param_or_detail_reads_with_empty_ones() -> None:
    back = break_from_record({"name": "p.f", "change": "removed"})

    assert back == ApiBreak("p.f", "removed")
    assert back is not None
    assert (back.param, back.detail) == ("", "")


def test_a_list_of_breaks_reads_whole_or_not_at_all() -> None:
    good = break_record(ApiBreak("p.f", "removed"))

    assert breaks_from_record([good]) == (ApiBreak("p.f", "removed"),)
    assert breaks_from_record([good, {"name": 1}]) is None
    assert breaks_from_record("x") is None
    assert breaks_from_record([]) == ()


def _entry(report: ApiReport | ApiComparison, verdict: Passed | Failed) -> CommandEntry:
    return CommandEntry(_API, Executed(_RAN, Judged(report, verdict)))


def test_the_journal_keeps_what_was_compared_and_reads_it_back() -> None:
    broken = ApiBreak("p.f", "removed", detail="ya no existe")
    accepted = ApiBreak("p.g", "removed", detail="ya no existe")
    record = control_record(_entry(_comparison(broken, accepted=(accepted,)), Passed()))

    assert record["api"] == {
        "symbols": 1,
        "base": _SHA,
        "broken": [break_record(broken)],
        "accepted": [break_record(accepted)],
        "problem": None,
    }
    back = control_from_record(record)
    assert isinstance(back, RecordedExecuted)
    assert isinstance(back.judgement, RecordedJudged)
    assert back.judgement.api == RecordedApi(1, _SHA, (broken,), (accepted,))


def test_the_journal_keeps_why_it_couldnt_compare() -> None:
    problem = ApiComparison(ApiReport(()), _SHA, problem="sin base")

    back = control_from_record(control_record(_entry(problem, Failed("sin base"))))

    assert isinstance(back, RecordedExecuted)
    assert isinstance(back.judgement, RecordedJudged)
    assert back.judgement.api == RecordedApi(0, _SHA, problem="sin base")


def test_the_journal_keeps_the_facts_alone_without_a_base() -> None:
    record = control_record(_entry(ApiReport((_s("p.f"), _s("p.g"))), Passed()))

    assert record["api"] == {"symbols": 2, "base": None}
    back = control_from_record(record)
    assert isinstance(back, RecordedExecuted)
    assert isinstance(back.judgement, RecordedJudged)
    assert back.judgement.api == RecordedApi(2)


@pytest.mark.parametrize(
    "api",
    [
        "x",
        {"base": None},
        {"symbols": True, "base": None},
        {"symbols": 1, "base": 3},
        {"symbols": 1, "base": None, "problem": 3},
        {"symbols": 1, "base": None, "broken": "x"},
        {"symbols": 1, "base": None, "accepted": [{"name": 1}]},
    ],
)
def test_an_api_record_that_doesnt_follow_leaves_the_control_out(api: object) -> None:
    record = control_record(_entry(ApiReport(()), Passed()))

    assert control_from_record({**record, "api": api}) is None


# The refactor.


def test_a_comparison_is_a_measure_of_its_breaks() -> None:
    broken, accepted = ApiBreak("p.f", "removed"), ApiBreak("p.g", "removed")

    measures = oracle_measures([_entry(_comparison(broken, accepted=(accepted,)), Failed("x"))])

    assert measures == {"api": Measure(breaks=frozenset({broken, accepted}))}


def test_no_comparison_is_no_measure() -> None:
    problem = ApiComparison(ApiReport(()), _SHA, problem="sin base")

    assert oracle_measures([_entry(problem, Failed("sin base"))]) == {}
    assert oracle_measures([_entry(ApiReport(()), Passed())]) == {}


def test_the_checkpoint_keeps_the_breaks_and_reads_them_back() -> None:
    breaks = frozenset({ApiBreak("p.g", "removed", detail="d"), ApiBreak("p.f", "kind")})

    record = measures_record({"api": Measure(breaks=breaks)})

    names = record["api"]["api_breaks"]
    assert isinstance(names, list)
    assert [b["name"] for b in names] == ["p.f", "p.g"]
    assert measures_from_record(record) == {"api": Measure(breaks=breaks, misses=None)}


def test_a_checkpoint_without_breaks_reads_none() -> None:
    record = measures_record({"api": Measure()})

    assert "api_breaks" not in record["api"]
    assert measures_from_record({"api": {"api_breaks": "x"}})["api"].breaks is None


def test_a_refactor_may_keep_the_breaks_of_its_green() -> None:
    breaks = frozenset({ApiBreak("p.f", "removed")})

    assert (
        refactor_regressions({"api": Measure(breaks=breaks)}, {"api": Measure(breaks=breaks)}) == []
    )
    assert (
        refactor_regressions({"api": Measure(breaks=breaks)}, {"api": Measure(breaks=frozenset())})
        == []
    )


def test_a_refactor_that_breaks_something_new_is_discarded_even_if_it_mends_another() -> None:
    green = frozenset({ApiBreak("p.f", "removed")})
    refactor = frozenset({ApiBreak("p.g", "kind", detail="pasa de función a clase")})

    found = refactor_regressions({"api": Measure(breaks=green)}, {"api": Measure(breaks=refactor)})

    assert found == ["api: rompe `p.g`: pasa de función a clase, y el verde no"]


def test_what_a_refactor_breaks_is_told_in_order_of_name() -> None:
    new = frozenset(ApiBreak(f"p.{n}", "removed", detail="d") for n in ("c", "a", "d", "b"))

    found = refactor_regressions({"api": Measure(breaks=frozenset())}, {"api": Measure(breaks=new)})

    assert found == [f"api: rompe `p.{n}`: d, y el verde no" for n in ("a", "b", "c", "d")]


def test_a_refactor_without_a_measure_of_the_api_is_discarded() -> None:
    green = {"api": Measure(breaks=frozenset())}

    assert refactor_regressions(green, {}) == ["api: sin medida, y en el verde la tenía"]
    assert refactor_regressions({"api": Measure()}, {}) == []


# The progress of a run.


def _recorded(*broken: ApiBreak) -> RecordedExecuted:
    record = control_record(_entry(_comparison(*broken), Passed()))
    back = control_from_record(record)
    assert isinstance(back, RecordedExecuted)
    return back


def test_a_warning_names_the_first_breaks_and_counts_the_rest() -> None:
    breaks = [ApiBreak(f"p.f{i}", "removed", detail="ya no existe") for i in range(5)]

    warning = api_warning([_recorded(*breaks)])

    assert warning == (
        "rompe la API pública de la base: `p.f0` (ya no existe), `p.f1` (ya no existe), "
        "`p.f2` (ya no existe) y 2 más; lo detalla report.md."
    )


def test_three_breaks_are_named_without_a_count() -> None:
    breaks = [ApiBreak(f"p.f{i}", "removed", detail="d") for i in range(3)]

    warning = api_warning([_recorded(*breaks)])

    assert warning is not None
    assert "más" not in warning


def test_a_warning_looks_past_the_controls_that_compared_nothing() -> None:
    facts = control_from_record(control_record(_entry(ApiReport(()), Passed())))
    lint = control_from_record(
        {**control_record(_entry(ApiReport(()), Passed())), "judgement": "unjudged", "changes": []}
    )
    assert facts is not None
    assert lint is not None

    skipped = RecordedSkipped("measure", "sin plugin")
    found = _recorded(ApiBreak("p.f", "removed", detail="d"))

    warning = api_warning([skipped, lint, facts, found])

    assert warning is not None
    assert "`p.f` (d)" in warning


def test_nothing_broken_warns_nothing() -> None:
    assert api_warning([_recorded()]) is None
    assert api_warning([]) is None


def _event(kind: str, data: dict[str, object]) -> Event:
    return Event(
        id="x",
        type=kind,
        source="lictor",
        time="2026-11-25T10:00:00Z",
        runid="R1",
        scenarioid="S-1",
        data=data,
    )


def _verify(ok: bool, role: str = "implement") -> Event:
    broken = ApiBreak("p.f", "removed", detail="ya no existe")
    record = control_record(_entry(_comparison(broken), Failed("x")))
    data: dict[str, object] = {"ok": ok, "role": role, "controls": {"api": record}, "attempt": 1}
    return _event("lictor.verify.finished.v2", data)


def _green() -> Event:
    return _event("lictor.scenario.green.v1", {"attempt": 1})


def test_a_green_that_breaks_the_api_is_told_with_a_warning() -> None:
    progress = RunProgress()
    progress.observe(_verify(ok=True))

    lines = progress.observe(_green())

    assert lines[-1] == (
        "⚠ S-1: rompe la API pública de la base: `p.f` (ya no existe); lo detalla report.md."
    )
    assert progress.observe(_green())[-1].startswith("S-1: verde")  # told once


@pytest.mark.parametrize(("ok", "role"), [(False, "implement"), (True, "refactor")])
def test_a_failed_or_a_refactor_verification_warns_no_green(ok: bool, role: str) -> None:
    progress = RunProgress()
    progress.observe(_verify(ok=ok, role=role))

    assert not any(line.startswith("⚠") for line in progress.observe(_green()))


def test_the_whole_reason_names_every_break() -> None:
    """What `check` leaves in `api.error.txt`: no break counted instead of named."""
    many = tuple(ApiBreak(f"p.f{i:02}", "removed", detail="ya no existe") for i in range(NAMED + 2))

    lines = broken_reason(_comparison(*many), named=None).splitlines()

    assert len(lines) == NAMED + 3
    assert lines[-1] == f"- `p.f{NAMED + 1}`: ya no existe"


def test_what_was_broken_at_the_start_or_by_an_earlier_green_isnt_warned_again() -> None:
    """H4 of 0.20.0: with `api.base` the base is fixed, so only what this run breaks anew."""
    progress = RunProgress()
    progress.observe(_event("lictor.run.started.v1", {"api_start": api_start_record("b", ["p.f"])}))
    progress.observe(_verify(ok=True))

    assert not any(line.startswith("⚠") for line in progress.observe(_green()))


def test_a_break_is_news_once_in_a_run() -> None:
    progress = RunProgress()
    progress.observe(_verify(ok=True))
    assert progress.observe(_green())[-1].startswith("⚠")

    progress.observe(_verify(ok=True))

    assert not any(line.startswith("⚠") for line in progress.observe(_green()))


def test_a_start_without_api_base_knows_nothing_broken() -> None:
    assert api_start_names(None) == frozenset()
    assert api_start_names({"broken": "p.f"}) == frozenset()
    assert api_start_names(api_start_record("b", ["p.g", "p.f"])) == {"p.f", "p.g"}
