"""The complexity control (ADR-0013): its report, its verdict, and what a refactor can't raise."""

import pytest

from lictor.domain.complexity import (
    NAMED,
    OverLimit,
    describe_over,
    implementer_complexity_text,
    over_limit,
    over_limit_reason,
    refactor_complexity_text,
)
from lictor.domain.controls import (
    AGENT_FILES,
    CommandControl,
    ReaderControl,
    agent_command,
    agent_tools_with_files,
    code_controls,
    refactor_measures,
)
from lictor.domain.errors import ConfigError
from lictor.domain.refactor import (
    Measure,
    measures_from_record,
    measures_record,
    oracle_measures,
    refactor_regressions,
)
from lictor.domain.reports import (
    ComplexityReport,
    FunctionComplexity,
    InvalidReport,
    MissingReport,
    complexity_report,
)
from lictor.domain.results import (
    CommandEntry,
    Executed,
    Judged,
    Ran,
    RecordedComplexity,
    RecordedExecuted,
    RecordedJudged,
    control_from_record,
    control_record,
)
from lictor.domain.verdicts import Execution, Failed, Passed, VerdictRule, complexity_verdict

_RUN = "uv run python -I -B {lictor_files}/m.py src"
_CONTROL = CommandControl(
    id="complexity",
    level="L1",
    run="medir {out}",
    report="complexity",
    mode="measure",
    limit=10,
    agent_run=_RUN,
)
_RAN = Ran(("medir",), Execution(0, 60), "log")
_OK = Execution(0, 60)


def _f(value: int, name: str = "f", file: str = "src/p/m.py", line: int = 1) -> FunctionComplexity:
    return FunctionComplexity(file, name, line, value)


def _report(*values: int) -> ComplexityReport:
    return ComplexityReport(tuple(_f(v, f"f{i}", line=i) for i, v in enumerate(values, 1)))


def _entry(report: ComplexityReport) -> CommandEntry:
    verdict = complexity_verdict(report, _OK, 10)
    return CommandEntry(_CONTROL, Executed(_RAN, Judged(report, verdict)))


def _measured(*values: int) -> dict[str, Measure]:
    return oracle_measures([_entry(_report(*values))])


# What is above the limit.


def test_over_limit_keeps_the_functions_above_it_most_complex_first() -> None:
    report = ComplexityReport(
        (_f(11, "b", "b.py", 3), _f(10, "a"), _f(14, "c", "c.py"), _f(11, "a", "a.py", 9))
    )

    over = over_limit(report, 10)

    assert [f.function for f in over.functions] == ["c", "a", "b"]
    assert (over.excess, over.peak) == (6, 4)


def test_nothing_above_the_limit_leaves_nothing_to_lower() -> None:
    over = over_limit(_report(10, 3), 10)

    assert (over.functions, over.excess, over.peak) == ((), 0, 0)


@pytest.mark.parametrize(
    ("before", "after", "regressions"),
    [
        ((14,), (12,), []),  # improves without reaching the limit
        ((14,), (11, 11), []),  # splits into two still above it
        ((11, 11), (12, 10), ["peak"]),  # concentrates
        ((9,), (11,), ["excess", "peak"]),  # a function goes above
        ((12, 12, 12), (13, 10, 10), ["peak"]),  # a known limit of peak: the total improves
        ((4, 4, 4), (10,), []),  # below the limit nothing counts
        ((12,), (), []),  # removing the function
    ],
)
def test_the_refactor_may_raise_neither_the_excess_nor_the_peak(
    before: tuple[int, ...], after: tuple[int, ...], regressions: list[str]
) -> None:
    """The table of ADR-0013, point 5."""
    found = refactor_regressions(_measured(*before), _measured(*after))

    kinds = [
        kind
        for kind, words in (("excess", "sumada"), ("peak", "peor función"))
        if any(words in line for line in found)
    ]
    assert kinds == regressions


def test_a_refactor_whose_complexity_wasnt_measured_regresses() -> None:
    assert refactor_regressions(_measured(12), {}) == [
        "complexity: sin medida, y en el verde la tenía"
    ]


def test_the_measure_keeps_the_functions_for_the_prompt() -> None:
    [measure] = _measured(12, 3).values()

    assert measure.misses == (("excess", 2), ("peak", 2))
    assert measure.findings == OverLimit(10, (_f(12, "f1", line=1),))


def test_the_measure_goes_through_the_checkpoint_and_back() -> None:
    measures = _measured(14, 12, 3)

    assert measures_from_record(measures_record(measures)) == measures


@pytest.mark.parametrize(
    "over",
    [
        "no",
        {"limit": 10, "functions": []},
        {"kind": "otra", "limit": 10, "functions": []},
        {"kind": "complexity", "limit": "10", "functions": []},
        {"kind": "complexity", "limit": 10, "functions": {}},
        {"kind": "complexity", "limit": 10, "functions": [1]},
        {
            "kind": "complexity",
            "limit": 10,
            "functions": [{"file": 1, "function": "f", "line": 1, "complexity": 1}],
        },
        {
            "kind": "complexity",
            "limit": 10,
            "functions": [{"file": "a", "function": "f", "line": "1", "complexity": 1}],
        },
    ],
)
def test_a_checkpoint_with_a_bad_list_keeps_no_list(over: object) -> None:
    record = {"complexity": {"passed": None, "misses": {"excess": 0, "peak": 0}, "findings": over}}

    assert measures_from_record(record)["complexity"].findings is None


def test_a_report_of_another_control_or_without_limit_is_no_complexity_measure() -> None:
    lint = CommandControl(id="lint", level="L1", run="l", report="exit-code")
    entry = CommandEntry(lint, Executed(_RAN, Judged(_report(12), Passed())))

    assert oracle_measures([entry]) == {}


# The report and its verdict.


def test_a_report_is_every_function_with_its_value() -> None:
    data = {"functions": [{"file": "src/p/m.py", "function": "A.f", "line": 3, "complexity": 4}]}

    assert complexity_report(data) == ComplexityReport((_f(4, "A.f", line=3),))


def test_a_path_is_kept_as_posix() -> None:
    data = {"functions": [{"file": "src//p/./m.py", "function": "f", "line": 1, "complexity": 1}]}

    report = complexity_report(data)

    assert isinstance(report, ComplexityReport)
    assert report.functions[0].file == "src/p/m.py"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        ({"functions": [{"file": "", "function": "f", "line": 1, "complexity": 1}]}, "'file'"),
        ({"functions": [{"file": "a", "function": "", "line": 1, "complexity": 1}]}, "'file'"),
        ({"functions": [{"file": "a", "function": "f", "complexity": 1}]}, "línea"),
        ({"functions": [{"file": "a", "function": "f", "line": True, "complexity": 1}]}, "línea"),
        ({"functions": [{"file": "a", "function": "f", "line": 1, "complexity": -1}]}, "complej"),
        ({"functions": [{"file": "a", "function": "f", "line": 1}]}, "complejidad"),
    ],
)
def test_a_function_that_cant_be_real_invalidates_the_report(data: object, reason: str) -> None:
    report = complexity_report(data)

    assert isinstance(report, InvalidReport)
    assert reason in report.reason


def test_the_verdict_passes_with_nothing_above_the_limit() -> None:
    assert complexity_verdict(_report(10, 1), _OK, 10) == Passed()
    assert complexity_verdict(ComplexityReport(), _OK, 10) == Passed()


def test_the_verdict_names_what_is_above_the_limit() -> None:
    verdict = complexity_verdict(_report(12, 11), _OK, 10)

    assert verdict == Failed(
        "2 funciones superan la complejidad de 10:\n"
        "- src/p/m.py:1 `f1`: 12\n"
        "- src/p/m.py:2 `f2`: 11"
    )


@pytest.mark.parametrize(
    ("report", "execution", "reason"),
    [
        (_report(1), Execution(0, 60, timed_out=True), "Se cortó a los 60 s"),
        (MissingReport(), Execution(2, 60), "Sin informe de complejidad (código de salida 2)"),
        (InvalidReport("roto"), _OK, "El informe de complejidad no es válido: roto"),
        (_report(1), Execution(1, 60), "El comando de complejidad falló con el código 1"),
    ],
)
def test_a_command_that_didnt_measure_well_fails(
    report: ComplexityReport | MissingReport | InvalidReport, execution: Execution, reason: str
) -> None:
    verdict = complexity_verdict(report, execution, 10)

    assert isinstance(verdict, Failed)
    assert verdict.reason.startswith(reason)


def test_a_long_list_names_the_first_ones_and_counts_the_rest() -> None:
    reason = over_limit_reason(over_limit(_report(*([11] * (NAMED + 3))), 10))

    assert reason.splitlines()[0] == f"{NAMED + 3} funciones superan la complejidad de 10:"
    assert reason.splitlines()[-1] == "- y 3 más"
    assert len(reason.splitlines()) == NAMED + 2


def test_one_function_is_said_in_singular() -> None:
    assert over_limit_reason(over_limit(_report(12), 10)).startswith(
        "Una función supera la complejidad de 10:"
    )


@pytest.mark.parametrize(
    ("count", "peak", "text"),
    [
        (0, 0, "ninguna función supera 10"),
        (1, 2, "1 función supera 10 (la peor, 12)"),
        (41, 16, "41 funciones superan 10 (la peor, 26)"),
    ],
)
def test_describe_over_says_it_in_one_phrase(count: int, peak: int, text: str) -> None:
    assert describe_over(count, 10, peak) == text


# The journal.


def test_the_journal_keeps_the_limit_and_what_is_above_it() -> None:
    record = control_record(_entry(_report(14, 12, 3)))

    assert record["complexity"] == {"limit": 10, "over": 2, "excess": 6, "peak": 4}
    recorded = control_from_record(record)
    assert isinstance(recorded, RecordedExecuted)
    assert isinstance(recorded.judgement, RecordedJudged)
    assert recorded.judgement.complexity == RecordedComplexity(10, 2, 6, 4)


def test_a_journal_record_with_a_broken_complexity_doesnt_follow_the_schema() -> None:
    record = {**control_record(_entry(_report(12))), "complexity": {"limit": 10}}

    assert control_from_record(record) is None


def test_another_control_keeps_no_complexity() -> None:
    lint = CommandControl(id="lint", level="L1", run="l", report="exit-code")
    record = control_record(CommandEntry(lint, Executed(_RAN, Judged(None, Passed()))))

    assert "complexity" not in record


# The control and what the agent is given.


def test_a_complexity_control_needs_its_limit_and_only_it_takes_one() -> None:
    with pytest.raises(ConfigError, match="necesita limit"):
        CommandControl(id="c", level="L1", run="m {out}", report="complexity")
    with pytest.raises(ConfigError, match="solo vale con report='complexity'"):
        CommandControl(id="c", level="L1", run="m {out}", report="junit", limit=10)


def test_its_verdict_rule_carries_the_limit() -> None:
    assert _CONTROL.verdict_rule == VerdictRule("complexity", 600, (), 10)


def test_the_profiles_files_are_written_into_a_rule_or_dropped() -> None:
    tools = ["Bash(uv run pytest:*)", f"Bash(uv run python {AGENT_FILES}/m.py:*)"]

    assert agent_tools_with_files(tools, None) == ("Bash(uv run pytest:*)",)
    assert agent_tools_with_files(tools, "/f") == (
        "Bash(uv run pytest:*)",
        "Bash(uv run python /f/m.py:*)",
    )
    assert agent_tools_with_files(tools, "/a b")[1] == "Bash(uv run python '/a b'/m.py:*)"


def test_the_agent_command_needs_the_files_it_names() -> None:
    plain = CommandControl(id="u", level="L2", run="u {out}", report="junit", agent_run="u")

    assert agent_command(_CONTROL, None) is None
    assert agent_command(_CONTROL, "/f") == "uv run python -I -B /f/m.py src"
    assert agent_command(plain, None) == "u"
    assert (
        agent_command(CommandControl(id="l", level="L1", run="l", report="exit-code"), "/f") is None
    )


def test_complexity_has_its_own_lines_in_the_refactor_prompt() -> None:
    unit = CommandControl(id="unit", level="L2", run="u {out}", report="junit", agent_run="u")
    reader = ReaderControl(id="cov", level="L2", reads="unit", adds="--cov {out}")

    assert code_controls([unit, reader, _CONTROL]) == (_CONTROL,)
    assert refactor_measures([unit, _CONTROL], ()) == (("unit", "u"),)


def test_the_refactor_hears_which_functions_go_above_and_how_to_measure() -> None:
    text = refactor_complexity_text(over_limit(_report(12, 3), 10), "medir src")

    assert text.splitlines() == [
        "Complejidad, con un límite de 10 por función, medida en la verificación del verde:",
        "- src/p/m.py:1 `f1`: 12",
        "Baja al menos una, empezando por las de los ficheros que cambió este escenario; no hace "
        "falta arreglarlas todas ahora.",
        "La verificación descarta el refactor si deja por encima del límite más que el verde, en "
        "total o en la peor función. Por debajo del límite la complejidad no cuenta, y mejorar una "
        "función sin llegar al límite no se descarta.",
        "Mídela tú con `medir src`, sola y no detrás de otras órdenes con `&&`: una vez antes de "
        "editar y otra al terminar.",
    ]


def test_an_empty_list_is_said_and_no_measure_says_nothing() -> None:
    text = refactor_complexity_text(OverLimit(10), None)

    assert "- Ninguna función supera el límite." in text.splitlines()
    assert "Baja al menos una" not in text
    assert "Mídela" not in text
    assert refactor_complexity_text(None, "medir") == ""


def test_the_implementer_hears_the_limit_only_of_a_control_that_blocks() -> None:
    assert implementer_complexity_text(10, None) == (
        "- La verificación exige que ninguna función supere una complejidad ciclomática de 10."
    )
    assert implementer_complexity_text(8, "m").endswith("de 8. Mídela con `m`.")
