"""The duplication control (ADR-0014): its report, its verdict, and what a refactor can't raise."""

import pytest

from lictor.domain.controls import CommandControl, ReaderControl, code_controls, refactor_measures
from lictor.domain.duplication import (
    NAMED,
    Duplication,
    clone_line,
    clones_reason,
    describe_duplication,
    duplication_of,
    first_where_changed,
    implementer_duplication_text,
    refactor_duplication_text,
)
from lictor.domain.refactor import (
    Measure,
    measures_from_record,
    measures_record,
    oracle_measures,
    refactor_regressions,
)
from lictor.domain.reports import (
    Clone,
    CloneOccurrence,
    CodeFile,
    DuplicationReport,
    InvalidReport,
    MissingReport,
    duplication_report,
)
from lictor.domain.results import (
    CommandEntry,
    Executed,
    Judged,
    Ran,
    RecordedDuplication,
    RecordedExecuted,
    RecordedJudged,
    control_from_record,
    control_record,
)
from lictor.domain.verdicts import Execution, Failed, Passed, duplication_verdict

_CONTROL = CommandControl(
    id="duplication",
    level="L1",
    run="medir {out}",
    report="duplication",
    mode="measure",
    agent_run="uv run python -I -B {lictor_files}/d.py src",
)
_RAN = Ran(("medir",), Execution(0, 60), "log")
_OK = Execution(0, 60)


def _place(file: str, start: int, end: int, line: int = 1) -> CloneOccurrence:
    return CloneOccurrence(file, line, line + 5, start, end)


def _clone(*places: tuple[str, int, int]) -> Clone:
    return Clone(tuple(_place(f, s, e) for f, s, e in places))


def _report(*clones: Clone, tokens: int = 500) -> DuplicationReport:
    """A report whose files say in a clone exactly what its clones cover."""
    spans: dict[str, list[tuple[int, int]]] = {}
    for clone in clones:
        for o in clone.occurrences:
            spans.setdefault(o.file, []).append((o.start, o.end))
    files = tuple(
        CodeFile(f, tokens, tuple(sorted(spans.get(f, [])))) for f in sorted({"a.py", *spans})
    )
    return DuplicationReport(50, files, clones)


def _entry(report: DuplicationReport) -> CommandEntry:
    return CommandEntry(_CONTROL, Executed(_RAN, Judged(report, duplication_verdict(report, _OK))))


def _measured(report: DuplicationReport) -> dict[str, Measure]:
    return oracle_measures([_entry(report)])


_BIG = _clone(("a.py", 0, 80), ("b.py", 10, 90))
_SMALL = _clone(("a.py", 100, 155), ("c.py", 0, 55))


# The report.


def test_the_tokens_in_clones_count_every_copy() -> None:
    report = _report(_BIG, _SMALL)

    assert report.in_clones == 80 + 80 + 55 + 55
    assert report.code_tokens == 500 * 3
    assert _BIG.tokens == 80


def _raw(**changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "min_tokens": 50,
        "files": [
            {"file": "a.py", "tokens": 100, "covered": [[0, 60]]},
            {"file": "b.py", "tokens": 100, "covered": [[10, 70]]},
        ],
        "families": [
            {
                "occurrences": [
                    {"file": "a.py", "first_line": 1, "last_line": 8, "start": 0, "end": 60},
                    {"file": "b.py", "first_line": 3, "last_line": 9, "start": 10, "end": 70},
                ]
            }
        ],
    }
    data.update(changes)
    return data


def _occurrence(**changes: object) -> dict[str, object]:
    return {"file": "a.py", "first_line": 1, "last_line": 8, "start": 0, "end": 60, **changes}


def test_a_report_is_its_files_and_its_clones() -> None:
    report = duplication_report(_raw())

    assert report == DuplicationReport(
        50,
        (CodeFile("a.py", 100, ((0, 60),)), CodeFile("b.py", 100, ((10, 70),))),
        (Clone((CloneOccurrence("a.py", 1, 8, 0, 60), CloneOccurrence("b.py", 3, 9, 10, 70))),),
    )
    assert report.in_clones == 120


def test_a_report_with_nothing_repeated_is_a_report() -> None:
    data = _raw(files=[{"file": "a.py", "tokens": 9, "covered": []}], families=[])

    assert duplication_report(data) == DuplicationReport(50, (CodeFile("a.py", 9),))


def _families(*occurrences: dict[str, object]) -> list[dict[str, object]]:
    return [{"occurrences": list(occurrences)}]


_B = {"file": "b.py", "first_line": 3, "last_line": 9, "start": 10, "end": 70}


_C1 = "el clon 1"


def _files(*files: tuple[str, int, list[list[int]]]) -> list[dict[str, object]]:
    return [{"file": f, "tokens": t, "covered": c} for f, t, c in files]


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ([], "no es un objeto JSON"),
        (_raw(min_tokens=0), "'min_tokens' no es válido: 0"),
        (_raw(min_tokens=True), "'min_tokens' no es válido: True"),
        (_raw(files={}), "no tiene 'files' y 'families'"),
        (_raw(families={}), "no tiene 'files' y 'families'"),
        (_raw(files=["a"]), "el fichero 1 no es un objeto"),
        (_raw(files=_files(("/a.py", 1, []))), "el fichero 1 está fuera del árbol medido: '/a.py'"),
        (_raw(files=_files(("../a", 1, []))), "el fichero 1 está fuera del árbol medido: '../a'"),
        (_raw(files=_files(("", 1, []))), "el fichero 1 está fuera del árbol medido: ''"),
        (_raw(files=[{"file": 3, "tokens": 1}]), "el fichero 1 está fuera del árbol medido: 3"),
        (_raw(files=_files(("a.py", -1, []))), "los tokens de a.py no son un número: -1"),
        (_raw(files=_files(("a.py", 1, [[0]]))), "los tramos en clones de a.py no son válidos"),
        (_raw(files=_files(("a.py", 1, "x"))), "los tramos en clones de a.py no son válidos"),
        (_raw(files=_files(("a.py", 1, ["x"]))), "los tramos en clones de a.py no son válidos"),
        (_raw(files=_files(("a.py", 1, [[0, -1]]))), "los tramos en clones de a.py no son válidos"),
        (_raw(files=_files(("a.py", 1, [[-1, 0]]))), "los tramos en clones de a.py no son válidos"),
        (_raw(files=_files(("a.py", 1, []), ("a.py", 1, []))), "el fichero a.py está dos veces"),
        (
            _raw(families=[{"occurrences": [_occurrence()]}]),
            f"{_C1} no tiene al menos dos apariciones",
        ),
        (_raw(families=["x"]), f"{_C1} no tiene al menos dos apariciones"),
        (_raw(families=_families(_occurrence(), "x")), f"{_C1}: una aparición no es un objeto"),
        (
            _raw(families=_families(_occurrence(file="z.py"), _B)),
            f"{_C1}: una aparición está en un fichero que no lista: 'z.py'",
        ),
        (
            _raw(families=_families(_occurrence(first_line=0), _B)),
            f"{_C1}: las líneas de una aparición en a.py no son válidas",
        ),
        (
            _raw(families=_families(_occurrence(first_line="1"), _B)),
            f"{_C1}: las líneas de una aparición en a.py no son válidas",
        ),
        (
            _raw(families=_families(_occurrence(last_line="8"), _B)),
            f"{_C1}: las líneas de una aparición en a.py no son válidas",
        ),
        (
            _raw(families=_families(_occurrence(last_line=0), _B)),
            f"{_C1}: las líneas de una aparición en a.py no son válidas",
        ),
        (
            _raw(families=_families(_occurrence(start="0"), _B)),
            f"{_C1}: los tokens de una aparición en a.py no son válidos",
        ),
        (
            _raw(families=_families(_occurrence(end="60"), _B)),
            f"{_C1}: los tokens de una aparición en a.py no son válidos",
        ),
        (
            _raw(families=_families(_occurrence(end=0), _B)),
            f"{_C1}: los tokens de una aparición en a.py no son válidos",
        ),
        (
            _raw(families=_families(_occurrence(end=101), _B)),
            f"{_C1}: los tokens de una aparición en a.py no son válidos",
        ),
        (
            _raw(families=_families(_occurrence(start=10, end=59), _B)),
            f"{_C1}: una aparición en a.py tiene 49 tokens, menos que el mínimo de 50",
        ),
        (
            _raw(families=_families(_occurrence(), _occurrence(start=30, end=90))),
            f"{_C1} tiene dos apariciones que se solapan en a.py",
        ),
        (
            _raw(
                files=_files(("a.py", 200, []), ("b.py", 100, [])),
                families=_families(_occurrence(), _occurrence(start=59, end=119)),
            ),
            f"{_C1} tiene dos apariciones que se solapan en a.py",
        ),
        (
            _raw(families=[*_families(_occurrence(), _B), *_families(_occurrence(), "x")]),
            "el clon 2: una aparición no es un objeto",
        ),
        (
            _raw(files=_files(("a.py", 100, [[0, 70]]), ("b.py", 100, [[10, 70]]))),
            "los tramos en clones de a.py no son los de sus clones",
        ),
        (
            _raw(files=_files(("a.py", 100, [[0, 60]]), ("b.py", 100, []))),
            "los tramos en clones de b.py no son los de sus clones",
        ),
    ],
)
def test_a_report_that_cant_be_real_is_invalid(data: object, reason: str) -> None:
    assert duplication_report(data) == InvalidReport(reason)


def test_what_is_in_a_clone_is_where_the_clones_are_merged() -> None:
    """Places that touch or overlap make one run; the report must say exactly that."""
    a = _occurrence(start=0, end=60)
    data = _raw(
        files=_files(("a.py", 200, [[0, 120], [130, 190]]), ("b.py", 200, [[0, 60], [60, 190]])),
        families=[
            *_families(a, {**_B, "start": 0, "end": 60}),
            *_families({**a, "start": 60, "end": 120}, {**_B, "start": 60, "end": 120}),
            *_families({**a, "start": 130, "end": 190}, {**_B, "start": 80, "end": 190}),
        ],
    )
    data["files"] = _files(("a.py", 200, [[0, 120], [130, 190]]), ("b.py", 200, [[0, 190]]))

    report = duplication_report(data)

    assert isinstance(report, DuplicationReport)
    assert report.in_clones == 120 + 60 + 190
    unmerged = {
        **data,
        "files": _files(("a.py", 200, [[0, 60], [60, 120], [130, 190]]), ("b.py", 200, [[0, 190]])),
    }
    assert duplication_report(unmerged) == InvalidReport(
        "los tramos en clones de a.py no son los de sus clones"
    )


@pytest.mark.parametrize("minimum", [1, 60])
def test_places_at_the_edges_are_valid(minimum: int) -> None:
    """A place may be one line, as long as the minimum, end at the last token of its file, and
    start where another ends, in either order."""
    data = _raw(
        min_tokens=minimum,
        files=_files(("a.py", 120, [[0, 120]]), ("b.py", 100, [[10, 70]])),
        families=_families(
            _occurrence(start=60, end=120, first_line=4, last_line=4), _occurrence(), _B
        ),
    )

    report = duplication_report(data)

    assert isinstance(report, DuplicationReport)
    [clone] = report.clones
    assert clone.occurrences[0] == CloneOccurrence("a.py", 4, 4, 60, 120)
    assert [o.tokens for o in clone.occurrences] == [60, 60, 60]


# The verdict.


def test_the_verdict_passes_with_no_clone() -> None:
    assert duplication_verdict(_report(), _OK) == Passed()


def test_the_verdict_names_the_clones_largest_first() -> None:
    verdict = duplication_verdict(_report(_SMALL, _BIG), _OK)

    assert verdict == Failed(
        "Hay 2 clones de código repetido:\n"
        "- a.py:1-6 y b.py:1-6: 80 tokens\n"
        "- a.py:1-6 y c.py:1-6: 55 tokens"
    )


def test_one_clone_is_said_in_singular() -> None:
    verdict = duplication_verdict(_report(_BIG), _OK)

    assert isinstance(verdict, Failed)
    assert verdict.reason.startswith("Hay un clon de código repetido:")


@pytest.mark.parametrize(
    ("report", "execution", "reason"),
    [
        (MissingReport(), Execution(2, 60), "Sin informe de duplicación (código de salida 2)"),
        (InvalidReport("roto"), _OK, "El informe de duplicación no es válido: roto"),
        (_report(), Execution(1, 60), "El comando de duplicación falló con el código 1"),
        (_report(), Execution(0, 60, timed_out=True), "Se cortó a los 60 s, su tiempo límite"),
    ],
)
def test_a_command_that_didnt_measure_well_fails(
    report: DuplicationReport | MissingReport | InvalidReport, execution: Execution, reason: str
) -> None:
    assert duplication_verdict(report, execution) == Failed(reason)


def test_a_long_list_names_the_first_ones_and_counts_the_rest() -> None:
    clones = [_clone((f"f{i}.py", 0, 60 + i), (f"g{i}.py", 0, 60 + i)) for i in range(NAMED + 3)]

    lines = clones_reason(duplication_of(_report(*clones))).splitlines()

    assert lines[0] == f"Hay {NAMED + 3} clones de código repetido:"
    assert len(lines) == 1 + NAMED + 1
    assert lines[-1] == "- y 3 más"


def test_a_clone_with_three_places_names_them_all() -> None:
    clone = _clone(("a.py", 0, 60), ("b.py", 0, 60), ("c.py", 0, 60))

    assert clone_line(clone) == "a.py:1-6, b.py:1-6 y c.py:1-6: 60 tokens"


@pytest.mark.parametrize(
    ("count", "in_clones", "text"),
    [
        (0, 0, "ningún clon"),
        (1, 160, "1 clon, 160 de 900 tokens en clones"),
        (3, 400, "3 clones, 400 de 900 tokens en clones"),
    ],
)
def test_describe_duplication_says_it_in_one_phrase(count: int, in_clones: int, text: str) -> None:
    assert describe_duplication(count, in_clones, 900) == text


# What a refactor may not raise (ADR-0014, point 5).


@pytest.mark.parametrize(
    ("before", "after", "regresses"),
    [
        ((_BIG, _SMALL), (_SMALL,), False),  # removes a clone
        ((_BIG,), (), False),  # removes every clone
        ((_BIG,), (_SMALL,), False),  # a smaller one in place of a larger one
        ((), (_SMALL,), True),  # a new clone
        ((_SMALL,), (_BIG,), True),  # a larger one
        ((_BIG,), (_BIG,), False),  # the same
    ],
)
def test_the_refactor_may_not_leave_more_tokens_in_clones(
    before: tuple[Clone, ...], after: tuple[Clone, ...], regresses: bool
) -> None:
    found = refactor_regressions(_measured(_report(*before)), _measured(_report(*after)))

    assert bool(found) is regresses
    if regresses:
        assert found[0].startswith("duplication: tokens en clones: ")


def test_a_refactor_whose_duplication_wasnt_measured_regresses() -> None:
    assert refactor_regressions(_measured(_report(_BIG)), {}) == [
        "duplication: sin medida, y en el verde la tenía"
    ]


def test_the_measure_keeps_the_clones_for_the_prompt() -> None:
    [measure] = _measured(_report(_SMALL, _BIG)).values()

    assert measure.misses == (("duplicated", 270),)
    assert measure.findings == Duplication(50, 270, (_BIG, _SMALL), 2)


def test_the_measure_goes_through_the_checkpoint_and_back() -> None:
    measures = _measured(_report(_BIG, _SMALL))

    assert measures_from_record(measures_record(measures)) == measures


def test_the_checkpoint_keeps_only_the_clones_a_message_names() -> None:
    clones = [_clone((f"f{i}.py", 0, 60), (f"g{i}.py", 0, 60)) for i in range(NAMED + 5)]
    measures = _measured(_report(*clones))

    [kept] = measures_from_record(measures_record(measures)).values()

    assert isinstance(kept.findings, Duplication)
    assert (len(kept.findings.clones), kept.findings.count) == (NAMED, NAMED + 5)
    assert kept.misses == (("duplicated", 120 * (NAMED + 5)),)


@pytest.mark.parametrize(
    "findings",
    [
        {"kind": "duplication", "min_tokens": 50, "in_clones": 1, "count": 1},
        {"kind": "duplication", "min_tokens": 50, "in_clones": 1, "count": 1, "clones": [[]]},
        {"kind": "duplication", "min_tokens": 50, "in_clones": 1, "count": 1, "clones": [["x"]]},
        {
            "kind": "duplication",
            "min_tokens": 50,
            "in_clones": 1,
            "count": 1,
            "clones": [[["a.py", 1, 2, 0, 9], ["b.py", 1, 2, 0]]],
        },
        {
            "kind": "duplication",
            "min_tokens": 50,
            "in_clones": 1,
            "count": 1,
            "clones": [[["a.py", 1, 2, 0, 9], "b.py"]],
        },
        {
            "kind": "duplication",
            "min_tokens": 50,
            "in_clones": 1,
            "count": 1,
            "clones": [[[1, 1, 2, 0, 9], ["b.py", 1, 2, 0, 9]]],
        },
        {
            "kind": "duplication",
            "min_tokens": 50,
            "in_clones": 1,
            "count": 1,
            "clones": [[[1, 1, 2, 0, 9]]],
        },
        {
            "kind": "duplication",
            "min_tokens": 50,
            "in_clones": 1,
            "count": 1,
            "clones": [[["a.py", "1", 2, 0, 9]]],
        },
        {"kind": "duplication", "min_tokens": "50", "in_clones": 1, "count": 1, "clones": []},
    ],
)
def test_a_checkpoint_with_bad_clones_keeps_none(findings: object) -> None:
    record = {"duplication": {"passed": None, "misses": {"duplicated": 1}, "findings": findings}}

    assert measures_from_record(record)["duplication"].findings is None


# The journal.


def test_the_journal_keeps_how_much_is_repeated() -> None:
    record = control_record(_entry(_report(_BIG)))

    assert record["duplication"] == {"clones": 1, "in_clones": 160, "code_tokens": 1000}
    recorded = control_from_record(record)
    assert isinstance(recorded, RecordedExecuted)
    assert isinstance(recorded.judgement, RecordedJudged)
    assert recorded.judgement.duplication == RecordedDuplication(1, 160, 1000)


def test_a_journal_record_with_a_broken_duplication_doesnt_follow_the_schema() -> None:
    record = {**control_record(_entry(_report(_BIG))), "duplication": {"clones": 1}}

    assert control_from_record(record) is None


def test_another_control_keeps_no_duplication() -> None:
    lint = CommandControl(id="lint", level="L1", run="l", report="exit-code")
    record = control_record(CommandEntry(lint, Executed(_RAN, Judged(None, Passed()))))

    assert "duplication" not in record


# What the agent is given.


def test_duplication_has_its_own_lines_in_the_refactor_prompt() -> None:
    unit = CommandControl(id="unit", level="L2", run="u {out}", report="junit", agent_run="u")
    reader = ReaderControl(id="cov", level="L2", reads="unit", adds="--cov {out}")

    assert code_controls([unit, reader, _CONTROL]) == (_CONTROL,)
    assert refactor_measures([unit, _CONTROL], ()) == (("unit", "u"),)


def test_the_refactor_hears_the_clones_and_how_to_measure() -> None:
    text = refactor_duplication_text(duplication_of(_report(_BIG)), "medir src")

    assert text.splitlines() == [
        "Código repetido, medido en la verificación del verde (un clon es un trozo de 50 tokens "
        "o más que se repite en dos sitios, con los nombres y los literales cambiados de forma "
        "coherente dentro de cada función):",
        "- a.py:1-6 y b.py:1-6: 80 tokens",
        "Quita al menos uno, empezando por los de los ficheros que cambió este escenario; no hace "
        "falta quitarlos todos ahora.",
        "La verificación descarta el refactor si deja más tokens en clones que el verde (160). El "
        "formato no cuenta, ni cambiar el nombre de una variable dentro de una función.",
        "Mídelo tú con `medir src`, sola y no detrás de otras órdenes con `&&`: una vez antes de "
        "editar y otra al terminar. Su segunda línea dice los tokens en clones.",
    ]


def test_the_clones_of_the_files_the_scenario_changed_come_first() -> None:
    assert first_where_changed([_BIG, _SMALL], {"c.py"}) == [_SMALL, _BIG]
    assert first_where_changed([_BIG, _SMALL], set()) == [_BIG, _SMALL]

    text = refactor_duplication_text(duplication_of(_report(_BIG, _SMALL)), None, ["c.py"])

    assert text.splitlines()[1] == "- a.py:1-6 y c.py:1-6: 55 tokens"


def test_an_empty_list_is_said_and_no_measure_says_nothing() -> None:
    text = refactor_duplication_text(duplication_of(_report()), None)

    assert "- Ningún clon." in text.splitlines()
    assert "Quita al menos uno" not in text
    assert "Mídelo" not in text
    assert refactor_duplication_text(None, "medir") == ""


def test_the_implementer_hears_of_it_only_from_a_control_that_blocks() -> None:
    assert implementer_duplication_text(None) == (
        "- La verificación exige que no haya código repetido: ningún trozo que se repita en dos "
        "sitios, aunque cambien los nombres."
    )
    assert implementer_duplication_text("m").endswith(
        "Mídelo con `m`, que dice en su primera línea qué cuenta como clon."
    )


def test_clones_of_one_size_go_in_the_order_of_their_first_place() -> None:
    later = _clone(("z.py", 0, 60), ("a.py", 200, 260))
    earlier = _clone(("b.py", 5, 65), ("c.py", 0, 60))

    assert duplication_of(_report(later, earlier)).clones == (earlier, later)


def test_one_clone_more_than_a_message_names_is_counted() -> None:
    clones = [_clone((f"f{i}.py", 0, 60), (f"g{i}.py", 0, 60)) for i in range(NAMED + 1)]

    assert clones_reason(duplication_of(_report(*clones))).splitlines()[-1] == "- y 1 más"


def test_a_measure_from_the_checkpoint_counts_what_it_no_longer_names() -> None:
    clones = tuple(_clone((f"f{i}.py", 0, 60), (f"g{i}.py", 0, 60)) for i in range(NAMED))
    kept = Duplication(50, 120 * (NAMED + 5), clones, NAMED + 5)

    assert clones_reason(kept).splitlines()[-1] == "- y 5 más"
    assert "- y 5 más" in refactor_duplication_text(kept, None).splitlines()


@pytest.mark.parametrize("missing", ["min_tokens", "in_clones", "count", "clones"])
def test_a_checkpoint_missing_a_field_keeps_no_clones(missing: str) -> None:
    findings: dict[str, object] = {
        "kind": "duplication",
        "min_tokens": 50,
        "in_clones": 160,
        "count": 1,
        "clones": [[["a.py", 1, 6, 0, 80], ["b.py", 1, 6, 10, 90]]],
    }
    record = {"duplication": {"passed": None, "misses": {"duplicated": 160}, "findings": findings}}
    assert isinstance(measures_from_record(record)["duplication"].findings, Duplication)

    del findings[missing]

    assert measures_from_record(record)["duplication"].findings is None


@pytest.mark.parametrize("missing", ["clones", "in_clones", "code_tokens"])
def test_a_journal_record_missing_a_count_doesnt_follow_the_schema(missing: str) -> None:
    record = control_record(_entry(_report(_BIG)))
    raw = record["duplication"]
    assert isinstance(raw, dict)
    duplication = {key: value for key, value in raw.items() if key != missing}

    assert control_from_record({**record, "duplication": duplication}) is None
