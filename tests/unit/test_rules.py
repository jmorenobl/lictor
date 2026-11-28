"""Unit tests for pure domain rules."""

from typing import Any, Literal

import pytest

from lictor.domain.errors import AmbiguousMappingError, DriverError, ExitCode
from lictor.domain.model import (
    AgentSignal,
    AgentStatus,
    MutationScore,
    PreconditionProblem,
    ScenarioId,
    TestCaseResult,
)
from lictor.domain.rules import (
    AGENT_FAILURE_LIMIT,
    ANCHOR_FORMAT,
    PERSON_ONLY_STREAK,
    STATE_CHANGED_OUTSIDE_COPY,
    SUSPICIOUS_DRIVER,
    SYNTHETIC_MODEL,
    RedOracle,
    ScenarioClass,
    acceptance_failure,
    ambiguous_with_sealed_driver,
    anchor_from_older_version,
    anchor_problems,
    banned_matches,
    baseline_head_accepted,
    classify_agent,
    classify_scenarios,
    collateral_greens,
    condense_error,
    control_path_status,
    count_agent_failures,
    describe_score,
    describe_suppressions,
    error_for_agent,
    forbidden_paths,
    impostor_problem,
    impostor_source,
    is_agent_unavailable,
    judge_red,
    keeps_attempt,
    map_test_cases_to_scenarios,
    mapping_failure_reason,
    missing_executables,
    missing_plugin,
    model_responded,
    mutation_score,
    path_within,
    paths_only_a_person_can_change,
    person_only_reason,
    person_stop_reason,
    precondition_exit_code,
    present_scenarios,
    red_failure_problem,
    registration_problems,
    registration_subject,
    render_full_error,
    retired_name,
    retired_to_prune,
    routes_rejected,
    run_outcome,
    runs_to_prune,
    same_error_groups,
    score_meets,
    sealed_driver_cases,
    sealed_driver_reason,
    stub_lines,
    suppression_matches,
    suppressions_not_moved,
    suspicious_driver,
    unmapped_cases,
    with_mapping,
)
from lictor.domain.verdicts import Failed, Passed, enforce_min_score


def test_map_missing_case_says_what_the_test_must_be_called() -> None:
    with pytest.raises(DriverError, match="debe contener 'iva_002'"):
        map_test_cases_to_scenarios([TestCaseResult(name="test_otro")], ["IVA-002"])


def test_map_test_cases_success() -> None:
    test_cases = [
        TestCaseResult(name="test_iva_001", status="passed"),
        TestCaseResult(name="test_iva_002_other", status="failure"),
    ]
    scenario_ids = [ScenarioId("IVA-001"), "IVA-002"]

    mapping = map_test_cases_to_scenarios(test_cases, scenario_ids)
    assert len(mapping) == 2
    assert mapping["IVA-001"].name == "test_iva_001"
    assert mapping["IVA-002"].name == "test_iva_002_other"


def test_map_test_cases_classname_matching() -> None:
    test_cases = [
        TestCaseResult(name="test_case", classname="tests.acceptance.test_iva_001"),
    ]
    scenario_ids = ["IVA-001"]

    mapping = map_test_cases_to_scenarios(test_cases, scenario_ids)
    assert mapping["IVA-001"].classname == "tests.acceptance.test_iva_001"


def test_map_test_cases_boundary_prevents_partial_match() -> None:
    # iva_001 should not match test_iva_0010
    test_cases = [
        TestCaseResult(name="test_iva_0010", status="passed"),
    ]
    scenario_ids = ["IVA-001"]

    with pytest.raises(DriverError, match="No se encontró caso de test para el escenario"):
        map_test_cases_to_scenarios(test_cases, scenario_ids)


def test_map_test_cases_missing_scenario_raises_driver_error() -> None:
    test_cases = [
        TestCaseResult(name="test_iva_001", status="passed"),
    ]
    scenario_ids = ["IVA-001", "IVA-002"]

    with pytest.raises(DriverError, match="IVA-002"):
        map_test_cases_to_scenarios(test_cases, scenario_ids)


def test_map_test_cases_duplicate_for_scenario_raises_driver_error() -> None:
    test_cases = [
        TestCaseResult(name="test_iva_001_first", status="passed"),
        TestCaseResult(name="test_iva_001_second", status="passed"),
    ]
    scenario_ids = ["IVA-001"]

    with pytest.raises(AmbiguousMappingError) as raised:
        map_test_cases_to_scenarios(test_cases, scenario_ids)

    assert raised.value.scenario == "IVA-001"
    assert raised.value.cases == ("test_iva_001_first", "test_iva_001_second")
    assert "un subtest o un test parametrizado sale como otro caso" in str(raised.value)


def test_a_go_subtest_is_another_case_of_the_same_scenario_and_the_message_says_so() -> None:
    """G1 of 0.23.2: gotestsum writes `Test_X/fila` beside `Test_X`, and by name both are X."""
    cases = [
        TestCaseResult(name="Test_PLZ_007/despido", classname="acceptance"),
        TestCaseResult(name="Test_PLZ_007", classname="acceptance"),
    ]

    with pytest.raises(AmbiguousMappingError, match="Recorre los casos dentro de un solo test"):
        map_test_cases_to_scenarios(cases, ["PLZ-007"])


def test_map_test_cases_same_test_for_multiple_scenarios_raises_driver_error() -> None:
    test_cases = [
        TestCaseResult(name="test_iva_001_and_iva_002", status="passed"),
    ]
    scenario_ids = ["IVA-001", "IVA-002"]

    with pytest.raises(DriverError):
        map_test_cases_to_scenarios(test_cases, scenario_ids)


def test_condense_error_exit_code_tail_lines() -> None:
    lines = [f"line {i}" for i in range(100)]
    raw_error = "\n".join(lines)

    condensed = condense_error("lint", raw_error, max_chars=4000)
    assert condensed.startswith("Falló lint\n")
    # Should contain line 99 and line 60 (last 40 lines), but not line 50
    assert "line 99" in condensed
    assert "line 60" in condensed
    assert "line 50" not in condensed


def test_condense_error_junit_details_first_15_lines() -> None:
    details_lines = [f"trace line {i}" for i in range(30)]
    details = "\n".join(details_lines)

    test_cases = [
        TestCaseResult(name="test_pass", status="passed"),
        TestCaseResult(
            name="test_fail",
            status="failure",
            message="AssertionError",
            details=details,
        ),
    ]

    condensed = condense_error("unit", raw_error="", test_cases=test_cases, max_chars=4000)
    assert condensed.startswith("Falló unit\n")
    assert "test_fail" in condensed
    assert "AssertionError" in condensed
    assert "trace line 0" in condensed
    assert "trace line 14" in condensed
    assert "trace line 20" not in condensed
    assert "test_pass" not in condensed


def test_condense_error_strict_truncation() -> None:
    raw_error = "x" * 5000
    condensed = condense_error("L1", raw_error, max_chars=200)
    assert len(condensed) <= 200
    assert condensed.startswith("Falló L1")


def test_condense_error_truncation_keeps_both_ends_of_a_long_reason() -> None:
    raw_error = "motivo inicial " + "x" * 5000 + " resumen final"
    condensed = condense_error("L1", raw_error, max_chars=200)
    assert "motivo inicial" in condensed
    assert "resumen final" in condensed
    assert "[…]" in condensed


def test_condense_error_keeps_the_first_line_of_a_long_output() -> None:
    raw_error = "\n".join(["La suite se cayó", *(f"line {i}" for i in range(100))])
    condensed = condense_error("acceptance", raw_error)
    assert "La suite se cayó" in condensed
    assert "line 99" in condensed
    assert "line 40" not in condensed


def test_condense_error_reason_goes_before_the_cases() -> None:
    cases = [TestCaseResult("test_a", status="failure", message="m")]
    condensed = condense_error("x", "el motivo", cases)
    assert condensed.index("el motivo") < condensed.index("test_a")


def test_condense_error_without_reason_or_cases_is_the_header() -> None:
    assert condense_error("x", "") == "Falló x"


def test_condense_error_ignores_cases_that_did_not_fail() -> None:
    cases = [
        TestCaseResult("test_ok", status="passed"),
        TestCaseResult("test_skip", status="skipped"),
        TestCaseResult("test_bad", status="error"),
    ]
    condensed = condense_error("x", "", cases)
    assert "test_bad" in condensed
    assert "test_ok" not in condensed
    assert "test_skip" not in condensed


def test_condense_error_every_failed_case_keeps_its_message_before_any_detail() -> None:
    cases = [
        TestCaseResult(f"test_{i}", status="failure", message=f"motivo-{i}", details="d\n" * 400)
        for i in range(30)
    ]
    condensed = condense_error("x", "", cases, max_chars=2000)
    assert len(condensed) <= 2000
    assert all(f"motivo-{i}\n" in condensed for i in range(30))


def test_condense_error_counts_the_cases_that_do_not_fit() -> None:
    cases = [TestCaseResult(f"test_{i}", status="failure") for i in range(100)]
    condensed = condense_error("x", "", cases, max_chars=300)
    assert len(condensed) <= 300
    assert "test_0" in condensed
    shown = sum(f"test_{i}\n" in condensed for i in range(100))
    assert f"y {100 - shown} casos fallidos más" in condensed


def test_condense_error_long_reason_leaves_room_for_the_cases() -> None:
    cases = [TestCaseResult("test_a", status="failure", message="CLAVE")]
    condensed = condense_error("x", "r" * 10000, cases, max_chars=500)
    assert "CLAVE" in condensed
    assert len(condensed) <= 500


def test_condense_error_detail_budget_is_shared_between_cases() -> None:
    cases = [
        TestCaseResult("test_a", status="failure", details="primero\n" + "a\n" * 14),
        TestCaseResult("test_b", status="failure", details="segundo\n" + "b\n" * 14),
    ]
    condensed = condense_error("x", "", cases, max_chars=4000)
    assert "primero" in condensed
    assert "segundo" in condensed


def test_condense_error_long_message_is_bounded_per_case() -> None:
    cases = [
        TestCaseResult("test_a", status="failure", message="a" * 2000),
        TestCaseResult("test_b", status="failure", message="SEGUNDO"),
    ]
    assert "SEGUNDO" in condense_error("x", "", cases, max_chars=1000)


def test_condense_error_tiny_limit_is_still_strict() -> None:
    assert condense_error("control-con-nombre-largo", "motivo", max_chars=5) == "Falló"


@pytest.mark.parametrize(
    "line",
    ["E   assert 1 == 2", "assert total == 3", "ValueError: mal", "RuntimeException in x"],
)
def test_condense_error_assertion_lines_survive_past_the_head(line: str) -> None:
    details = "\n".join([*(f"marco {i}" for i in range(30)), line, "pie"])
    cases = [TestCaseResult("t", status="failure", details=details)]
    condensed = condense_error("x", "", cases)
    assert line in condensed
    assert "marco 14" in condensed
    assert "marco 15" not in condensed
    assert "pie" not in condensed


def test_condense_error_assertion_lines_are_capped_from_the_end() -> None:
    lines = [f"marco {i}" for i in range(20)] + [f"E   linea-{i}x" for i in range(30)]
    cases = [TestCaseResult("t", status="failure", details="\n".join(lines))]
    condensed = condense_error("x", "", cases)
    assert "linea-29x" in condensed
    assert "linea-20x" in condensed
    assert "linea-19x" not in condensed


def test_condense_error_exact_format() -> None:
    long_details = "\n".join([*(f"m{i}" for i in range(16)), "E   assert 1 == 2"])
    cases = [
        TestCaseResult("test_a", status="failure", message=" corto ", details=long_details),
        TestCaseResult("test_b", status="error"),
    ]
    head = "\n".join(f"    m{i}" for i in range(15))
    assert condense_error("x", "el motivo", cases) == (
        "Falló x\n\nel motivo\n\n"
        f"- test_a\n  Mensaje: corto\n  Detalle:\n{head}\n    ...\n    E   assert 1 == 2"
        "\n\n- test_b"
    )


def test_condense_error_blank_details_add_no_section() -> None:
    cases = [TestCaseResult("test_a", status="failure", details="")]
    assert condense_error("x", "", cases) == "Falló x\n\n- test_a"


def test_condense_error_keeps_a_40_line_reason_verbatim() -> None:
    reason = "\n".join(f"linea {i}" for i in range(40))
    assert condense_error("x", reason) == f"Falló x\n\n{reason}"


def test_condense_error_of_41_lines_adds_nothing_between_reason_and_tail() -> None:
    lines = [f"linea {i}" for i in range(41)]
    assert condense_error("x", "\n".join(lines)) == "\n".join(
        ["Falló x", "", "linea 0", *lines[1:]]
    )


def test_condense_error_fills_the_limit_exactly_with_a_long_reason() -> None:
    condensed = condense_error("L1", "x" * 5000, max_chars=200)
    assert len(condensed) == 200


def test_condense_error_text_at_the_limit_is_not_clipped() -> None:
    reason = "x" * (200 - len("Falló L1\n\n"))
    assert condense_error("L1", reason, max_chars=200) == f"Falló L1\n\n{reason}"


def test_condense_error_clip_keeps_as_much_of_the_end_as_of_the_start() -> None:
    condensed = condense_error("x", "a" * 3000 + "z" * 3000, max_chars=200)
    assert abs(condensed.count("a") - condensed.count("z")) <= 1


def test_condense_error_limit_too_small_for_the_clip_marker() -> None:
    condensed = condense_error("x", "abcdefghij", max_chars=len("Falló x\n\n") + 3)
    assert condensed.endswith("abc")


def test_condense_error_limit_of_exactly_the_clip_marker() -> None:
    condensed = condense_error("x", "abcdefghij", max_chars=len("Falló x\n\n") + 5)
    assert condensed.endswith("abcde")


def test_condense_error_long_reason_does_not_push_the_count_out() -> None:
    cases = [TestCaseResult(f"test_{i}", status="failure") for i in range(100)]
    condensed = condense_error("x", "r" * 5000, cases, max_chars=600)
    assert "casos fallidos más" in condensed


def test_condense_error_reason_gets_half_of_the_budget() -> None:
    cases = [TestCaseResult("test_a", status="failure", details="d\n" * 500)]
    condensed = condense_error("x", "r" * 5000, cases, max_chars=1000)
    assert condensed.count("r") >= 480


def test_condense_error_last_case_gets_its_share_of_detail() -> None:
    cases = [
        TestCaseResult(f"test_{i}", status="failure", details="\n".join(["y" * 80] * 15))
        for i in range(3)
    ]
    condensed = condense_error("x", "", cases, max_chars=1500)
    shares = [len(part) for part in condensed.split("\n\n- ")[1:]]
    assert min(shares) > max(shares) / 2


def test_full_error_exact_format() -> None:
    cases = [TestCaseResult("test_a", status="failure", message=" m ", details="  d1\nd2\n")]
    assert render_full_error("x", " r ", cases, " salida ") == (
        "Falló x\n\nr\n\n- test_a\n  Mensaje: m\n  d1\nd2\n\nSalida:\nsalida\n"
    )


def test_full_error_keeps_everything() -> None:
    cases = [
        TestCaseResult(
            f"test_{i}",
            status="failure",
            message=f"mensaje-{i}",
            details="\n".join(f"detalle-{i}-{n}" for n in range(50)),
        )
        for i in range(40)
    ]
    full = render_full_error("acceptance", "el motivo", cases, output="salida cruda")
    assert full.startswith("Falló acceptance\n")
    assert full.index("el motivo") < full.index("test_0")
    assert all(f"mensaje-{i}" in full and f"detalle-{i}-49" in full for i in range(40))
    assert full.endswith("salida cruda\n")


def test_full_error_skips_cases_that_did_not_fail() -> None:
    full = render_full_error("x", "m", [TestCaseResult("test_ok", status="passed")])
    assert "test_ok" not in full
    assert "Salida:" not in full


def test_acceptance_failure_is_none_when_current_and_greens_pass() -> None:
    mapped = {
        "IVA-001": TestCaseResult("test_iva_001"),
        "IVA-002": TestCaseResult("test_iva_002"),
        "IVA-003": TestCaseResult("test_iva_003", status="failure"),
    }
    assert acceptance_failure("IVA-002", ["IVA-001"], mapped) is None


def test_acceptance_failure_keeps_only_the_current_scenario() -> None:
    current = TestCaseResult("test_iva_002", status="failure", message="m")
    pending = TestCaseResult("test_iva_003", status="failure")
    failure = acceptance_failure("IVA-002", [], {"IVA-002": current, "IVA-003": pending})
    assert failure == ("Escenario IVA-002 no pasó (failure)", (current,))


def test_acceptance_failure_names_a_skipped_current_scenario() -> None:
    current = TestCaseResult("test_iva_001", status="skipped")
    failure = acceptance_failure("IVA-001", [], {"IVA-001": current})
    assert failure is not None
    assert "skipped" in failure[0]
    assert failure[1] == (current,)


def test_acceptance_failure_reports_every_regression() -> None:
    mapped = {
        "IVA-001": TestCaseResult("test_iva_001", status="failure"),
        "IVA-002": TestCaseResult("test_iva_002", status="error"),
        "IVA-003": TestCaseResult("test_iva_003"),
    }
    failure = acceptance_failure("IVA-003", ["IVA-001", "IVA-002"], mapped)
    assert failure is not None
    reason, cases = failure
    assert "Regresión detectada en escenario verde previo: IVA-001" in reason
    assert "Regresión detectada en escenario verde previo: IVA-002" in reason
    assert cases == (mapped["IVA-001"], mapped["IVA-002"])


def test_acceptance_failure_lists_one_reason_per_line() -> None:
    failure = acceptance_failure("IVA-002", ["IVA-001"], {})
    assert failure is not None
    assert failure[0] == (
        "Escenario IVA-002 no pasó (sin resultado)\n"
        "Regresión detectada en escenario verde previo: IVA-001"
    )


def test_acceptance_failure_puts_the_current_scenario_before_regressions() -> None:
    current = TestCaseResult("test_iva_002", status="failure")
    green = TestCaseResult("test_iva_001", status="failure")
    failure = acceptance_failure("IVA-002", ["IVA-001"], {"IVA-001": green, "IVA-002": current})
    assert failure is not None
    assert failure[0].index("IVA-002") < failure[0].index("Regresión")
    assert failure[1] == (current, green)


def test_acceptance_failure_without_results_for_the_ids() -> None:
    failure = acceptance_failure("IVA-002", ["IVA-001"], {})
    assert failure is not None
    assert "sin resultado" in failure[0]
    assert "IVA-001" in failure[0]
    assert failure[1] == ()


FAILED_CASE = TestCaseResult("t", status="failure")
ERROR_CASE = TestCaseResult("t", status="error")
PASSED_CASE = TestCaseResult("t")


def test_run_outcome_spec_conflict_wins() -> None:
    code, reason = run_outcome(
        {"IVA-001": "quarantined", "IVA-002": "quarantined"},
        {"IVA-001": "spec_conflict", "IVA-002": "intentos agotados"},
    )
    assert code == ExitCode.SPEC_CONFLICT
    assert "IVA-001" in reason
    assert "IVA-002" not in reason


def test_run_outcome_exhausted_attempts() -> None:
    code, reason = run_outcome(
        {"IVA-001": "green", "IVA-002": "quarantined"}, {"IVA-002": "intentos agotados"}
    )
    assert code == ExitCode.QUARANTINED
    assert "IVA-002" in reason
    assert "IVA-001" not in reason
    assert "report.md" in reason


def test_run_outcome_quarantined_status_without_reason() -> None:
    code, _ = run_outcome({"IVA-001": "quarantined"}, {})
    assert code == ExitCode.QUARANTINED


def test_run_outcome_reason_without_status_still_quarantines() -> None:
    code, _ = run_outcome({}, {"IVA-001": "intentos agotados"})
    assert code == ExitCode.QUARANTINED


@pytest.mark.parametrize(
    "path",
    ["src/x.pth", "src/a-1.0.dist-info/METADATA", "tests/unit/conftest.py", "src/a/.gitignore"],
)
def test_forbidden_paths_match_any_component(path: str) -> None:
    patterns = ["*.pth", "*.dist-info", "conftest.py", ".gitignore"]
    assert forbidden_paths([path, "src/bien.py"], patterns) == [path]


def test_forbidden_paths_do_not_match_substrings() -> None:
    assert forbidden_paths(["src/myconftest.py.txt"], ["conftest.py"]) == []
    assert forbidden_paths(["src/x.pth.bak"], ["*.pth"]) == []


def test_forbidden_paths_with_no_patterns() -> None:
    assert forbidden_paths(["src/x.pth"], []) == []


@pytest.mark.parametrize("line", ["x = 1  # NOQA", "y  #noqa", "z  # NoQa: E501"])
def test_banned_matches_ignore_case(line: str) -> None:
    assert banned_matches([line], [r"#\s*noqa"]) == [r"#\s*noqa"]


def test_banned_matches_lists_each_pattern_once_and_sorted() -> None:
    lines = ["# b", "# a", "# b"]
    assert banned_matches(lines, ["# b", "# a", "# c"]) == ["# a", "# b"]


def test_banned_matches_clean_lines() -> None:
    assert banned_matches(["x = 1"], [r"#\s*noqa"]) == []


def test_suppression_matches_joins_literals_and_patterns_once_and_sorted() -> None:
    lines = ["x = 1  # noqa", "sys.path.insert(0, 'src')", "y = 2  # noqa"]

    found = suppression_matches(lines, ["# noqa", "# type: ignore"], [r"\bsys\.path\b"])

    assert found == ["# noqa", r"\bsys\.path\b"]
    assert suppression_matches(["x = 1"], ["# noqa"], [r"\bsys\.path\b"]) == []


_LITERALS = ["# noqa", "# type: ignore"]
_PATTERNS = [r"#\s*noqa", r"\bsys\.path\.(insert|append)\b"]
_IMPORT = "            from verifactu_py.services.xml_validator import (  # noqa: PLC0415"

# F72: what a change adds and removes, and what the suppressions rule refuses of it.
_MOVES: list[tuple[str, list[str], list[str], list[str]]] = [
    ("juntar tres en una", [_IMPORT.strip()], [_IMPORT] * 3, []),
    ("mover a otro módulo", ["    def f(x):  # noqa: ARG001"], ["def f(x):  # noqa: ARG001"], []),
    ("reindentar", ["        y = g()  # noqa: E501"], ["    y = g()  # noqa: E501"], []),
    ("sin supresiones", ["x = 1"], ["y = 2"], []),
    ("quitar una", [], ["x = 1  # noqa"], []),
    ("añadir una", ["x = 1  # noqa: E501"], [], ["# noqa: E501"]),
    (
        "una más de las que había",
        ["a  # noqa: E501", "b  # noqa: E501"],
        ["a  # noqa: E501"],
        ["# noqa: E501"],
    ),
    ("cambiar su código", ["x = 1  # noqa: F401"], ["x = 1  # noqa: E501"], ["# noqa: F401"]),
    ("ampliarla", ["x = 1  # noqa"], ["x = 1  # noqa: E501"], ["# noqa"]),
    (
        "type: ignore con código",
        ["x = f()  # type: ignore[arg-type]"],
        ["x = f()  # type: ignore"],
        ["# type: ignore[arg-type]"],
    ),
    (
        "un patrón con otro argumento",
        ["sys.path.insert(0, 'otro')"],
        ["sys.path.insert(0, 'src')"],
        ["sys.path.insert(0, 'otro')"],
    ),
    ("un patrón movido", ["    sys.path.append(p)"], ["sys.path.append(p)"], []),
]


@pytest.mark.parametrize(
    ("added", "removed", "found"), [m[1:] for m in _MOVES], ids=[m[0] for m in _MOVES]
)
def test_suppressions_not_moved_refuses_only_what_the_change_adds(
    added: list[str], removed: list[str], found: list[str]
) -> None:
    assert suppressions_not_moved(added, removed, _LITERALS, _PATTERNS) == found


def test_suppressions_not_moved_counts_literal_and_pattern_of_one_line_once_each() -> None:
    """`# noqa` is both a literal and a pattern of the profile: both keys are its same text."""
    assert suppressions_not_moved(
        ["x  # noqa", "y  # noqa"], ["x  # noqa"], _LITERALS, _PATTERNS
    ) == ["# noqa"]


def test_describe_suppressions_names_each_finding_once() -> None:
    assert describe_suppressions(["b", "a", "b"]) == (
        "Se detectaron supresiones no permitidas: a, b"
    )


def test_run_outcome_exact_reasons() -> None:
    conflict = {"IVA-001": "spec_conflict", "IVA-002": "spec_conflict"}
    assert run_outcome({}, conflict)[1] == (
        "Conflicto de especificación en IVA-001, IVA-002. Detalle en .lictor/report.md"
    )
    status = {"IVA-001": "quarantined", "IVA-002": "quarantined"}
    assert run_outcome(status, {})[1] == (
        "Escenarios en cuarentena: IVA-001, IVA-002. Detalle en .lictor/report.md"
    )


def test_run_outcome_all_green() -> None:
    code, reason = run_outcome({"IVA-001": "green", "IVA-002": "green"}, {})
    assert code == ExitCode.OK
    assert "2" in reason


@pytest.mark.parametrize(
    ("status", "failures", "expected"),
    [
        ("error", 0, 1),
        ("error", 1, 2),
        ("ok", 1, 0),
        ("max_turns", 1, 0),
        ("timeout", 1, 0),
        ("rate_limited", 1, 0),
    ],
)
def test_count_agent_failures_only_counts_consecutive_errors(
    status: AgentStatus, failures: int, expected: int
) -> None:
    assert count_agent_failures(status, failures) == expected


def test_agent_failure_limit_stops_before_a_third_call() -> None:
    assert AGENT_FAILURE_LIMIT == 2


@pytest.mark.parametrize(
    ("status", "failures", "expected"),
    [
        ("auth", 0, True),
        ("error", AGENT_FAILURE_LIMIT, True),
        ("error", AGENT_FAILURE_LIMIT - 1, False),
        ("ok", 0, False),
        ("max_turns", 0, False),
    ],
)
def test_is_agent_unavailable(status: AgentStatus, failures: int, expected: bool) -> None:
    assert is_agent_unavailable(status, failures) is expected


_ANCHOR = {"head": "a" * 40, "seal": "sha256:s", "repo": "sha256:r"}


def test_anchor_without_differences_has_no_problems() -> None:
    assert anchor_problems(_ANCHOR, _ANCHOR, {"a" * 40}) == []


def test_anchor_accepts_a_head_the_run_knows() -> None:
    observed = {**_ANCHOR, "head": "b" * 40}
    assert anchor_problems(_ANCHOR, observed, {"a" * 40, "b" * 40}) == []


def test_anchor_reports_a_moved_head_with_both_values() -> None:
    observed = {**_ANCHOR, "head": "b" * 40}
    problems = anchor_problems(_ANCHOR, observed, {"a" * 40})
    assert problems == [f"HEAD está en {'b' * 12} y la ejecución lo dejó en {'a' * 12}"]


def test_anchor_reports_a_head_that_could_not_be_read() -> None:
    problems = anchor_problems(_ANCHOR, {"seal": _ANCHOR["seal"]}, {"a" * 40})
    assert problems == [f"HEAD está en  y la ejecución lo dejó en {'a' * 12}"]


def test_anchor_reports_a_forged_seal() -> None:
    observed = {**_ANCHOR, "seal": "sha256:otro"}
    assert anchor_problems(_ANCHOR, observed, {"a" * 40}) == [
        ".lictor/seal.json no es el que escribió la ejecución"
    ]


def test_anchor_reports_a_missing_seal() -> None:
    observed = {**_ANCHOR, "seal": ""}
    assert len(anchor_problems(_ANCHOR, observed, {"a" * 40})) == 1


def test_anchor_before_sealing_ignores_the_seal() -> None:
    assert anchor_problems({**_ANCHOR, "seal": ""}, _ANCHOR, {"a" * 40}) == []


def test_anchor_of_a_run_that_recorded_nothing_has_no_problems() -> None:
    assert anchor_problems({}, _ANCHOR, set()) == []


@pytest.mark.parametrize(
    ("path", "in_tree", "deleted", "expected"),
    [
        ("tests/unit", True, [], "present"),
        ("tests/unit", True, ["tests/unit/test_a.py"], "present"),
        ("tests/unit", False, [], "absent"),
        ("tests/unit", False, ["tests/unit/test_a.py"], "removed"),
        ("tests/unit/", False, ["tests/unit/test_a.py"], "removed"),
        ("tests/unit", False, ["tests/unit"], "removed"),
        ("tests/unit", False, ["tests/unittest/test_a.py", "src/tests/unit/x.py"], "absent"),
    ],
)
def test_control_path_status_tells_absent_from_removed(
    path: str, in_tree: bool, deleted: list[str], expected: str
) -> None:
    """Only a path the base never had lets a control be skipped; removing one fails it."""
    assert control_path_status(path, in_tree=in_tree, deleted=deleted) == expected


@pytest.mark.parametrize(
    ("required", "installed", "expected"),
    [
        (None, (), None),
        ("pytest_cov.plugin", ("pytest_cov.plugin", "xdist.plugin"), None),
        ("pytest_cov.plugin", ("xdist.plugin",), "pytest_cov.plugin"),
        ("pytest_cov.plugin", (), "pytest_cov.plugin"),
    ],
)
def test_missing_plugin_is_only_what_the_environment_is_known_to_lack(
    required: str | None, installed: tuple[str, ...], expected: str | None
) -> None:
    assert missing_plugin(required, installed) == expected


# --- Phase 5: the oracle and the cycle of scenarios ---

IDS = ["IVA-001", "IVA-002"]
FILES = dict.fromkeys(IDS, "features/iva.feature")
Status = Literal["passed", "failure", "error", "skipped"]


def _linked(name: str, sid: str = "", feature: str = "features/iva.feature") -> TestCaseResult:
    props = {"lictor.scenario": sid, "lictor.feature": feature} if sid else {}
    return TestCaseResult(name=name, properties=props)


def test_map_by_scenario_follows_the_scenario_executed() -> None:
    first, second = _linked("test_uno", "IVA-001"), _linked("test_dos", "IVA-002")

    mapped = map_test_cases_to_scenarios([second, first], IDS, link="scenario", files=FILES)

    assert mapped == {"IVA-001": first, "IVA-002": second}


def test_map_by_scenario_ignores_tests_that_execute_no_scenario() -> None:
    helper = _linked("test_ayuda")
    first, second = _linked("test_iva_001", "IVA-001"), _linked("test_iva_002", "IVA-002")

    mapped = map_test_cases_to_scenarios([helper, first, second], IDS, link="scenario", files=FILES)

    assert unmapped_cases([helper, first, second], mapped) == [helper]


def test_map_by_scenario_rejects_a_test_named_after_a_scenario_it_does_not_execute() -> None:
    with pytest.raises(DriverError, match=r"test_iva_001 lleva el ID de IVA-001.*no ejecuta"):
        map_test_cases_to_scenarios([_linked("test_iva_001")], IDS, link="scenario", files=FILES)


def test_map_by_scenario_rejects_a_name_that_contradicts_the_scenario() -> None:
    cases = [_linked("test_iva_001", "IVA-002"), _linked("test_x", "IVA-001")]
    with pytest.raises(DriverError, match="nombre, pero ejecuta el escenario IVA-002"):
        map_test_cases_to_scenarios(cases, IDS, link="scenario", files=FILES)


def test_map_by_scenario_accepts_a_name_that_agrees() -> None:
    cases = [_linked("test_iva_001", "IVA-001"), _linked("test_iva_002", "IVA-002")]
    assert (
        map_test_cases_to_scenarios(cases, IDS, link="scenario", files=FILES)["IVA-001"] is cases[0]
    )


@pytest.mark.parametrize(
    "feature",
    ["", "features/copia.txt", "features/otra.feature", "otros/iva.feature", "features/iva"],
)
def test_map_by_scenario_requires_the_scenario_s_own_file(feature: str) -> None:
    """Before the seal a driver may write another file with the same tag, `.feature` or not."""
    cases = [_linked("test_a", "IVA-001", feature), _linked("test_b", "IVA-002")]
    with pytest.raises(
        DriverError, match=r"ejecuta IVA-001 desde .*no desde features/iva\.feature"
    ):
        map_test_cases_to_scenarios(cases, IDS, link="scenario", files=FILES)


def test_map_by_scenario_reads_each_scenario_from_its_own_file() -> None:
    files = {"IVA-001": "features/a.feature", "IVA-002": "features/sub/b.feature"}
    first = _linked("test_a", "IVA-001", "features/a.feature")
    second = _linked("test_b", "IVA-002", "features/sub/b.feature")

    assert map_test_cases_to_scenarios([first, second], IDS, link="scenario", files=files) == {
        "IVA-001": first,
        "IVA-002": second,
    }
    swapped = [_linked("test_a", "IVA-001", "features/sub/b.feature"), second]
    with pytest.raises(DriverError, match=r"no desde features/a\.feature"):
        map_test_cases_to_scenarios(swapped, IDS, link="scenario", files=files)


@pytest.mark.parametrize("files", [None, {"IVA-002": "features/iva.feature"}])
def test_map_by_scenario_links_no_scenario_whose_file_it_does_not_know(
    files: dict[str, str] | None,
) -> None:
    cases = [_linked("test_a", "IVA-001"), _linked("test_b", "IVA-002")]
    with pytest.raises(DriverError) as raised:
        map_test_cases_to_scenarios(cases, IDS, link="scenario", files=files)
    assert str(raised.value) == (
        "El test test_a ejecuta IVA-001 desde features/iva.feature, "
        "no desde el fichero de su escenario"
    )


def test_map_by_name_ignores_the_files() -> None:
    case = TestCaseResult(name="test_iva_001")
    files = {"IVA-001": "features/otra.feature"}
    assert map_test_cases_to_scenarios([case], ["IVA-001"], files=files) == {"IVA-001": case}


def test_map_by_scenario_requires_one_test_per_scenario() -> None:
    with pytest.raises(DriverError, match="Ningún test ejecuta el escenario 'IVA-002'"):
        map_test_cases_to_scenarios(
            [_linked("test_a", "IVA-001")], IDS, link="scenario", files=FILES
        )
    twice = [_linked("a", "IVA-001"), _linked("b", "IVA-001"), _linked("c", "IVA-002")]
    with pytest.raises(AmbiguousMappingError, match=r"'IVA-001' sale en 2 casos.*\['a', 'b'\]"):
        map_test_cases_to_scenarios(twice, IDS, link="scenario", files=FILES)


def test_map_by_scenario_rejects_a_test_that_executes_two_scenarios() -> None:
    both = TestCaseResult(
        name="t", properties={"lictor.scenario": "IVA-001 IVA-002", "lictor.feature": "features/f"}
    )
    with pytest.raises(DriverError, match="varios escenarios"):
        map_test_cases_to_scenarios([both], IDS, link="scenario", files=FILES)


def test_map_by_name_stays_the_default() -> None:
    case = TestCaseResult(name="test_iva_001")
    assert map_test_cases_to_scenarios([case], ["IVA-001"]) == {"IVA-001": case}


def test_unmapped_cases_compares_identity_not_equality() -> None:
    first, twin = TestCaseResult(name="t"), TestCaseResult(name="t")
    assert unmapped_cases([first, twin], {"IVA-001": first}) == [twin]


def test_classify_scenarios_by_their_recorded_hash() -> None:
    current = {"A-1": "h1", "A-2": "h2", "A-3": "h3", "A-4": "h4"}
    recorded: dict[str, str | None] = {"A-1": "h1", "A-2": "viejo", "A-4": None}

    assert classify_scenarios(current, recorded) == {
        "A-1": "green",
        "A-2": "reopened",
        "A-3": "new",
        "A-4": "reopened",
    }


ORACLE = RedOracle(
    red_kinds=("assertion", "not_implemented"), writable=("src/",), driver=("tests/acceptance/",)
)


def _failed(kind: str = "", origin: str = "") -> TestCaseResult:
    props = {"lictor.failure": kind, "lictor.origin": origin} if kind or origin else {}
    return TestCaseResult(name="test_iva_001", status="failure", properties=props)


@pytest.mark.parametrize(
    ("kind", "origin"),
    [
        ("assertion", "tests/acceptance/test_iva.py"),
        ("assertion", ""),
        ("not_implemented", "src/iva/__init__.py"),
        ("not_implemented", "src"),
    ],
)
def test_valid_red_kinds(kind: str, origin: str) -> None:
    assert red_failure_problem(_failed(kind, origin), ORACLE) is None


@pytest.mark.parametrize(
    ("kind", "origin", "expected"),
    [
        (
            "error",
            "tests/acceptance/test_iva.py",
            "falla con error en tests/acceptance/test_iva.py",
        ),
        ("error", "", "falla con error"),
        ("", "", "falla con un fallo sin clasificar"),
        ("not_implemented", "tests/acceptance/test_iva.py", "desde tests/acceptance/test_iva.py"),
        ("not_implemented", "", "desde fuera del proyecto, no desde la interfaz en src/"),
        ("not_implemented", "srcx/iva.py", "desde srcx/iva.py"),
    ],
)
def test_invalid_red_kinds(kind: str, origin: str, expected: str) -> None:
    problem = red_failure_problem(_failed(kind, origin), ORACLE)
    assert problem is not None
    assert expected in problem


def test_not_implemented_inside_a_driver_nested_in_writable_is_invalid() -> None:
    oracle = RedOracle(("not_implemented",), writable=("tests/",), driver=("tests/acceptance/",))
    assert red_failure_problem(_failed("not_implemented", "tests/acceptance/x.py"), oracle)


def test_without_red_kinds_any_failure_is_a_red() -> None:
    assert red_failure_problem(_failed("error", "tests/acceptance/x.py"), RedOracle()) is None


def _case(status: Status, kind: str = "assertion") -> TestCaseResult:
    props = {"lictor.failure": kind} if status != "passed" else {}
    return TestCaseResult(name=f"t_{status}", status=status, properties=props)


def test_judge_red_keeps_greens_passing_and_pendings_failing() -> None:
    mapped = {"G": _case("passed"), "N": _case("failure"), "R": _case("error", "not_implemented")}
    classes: dict[str, ScenarioClass] = {"G": "green", "N": "new", "R": "reopened"}

    verdict = judge_red(mapped, classes, RedOracle(), driver_fresh=True)

    assert verdict.ok
    assert verdict.pending == ("N", "R")
    assert (verdict.register, verdict.unproven, verdict.cases) == ((), (), ())
    assert verdict.respecified == ()


def test_judge_red_reports_a_broken_green_with_its_case() -> None:
    broken = _case("failure")
    verdict = judge_red({"G": broken}, {"G": "green"}, RedOracle(), driver_fresh=False)

    assert not verdict.ok
    assert verdict.problems == ("El escenario G estaba en verde y ahora no pasa: t_failure",)
    assert verdict.cases == (broken,)


def test_judge_red_reports_a_scenario_without_result() -> None:
    verdict = judge_red({}, {"N": "new"}, RedOracle(), driver_fresh=True)
    assert verdict.problems == ("El escenario N no tiene resultado",)


def test_judge_red_registers_a_reopened_scenario_a_sealed_driver_passes() -> None:
    verdict = judge_red({"R": _case("passed")}, {"R": "reopened"}, ORACLE, driver_fresh=False)
    assert (verdict.ok, verdict.register, verdict.pending) == (True, ("R",), ())
    assert (verdict.unproven, verdict.respecified) == ((), ())


def test_judge_red_leaves_a_reopened_scenario_that_passes_a_fresh_driver_to_a_person() -> None:
    """The step may still hold the old value: nothing tells it from a test that doesn't check."""
    verdict = judge_red({"R": _case("passed")}, {"R": "reopened"}, ORACLE, driver_fresh=True)
    assert (verdict.ok, verdict.respecified) == (True, ("R",))
    assert (verdict.register, verdict.unproven, verdict.pending) == ((), (), ())


def test_judge_red_registers_a_new_scenario_a_sealed_driver_already_proved() -> None:
    verdict = judge_red({"N": _case("passed")}, {"N": "new"}, ORACLE, driver_fresh=False)
    assert (verdict.ok, verdict.register, verdict.unproven) == (True, ("N",), ())


def test_judge_red_cannot_prove_a_new_scenario_that_passes_with_a_fresh_driver() -> None:
    verdict = judge_red({"N": _case("passed")}, {"N": "new"}, ORACLE, driver_fresh=True)
    assert (verdict.ok, verdict.register, verdict.unproven) == (True, (), ("N",))
    assert verdict.respecified == ()


@pytest.mark.parametrize("status", ["passed", "failure", "error", "skipped"])
@pytest.mark.parametrize("cls", ["green", "new", "reopened"])
def test_judge_red_never_registers_with_a_fresh_driver(status: Status, cls: ScenarioClass) -> None:
    """Only a sealed driver has proved its tests: nothing is green without the agent before."""
    verdict = judge_red({"S": _case(status)}, {"S": cls}, ORACLE, driver_fresh=True)
    assert verdict.register == ()


def test_judge_red_rejects_a_skipped_pending_test() -> None:
    verdict = judge_red({"N": _case("skipped")}, {"N": "new"}, ORACLE, driver_fresh=True)
    assert verdict.problems == (
        "El test t_skipped del escenario N debía fallar, pero su estado es skipped",
    )


@pytest.mark.parametrize("cls", ["new", "reopened"])
def test_judge_red_rejects_a_pending_that_fails_with_an_error(cls: ScenarioClass) -> None:
    bad = _case("failure", "error")
    verdict = judge_red({"N": bad}, {"N": cls}, ORACLE, driver_fresh=True)
    assert verdict.problems == (
        "El test t_failure del escenario N no falla por un rojo válido: falla con error",
    )
    assert verdict.cases == (bad,)
    assert verdict.pending == ()


def test_stub_lines_are_the_ones_impostor_source_replaces() -> None:
    pattern = r"^(\s*)raise NotImplementedError\b.*$"
    text = "def f():\n    raise NotImplementedError\n    # raise NotImplementedError\r\nx\n"
    assert stub_lines(text, pattern) == [2]
    assert stub_lines("", pattern) == []


def test_impostor_source_replaces_whole_matching_lines_only() -> None:
    pattern = r"^(\s*)raise NotImplementedError\b.*$"
    text = (
        "def f():\n    raise NotImplementedError\n"
        "def g():\n\traise NotImplementedError('x')\r\n"
        "    # raise NotImplementedError\n"
        "    raise NotImplementedErrorX\n"
        "raise NotImplementedError"
    )

    result, count = impostor_source(text, pattern, r"\1return None")

    assert count == 3
    assert result == (
        "def f():\n    return None\n"
        "def g():\n\treturn None\r\n"
        "    # raise NotImplementedError\n"
        "    raise NotImplementedErrorX\n"
        "return None"
    )


def test_impostor_source_without_matches_leaves_the_text() -> None:
    assert impostor_source("x = 1\n", r"^raise\b.*$", "return None") == ("x = 1\n", 0)


def test_impostor_problem_needs_a_run_that_maps_and_lost_every_stub() -> None:
    stub = TestCaseResult(
        name="t1", status="failure", properties={"lictor.failure": "not_implemented"}
    )
    fine = TestCaseResult(name="t2", status="failure", properties={"lictor.failure": "assertion"})

    assert impostor_problem("caída", None, [fine]) == "caída"
    assert impostor_problem(None, "sin mapeo", [fine]) == "sin mapeo"
    problem = impostor_problem(None, None, [fine, stub])
    assert problem is not None
    assert problem.startswith("t1 siguen lanzando NotImplementedError")
    assert "una sola línea" in problem
    assert impostor_problem(None, None, [fine, TestCaseResult(name="t3")]) is None


def test_collateral_greens_are_other_pendings_that_pass() -> None:
    results = {"A": "passed", "B": "passed", "C": "failure", "D": "passed"}
    assert collateral_greens(results, ["A", "B", "C", "E"], "A") == ["B"]


@pytest.mark.parametrize(
    ("failures", "expected"),
    [
        ([("error", "tests/acceptance/test_iva.py")] * 3, True),
        ([("error", "tests/acceptance/x.py"), ("error", "src/iva.py")], False),
        ([("error", "tests/acceptance/x.py"), ("assertion", "tests/acceptance/x.py")], False),
        ([("error", "tests/acceptance/x.py"), None], False),
        ([("error", "")], False),
        ([], False),
    ],
)
def test_suspicious_driver_needs_every_attempt_to_fail_inside_it(
    failures: list[tuple[str, str] | None], expected: bool
) -> None:
    assert suspicious_driver(failures, ["tests/acceptance/"]) is expected


def test_run_outcome_names_a_suspicious_driver_without_claiming_it() -> None:
    code, reason = run_outcome(
        {"IVA-001": "quarantined", "IVA-002": "quarantined"},
        {"IVA-001": SUSPICIOUS_DRIVER, "IVA-002": "intentos agotados"},
    )

    assert code == ExitCode.QUARANTINED
    assert "Escenarios en cuarentena: IVA-001, IVA-002." in reason
    assert "Posible driver defectuoso en IVA-001:" in reason
    assert "IVA-002:" not in reason


def test_run_outcome_without_suspicion_says_nothing_of_the_driver() -> None:
    _, reason = run_outcome({"IVA-001": "quarantined"}, {"IVA-001": "intentos agotados"})
    assert "driver" not in reason


def _baseline(
    *, pending_node: str | None = "red_check", head_parent: str = "ancla", head_tree: str = "arbol"
) -> bool:
    return baseline_head_accepted(
        pending_node=pending_node,
        head_parent=head_parent,
        head_tree=head_tree,
        anchored_head="ancla",
        anchored_tree="arbol",
    )


def test_baseline_head_is_accepted_on_top_of_the_anchor_during_red_check() -> None:
    assert _baseline() is True


def test_baseline_head_is_refused_otherwise() -> None:
    assert _baseline(pending_node="implement") is False
    assert _baseline(pending_node=None) is False
    assert _baseline(head_parent="otro") is False
    assert _baseline(head_tree="x") is False


def test_judge_red_lists_each_offender_once_in_the_order_of_the_scenarios() -> None:
    mapped = {"A": _case("failure"), "B": _case("passed"), "C": _case("skipped")}
    classes: dict[str, ScenarioClass] = {"C": "new", "A": "green", "B": "green"}

    verdict = judge_red(mapped, classes, RedOracle(), driver_fresh=True)

    assert [problem.split()[2] for problem in verdict.problems] == ["t_skipped", "A"]
    assert verdict.cases == (mapped["A"],)


def test_map_by_scenario_says_exactly_what_is_missing() -> None:
    with pytest.raises(DriverError) as unlinked:
        map_test_cases_to_scenarios([_linked("test_iva_001")], IDS, link="scenario", files=FILES)
    assert str(unlinked.value) == (
        "El test test_iva_001 lleva el ID de IVA-001 en el nombre, pero no ejecuta "
        "ningún escenario del Gherkin: enlázalo con su escenario"
    )
    with pytest.raises(DriverError) as missing:
        map_test_cases_to_scenarios([_linked("a", "IVA-001")], IDS, link="scenario", files=FILES)
    assert str(missing.value) == (
        "Ningún test ejecuta el escenario 'IVA-002': cada escenario necesita un test "
        "enlazado con su escenario del Gherkin"
    )
    with pytest.raises(DriverError) as outside:
        map_test_cases_to_scenarios(
            [_linked("a", "IVA-001", ""), _linked("b", "IVA-002")],
            IDS,
            link="scenario",
            files=FILES,
        )
    assert str(outside.value) == (
        "El test a ejecuta IVA-001 desde un fichero fuera del proyecto, "
        "no desde features/iva.feature"
    )


def test_map_by_scenario_tells_ids_apart_by_their_letters() -> None:
    """`test_abc_001` carries no ID of these scenarios, whatever its digits."""
    other = _linked("test_abc_001", "IVA-002")
    first = _linked("test_iva_001", "IVA-001")
    assert (
        map_test_cases_to_scenarios([first, other], IDS, link="scenario", files=FILES)["IVA-002"]
        is other
    )


def test_under_keeps_every_character_of_the_root_but_its_slashes() -> None:
    oracle = RedOracle(("not_implemented",), writable=("srcX/",), driver=("tests/X",))
    assert red_failure_problem(_failed("not_implemented", "srcX/iva.py"), oracle) is None
    assert red_failure_problem(_failed("not_implemented", "src/iva.py"), oracle) is not None
    assert control_path_status("unitX/", in_tree=False, deleted=["unitX/a.py"]) == "removed"
    assert control_path_status("Xunit", in_tree=False, deleted=["Xunit/a.py"]) == "removed"


def test_invalid_red_names_every_writable_root() -> None:
    oracle = RedOracle(("not_implemented",), writable=("src/", "lib/"), driver=("tests/",))
    assert red_failure_problem(_failed("not_implemented", "tests/x.py"), oracle) == (
        "lanza NotImplementedError desde tests/x.py, no desde la interfaz en src/, lib/"
    )


def test_impostor_source_drops_the_trailing_spaces_of_the_line_it_replaces() -> None:
    result, count = impostor_source(
        "    raise NotImplementedError  \n", r"^(\s*)raise\b.*$", r"\1x"
    )
    assert (result, count) == ("    x\n", 1)


def test_impostor_problem_says_exactly_how_to_write_a_stub() -> None:
    def stub(name: str) -> TestCaseResult:
        return TestCaseResult(
            name=name, status="failure", properties={"lictor.failure": "not_implemented"}
        )

    assert impostor_problem(None, None, [stub("t1"), stub("t2")]) == (
        "t1, t2 siguen lanzando NotImplementedError: escribe cada "
        "`raise NotImplementedError` en una sola línea"
    )


def test_run_outcome_suspicion_message_is_exact() -> None:
    _, reason = run_outcome(
        {"A-1": "quarantined", "A-2": "quarantined"},
        {"A-1": SUSPICIOUS_DRIVER, "A-2": SUSPICIOUS_DRIVER},
    )
    assert reason == (
        "Escenarios en cuarentena: A-1, A-2. Posible driver defectuoso en A-1, A-2: sus tests "
        "fallaron por un error del propio driver en cada intento. Detalle en .lictor/report.md"
    )


@pytest.mark.parametrize(
    ("status", "failures", "flags", "expected"),
    [
        ("ok", 0, {}, "ok"),
        ("max_turns", 0, {}, "error"),
        ("timeout", 0, {}, "error"),
        ("error", 1, {}, "error"),
        ("error", AGENT_FAILURE_LIMIT, {}, "unavailable"),
        ("auth", 0, {}, "unavailable"),
        ("rate_limited", 0, {}, "rate_limited"),
        ("ok", 0, {"spec_conflict": True}, "spec_conflict"),
        ("ok", 0, {"unreadable": True}, "rejected"),
        ("error", 1, {"unreadable": True}, "rejected"),
        ("ok", 0, {"tampered": True}, "rejected"),
        ("rate_limited", 0, {"tampered": True}, "rejected"),
        ("auth", 0, {"tampered": True}, "rejected"),
        ("ok", 0, {"spec_conflict": True, "tampered": True}, "rejected"),
        ("rate_limited", 0, {"unreadable": True}, "rate_limited"),
        ("auth", 0, {"unreadable": True}, "unavailable"),
        ("rate_limited", 0, {"spec_conflict": True}, "rate_limited"),
        ("auth", 0, {"spec_conflict": True}, "unavailable"),
        ("ok", 0, {"spec_conflict": True, "unreadable": True}, "spec_conflict"),
    ],
)
def test_classify_agent_puts_tampering_first_then_the_backend(
    status: AgentStatus, failures: int, flags: dict[str, bool], expected: AgentSignal
) -> None:
    assert classify_agent(status, failures, **flags) == expected


@pytest.mark.parametrize(
    ("signal", "failures", "expected"),
    [
        ("rate_limited", 0, True),
        ("unavailable", 0, True),
        ("error", 1, True),
        ("error", 0, False),
        ("ok", 0, False),
        ("ok", 1, False),
        ("rejected", 1, False),
        ("spec_conflict", 0, False),
    ],
)
def test_keeps_attempt_only_when_the_backend_failed(
    signal: AgentSignal, failures: int, expected: bool
) -> None:
    assert keeps_attempt(signal, failures) is expected


def _assistant(model: str | None) -> dict[str, Any]:
    message: dict[str, Any] = {"content": []}
    if model is not None:
        message["model"] = model
    return {"type": "assistant", "message": message}


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([], False),
        ([{"type": "system", "subtype": "init", "model": "claude"}], False),
        ([{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}], False),
        ([{"type": "system", "subtype": "init"}, _assistant(SYNTHETIC_MODEL)], False),
        ([{"type": "assistant", "message": "texto"}], True),
        ([_assistant("claude-haiku-5-5")], True),
        ([_assistant(None)], True),
        ([_assistant(SYNTHETIC_MODEL), _assistant("claude-haiku-5-5")], True),
        ([{"type": "user", "message": {"model": "claude"}}], False),
        ([{"type": "system", "subtype": "init"}, _assistant("claude-haiku-5-5")], True),
    ],
)
def test_model_responded_ignores_what_the_backend_wrote_itself(
    events: list[dict[str, Any]], expected: bool
) -> None:
    assert model_responded(events) is expected


def test_synthetic_model_is_the_backend_s_marker() -> None:
    assert SYNTHETIC_MODEL == "<synthetic>"


@pytest.mark.parametrize(
    ("path", "roots", "expected"),
    [
        ("src/a.py", ["src/"], True),
        ("src", ["src/"], True),
        ("src/x/y.py", ["/src"], True),
        ("srcx/a.py", ["src/"], False),
        ("tests/a.py", ["src/", "tests/unit/"], False),
        ("tests/unit/a.py", ["src/", "tests/unit/"], True),
        ("a.py", [""], True),
        ("a.py", [], False),
        ("lib/src/a.py", ["src"], False),
        ("a.py", ["X"], False),
        ("X/a.py", ["X"], True),
    ],
)
def test_path_within(path: str, roots: list[str], expected: bool) -> None:
    assert path_within(path, roots) is expected


def test_mutation_score_counts_timeouts_and_untested_as_not_killed() -> None:
    counts = {
        "killed": 80,
        "survived": 5,
        "timeout": 4,
        "no_tests": 3,
        "suspicious": 2,
        "segfault": 1,
        "skipped": 5,
        "total": 100,
    }

    assert mutation_score(counts) == MutationScore(**counts, percent=100 * 80 / 95)


def test_mutation_score_reads_missing_counts_as_zero() -> None:
    assert mutation_score({"killed": 3, "total": 4}) == MutationScore(
        killed=3, total=4, percent=75.0
    )


@pytest.mark.parametrize("counts", [{}, {"total": 2, "skipped": 2}, {"total": 1, "skipped": 3}])
def test_mutation_score_without_tried_mutants_is_zero(counts: dict[str, int]) -> None:
    assert mutation_score(counts).percent == 0.0


def test_describe_score_names_what_was_not_killed() -> None:
    score = mutation_score({"killed": 923, "survived": 57, "total": 980})

    assert describe_score(score) == "94,1 % (923 de 980 muertos; 57 vivos, 0 por tiempo)"


def test_describe_score_adds_the_other_outcomes_only_when_there_are_some() -> None:
    counts = {
        "killed": 80,
        "survived": 5,
        "timeout": 4,
        "no_tests": 3,
        "suspicious": 2,
        "segfault": 1,
        "skipped": 5,
        "total": 100,
    }

    assert describe_score(mutation_score(counts)) == (
        "84,2 % (80 de 95 muertos; 5 vivos, 4 por tiempo, 3 sin tests, 2 sospechosos, "
        "1 con segfault, 5 omitidos)"
    )


def test_mutation_score_of_a_single_tried_mutant() -> None:
    assert mutation_score({"killed": 1, "total": 1}).percent == 100.0


def test_the_smallest_error_budget_keeps_the_reason_and_the_first_case() -> None:
    cases = [
        TestCaseResult(
            name=f"test_iva_00{n}",
            status="failure",
            message="AssertionError: " + "x" * 280,
            details="\n".join(f"linea {i} de la traza" for i in range(40)),
        )
        for n in range(1, 4)
    ]

    text = condense_error("L2-unit", "Fallaron 3 casos de test", cases, max_chars=500)

    assert len(text) <= 500
    assert "Falló L2-unit" in text
    assert "Fallaron 3 casos de test" in text
    assert "test_iva_001" in text
    assert "AssertionError: xxxxxxxx" in text


def test_no_problem_exits_zero() -> None:
    assert precondition_exit_code([]) == 0


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        (["environment"], 40),
        (["environment", "environment"], 40),
        (["config"], 10),
        (["repository"], 10),
        (["environment", "config"], 10),
        (["environment", "repository", "environment"], 10),
    ],
)
def test_the_project_decides_before_the_machine(
    kinds: list[Literal["config", "repository", "environment"]], expected: int
) -> None:
    problems = [PreconditionProblem(kind, "x") for kind in kinds]

    assert precondition_exit_code(problems) == expected


def test_a_command_whose_program_is_found_is_no_problem() -> None:
    assert (
        missing_executables(
            {"del control 'lint'": "uv run ruff check src"}, lambda word: word == "uv"
        )
        == []
    )


def test_a_command_whose_program_is_missing_names_it_and_the_command() -> None:
    problems = missing_executables(
        {"del control 'secrets'": "gitleaks detect --no-banner"}, lambda _word: False
    )

    assert problems == [
        PreconditionProblem(
            "environment",
            "No se encuentra 'gitleaks', con el que empieza el comando del control 'secrets' "
            "('gitleaks detect --no-banner'). Instálalo o corrige el comando en lictor.json.",
        )
    ]


def test_an_empty_command_is_a_configuration_problem() -> None:
    asked: list[str] = []

    problems = missing_executables(
        {"de la aceptación": "  "}, lambda word: asked.append(word) is None
    )

    assert problems == [PreconditionProblem("config", "El comando de la aceptación está vacío")]
    assert asked == []


def test_only_the_first_word_is_resolved() -> None:
    asked: list[str] = []

    missing_executables(
        {"a": "'./scripts/run tests.sh' --flag x", "b": "uv run pytest"},
        lambda word: asked.append(word) is None,
    )

    assert asked == ["./scripts/run tests.sh", "uv"]


def test_a_program_inside_the_environment_is_left_for_the_build() -> None:
    asked: list[str] = []

    problems = missing_executables(
        {"x": "{env_dir}/bin/python -c 1"}, lambda word: asked.append(word) is None
    )

    assert problems == []
    assert asked == []


def test_every_command_is_checked() -> None:
    problems = missing_executables({"a": "x", "b": "", "c": "y"}, lambda _word: False)

    assert [p.kind for p in problems] == ["environment", "config", "environment"]


RUNS = [f"2026110{d}-120000-abc{d}" for d in range(1, 6)]


def test_runs_past_the_most_recent_are_pruned_oldest_first() -> None:
    assert runs_to_prune(list(reversed(RUNS)), keep=2, protected=()) == RUNS[:3]


def test_keeping_as_many_runs_as_there_are_prunes_nothing() -> None:
    assert runs_to_prune(RUNS, keep=5, protected=()) == []
    assert runs_to_prune(RUNS, keep=4, protected=()) == RUNS[:1]


def test_keeping_none_prunes_all_but_the_protected() -> None:
    assert runs_to_prune(RUNS, keep=0, protected=[RUNS[0]]) == RUNS[1:]
    assert runs_to_prune(RUNS, keep=-1, protected=()) == RUNS


def test_a_protected_run_is_never_pruned_and_still_counts() -> None:
    assert runs_to_prune(RUNS, keep=1, protected=[RUNS[1]]) == [RUNS[0], RUNS[2], RUNS[3]]


def test_names_without_the_shape_of_a_run_are_left_alone() -> None:
    others = ["check", "20261101-120000-abc", "x20261101-120000-abc1", "20261101-120000-abc1x"]

    assert runs_to_prune([*others, *RUNS], keep=4, protected=()) == RUNS[:1]


@pytest.mark.parametrize(
    ("percent", "minimum", "expected"),
    [(95.0, 95.0, True), (94.99, 95.0, False), (96.3, 95.0, True), (0.0, None, True)],
)
def test_a_score_meets_its_minimum_when_it_reaches_it(
    percent: float, minimum: float | None, expected: bool
) -> None:
    assert score_meets(MutationScore(percent=percent), minimum) is expected


CLASSES: dict[str, ScenarioClass] = {"A-1": "new", "A-2": "reopened", "A-3": "green"}


def test_new_and_reopened_scenarios_can_be_registered() -> None:
    assert registration_problems(["A-1", "A-2", "A-1"], CLASSES) == []


def test_registering_nothing_is_refused() -> None:
    assert registration_problems([], CLASSES) == ["Indica al menos un escenario que registrar."]


def test_every_scenario_that_cant_be_registered_is_named_once() -> None:
    assert registration_problems(["A-3", "X-9", "A-1", "X-9"], CLASSES) == [
        "A-3 ya está en verde para su especificación actual.",
        "No existe el escenario X-9 en features/ de HEAD.",
    ]


SCORE_94 = mutation_score({"killed": 94, "survived": 6, "total": 100})


def test_a_score_below_the_minimum_fails_the_control_and_says_both() -> None:
    assert enforce_min_score(Passed(), SCORE_94, 95.5) == Failed(
        "La puntuación de mutación, 94,0 % (94 de 100 muertos; 6 vivos, 0 por tiempo), "
        "no llega al mínimo de 95,5 %"
    )


def test_a_minimum_leaves_alone_a_verdict_that_already_failed() -> None:
    failed = Failed("la pasada cayó")
    assert enforce_min_score(failed, SCORE_94, 99.0) == failed
    assert enforce_min_score(failed, None, 99.0) == failed


def test_without_a_minimum_a_pass_stays_a_pass() -> None:
    assert enforce_min_score(Passed(), SCORE_94, None) == Passed()


def test_a_score_at_the_minimum_passes() -> None:
    assert enforce_min_score(Passed(), SCORE_94, 94) == Passed()


def test_error_for_agent_hides_the_state_files_the_agent_never_sees() -> None:
    error = "Falló L0-estado\n\nEl estado fue modificado: .lictor/checkpoints.sqlite"

    assert error_for_agent("L0-estado", error) == STATE_CHANGED_OUTSIDE_COPY
    assert ".lictor" not in STATE_CHANGED_OUTSIDE_COPY


@pytest.mark.parametrize("failed_at", [None, "L0-rutas", "red_check", "L2 (unit)"])
def test_error_for_agent_keeps_any_other_failure(failed_at: str | None) -> None:
    assert error_for_agent(failed_at, "Falló algo") == "Falló algo"


@pytest.mark.parametrize(
    "path",
    ["CLAUDE.md", "src/claude.md", "src/.Claude/settings.json", "tests/unit/CONFTEST.py", "a.PTH"],
)
def test_forbidden_paths_ignore_case(path: str) -> None:
    patterns = ["CLAUDE.md", ".claude", "conftest.py", "*.pth"]

    assert forbidden_paths([path], patterns) == [path]


@pytest.mark.parametrize(
    "path",
    [
        "src/mcp.json",
        "src/x.mcp.json",
        "src/.mcp.json.bak",
        "src/CLAUDE.mdx",
        "src/NOTCLAUDE.md",
        "src/.claude.json",
        "src/.githubx/ci.yml",
        "src/github/ci.yml",
        "src/vscode.py",
    ],
)
def test_forbidden_paths_match_whole_components_only(path: str) -> None:
    patterns = [".mcp.json", "CLAUDE.md", ".claude", ".github", ".vscode"]

    assert forbidden_paths([path], patterns) == []


@pytest.mark.parametrize(
    ("rejected", "expected"),
    [
        ([], []),
        ([["pyproject.toml"]], []),
        ([None, ["pyproject.toml"]], []),
        ([["pyproject.toml"], None], []),
        ([["pyproject.toml"], []], []),
        ([["src/conftest.py"], ["README.md"]], []),
        ([["pyproject.toml"], ["pyproject.toml"]], ["pyproject.toml"]),
        ([["b", "a", "c"], ["c", "a"]], ["a", "c"]),
        ([["x"], ["README.md"], ["x", "README.md"]], ["README.md"]),
        ([None, ["uv.lock"], ["uv.lock"]], ["uv.lock"]),
    ],
)
def test_paths_only_a_person_can_change(
    rejected: list[list[str] | None], expected: list[str]
) -> None:
    assert paths_only_a_person_can_change(rejected) == expected


def test_the_streak_is_two_attempts() -> None:
    assert PERSON_ONLY_STREAK == 2


def test_person_only_reason_names_the_scenario_and_the_paths() -> None:
    assert person_only_reason("ROM-007", [".github/workflows/x.yml", "uv.lock"]) == (
        "ROM-007 necesita cambiar .github/workflows/x.yml, uv.lock, que solo puede tocar una "
        "persona. Haz tú el cambio, confírmalo y vuelve a lanzar `lictor run`; si el escenario no "
        "debería necesitarlo, revisa el escenario."
    )


def test_routes_rejected_lists_the_paths_and_says_what_to_do() -> None:
    assert routes_rejected(["pyproject.toml", "uv.lock"]) == (
        "Archivos modificados fuera de las rutas permitidas: pyproject.toml, uv.lock. Esos "
        "cambios se han descartado: solo una persona puede hacerlos. Si el escenario no se puede "
        "cumplir sin ellos, repite exactamente el mismo cambio y Lictor parará para pedírselo a "
        "una persona; si no hacen falta, no los toques."
    )


@pytest.mark.parametrize(
    "path",
    ["features/iva.feature", "tests/acceptance/test_iva.py", "tests/acceptance/conftest.py"],
)
def test_insisting_on_the_oracle_is_never_a_persons_change(path: str) -> None:
    rejected: list[list[str] | None] = [[path, "uv.lock"], [path, "uv.lock"]]

    assert paths_only_a_person_can_change(rejected, ["tests/acceptance"]) == ["uv.lock"]
    assert paths_only_a_person_can_change([[path], [path]], ["tests/acceptance"]) == []


def test_routes_rejected_on_the_oracle_says_to_declare_the_conflict() -> None:
    message = routes_rejected(["tests/acceptance/test_iva.py"], ["tests/acceptance"])

    assert message == (
        "Archivos modificados fuera de las rutas permitidas: tests/acceptance/test_iva.py. Los "
        "tests de aceptación y los features no se tocan nunca: si contradicen el escenario, "
        "declara el conflicto como se indica al final."
    )


def test_routes_rejected_on_both_says_both() -> None:
    message = routes_rejected(["features/a.feature", "uv.lock"], ["tests/acceptance"])

    assert "repite exactamente el mismo cambio" in message
    assert message.endswith("declara el conflicto como se indica al final.")


def test_an_anchor_without_format_comes_from_an_older_version() -> None:
    assert anchor_from_older_version({"head": "a", "seal": "", "repo": "sha256:x"})
    assert anchor_from_older_version({"repo": "sha256:x", "format": "1"})
    assert not anchor_from_older_version({"repo": "sha256:x", "format": ANCHOR_FORMAT})


def test_a_run_not_anchored_yet_is_not_from_an_older_version() -> None:
    """Before `preflight` there is no anchor to compare, whatever version wrote the checkpoint."""
    assert not anchor_from_older_version({})
    assert not anchor_from_older_version({"head": "a", "repo": ""})


def test_a_registration_of_one_scenario_names_it_in_the_subject() -> None:
    assert registration_subject(["DNI-005"], "verde colateral") == "test(DNI-005): verde colateral"


def test_a_registration_of_several_counts_them_and_leaves_the_ids_to_the_trailers() -> None:
    """F38: eleven IDs made a 120-character subject; the `Scenario:` trailers already name them."""
    ids = [f"DNI-{n:03d}" for n in range(14, 25)]

    assert registration_subject(ids, "verde colateral") == "test: verde colateral, 11 escenarios"


def test_a_retired_run_is_named_after_its_run_and_its_id() -> None:
    assert retired_name("uno", RUNS[0]) == f"uno-{RUNS[0]}"


def test_retired_runs_past_the_most_recent_go_oldest_first_by_their_id() -> None:
    names = [retired_name(n, rid) for n, rid in zip("edcba", RUNS, strict=True)]

    assert retired_to_prune(names, keep=2) == sorted(names[:3])
    assert retired_to_prune(names, keep=5) == []
    assert retired_to_prune(names, keep=0) == sorted(names)


def test_a_name_without_a_run_id_at_its_end_is_not_a_retired_run() -> None:
    names = ["notas", f"{RUNS[0]}-copia", retired_name("uno", RUNS[1])]

    assert retired_to_prune(names, keep=0) == [retired_name("uno", RUNS[1])]


def _collection_failure(
    name: str, cause: str = "E   PackageNotFoundError: agentix"
) -> TestCaseResult:
    return TestCaseResult(
        name, status="error", message="collection failure", details=f"Traceback\n  x\n{cause}\n"
    )


def test_condense_error_counts_cases_that_failed_alike_once_with_every_name_that_fits() -> None:
    cases = [_collection_failure(f"tests.unit.test_{i}") for i in range(24)]

    condensed = condense_error("unit", "", cases, max_chars=4000)

    assert condensed.count("Mensaje: collection failure") == 1
    assert "- 24 casos con el mismo error: tests.unit.test_0, tests.unit.test_1," in condensed
    assert "tests.unit.test_23" in condensed
    assert "Causa: E   PackageNotFoundError: agentix" in condensed
    assert "más" not in condensed.split("Mensaje")[0]
    assert "Detalle" not in condensed  # the cause says it


def test_condense_error_names_that_dont_fit_are_counted() -> None:
    cases = [_collection_failure(f"tests.unit.test_modulo_largo_{i}") for i in range(40)]

    condensed = condense_error("unit", "", cases, max_chars=800)

    assert len(condensed) <= 800
    assert "- 40 casos con el mismo error: tests.unit.test_modulo_largo_0," in condensed
    shown = sum(
        f"test_modulo_largo_{i}," in condensed or f"test_modulo_largo_{i} y" in condensed
        for i in range(40)
    )
    assert f" y {40 - shown} más\n  Mensaje" in condensed
    assert "casos fallidos más" not in condensed


def test_condense_error_groups_keep_their_place_beside_a_lone_case() -> None:
    cases = [
        _collection_failure("a"),
        TestCaseResult("solo", status="failure", message="assert 1 == 2"),
        _collection_failure("b"),
        _collection_failure("c", cause="E   ImportError: otro"),
        _collection_failure("d", cause="E   ImportError: otro"),
    ]

    condensed = condense_error("unit", "", cases)

    first, lone, second = (
        condensed.index("2 casos con el mismo error: a, b"),
        condensed.index("- solo"),
        condensed.index("2 casos con el mismo error: c, d"),
    )
    assert first < lone < second


def test_condense_error_same_message_and_another_cause_are_apart() -> None:
    cases = [_collection_failure("a"), _collection_failure("b", cause="E   ImportError: otro")]

    condensed = condense_error("unit", "", cases)

    assert "casos con el mismo error" not in condensed
    assert "- a\n" in condensed
    assert "- b\n" in condensed


def test_condense_error_cases_with_nothing_to_share_are_not_grouped() -> None:
    cases = [TestCaseResult("a", status="failure"), TestCaseResult("b", status="failure")]

    assert "casos con el mismo error" not in condense_error("x", "", cases)


def test_same_error_groups_needs_message_or_cause() -> None:
    by_message = [TestCaseResult(n, status="failure", message="m") for n in "ab"]
    assert [[c.name for c in g] for g in same_error_groups(by_message)] == [["a", "b"]]


def test_same_error_groups_parts_cases_that_failed_alike_in_two_places() -> None:
    """M2 of 0.20.0: `AssertionError` with no message in two tests are two failures."""

    def failed(name: str, frame: str = "", line: str = "") -> TestCaseResult:
        properties = {"lictor.frame": frame, "lictor.line": line} if frame else {}
        return TestCaseResult(
            name, status="failure", details="E   AssertionError", properties=properties
        )

    cases = [
        failed("a", "tests/test_x.py", "3"),
        failed("b", "tests/test_x.py", "9"),
        failed("c", "tests/test_x.py", "3"),
        failed("d"),
        failed("e"),
    ]

    assert [[c.name for c in g] for g in same_error_groups(cases)] == [
        ["a", "c"],
        ["b"],
        ["d", "e"],
    ]


def test_same_error_groups_keeps_going_after_a_case_with_nothing_to_share() -> None:
    cases = [
        TestCaseResult("a", status="failure"),
        TestCaseResult("b", status="failure", message="m"),
        TestCaseResult("c", status="failure", message="m"),
    ]

    assert [[c.name for c in g] for g in same_error_groups(cases)] == [["a"], ["b", "c"]]


_TESTMAIN = TestCaseResult(name="TestMain", classname="example.com/toy/acceptance", status="failed")
_NIF_001 = TestCaseResult(name="Test_NIF_001", classname="acceptance", status="failed")


def test_a_sealed_driver_with_cases_missing_was_cut_short_not_misnamed() -> None:
    present = present_scenarios([_NIF_001], ["NIF-001", "NIF-002"], "name")

    reason = mapping_failure_reason(
        "No se encontró caso de test para el escenario 'NIF-002'",
        ["NIF-001", "NIF-002"],
        present,
        sealed=True,
        output="panic: runtime error: index out of range\n",
    )

    assert present == {"NIF-001"}
    assert reason.startswith(
        "La aceptación se cortó antes de ejecutar 1 test: su informe no tiene caso de NIF-002."
    )
    assert "no es el nombre de un test" in reason
    assert reason.endswith(
        "Final de la salida de la aceptación:\npanic: runtime error: index out of range"
    )


def test_a_fresh_driver_whose_report_has_no_scenario_gets_the_output() -> None:
    ids = ["NIF-001", "NIF-002"]
    output = "=== Errors\nacceptance/nif_test.go:16:16: undefined: nif.NoExiste\n"

    reason = mapping_failure_reason(
        "No se encontró",
        ids,
        present_scenarios([_TESTMAIN], ids, "name"),
        sealed=False,
        output=output,
    )

    assert reason.startswith("No se encontró\nNingún caso del informe es de un escenario.")
    assert reason.endswith("undefined: nif.NoExiste")
    assert mapping_failure_reason("x", ids, set(), sealed=False, output=" ") == "x"
    assert mapping_failure_reason("x", ids, {"NIF-001"}, sealed=False, output=output) == "x"
    assert mapping_failure_reason("x", ids, set(ids), sealed=True, output=output) == "x"


def test_two_missing_cases_are_counted() -> None:
    reason = mapping_failure_reason("x", ["A-001", "A-002"], set(), sealed=True, output="")

    assert reason.startswith(
        "La aceptación se cortó antes de ejecutar 2 tests: su informe no tiene caso de "
        "A-001, A-002."
    )
    assert reason.endswith("antes de llegar a ellos.")


def test_by_scenario_a_case_is_present_by_what_it_executed() -> None:
    case = TestCaseResult(name="t", classname="c", properties={"lictor.scenario": "A-001"})

    assert present_scenarios([case], ["A-001", "A-002"], "scenario") == {"A-001"}
    assert present_scenarios([case], ["A-002"], "scenario") == frozenset()


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ([], None),
        (["A-001"], None),
        ([None, "A-001"], None),
        (["A-001", None], None),
        (["A-001", "A-001"], "A-001"),
        (["B-002", "A-001", "A-001"], "A-001"),
        (["A-001", "B-002"], None),
        ([None, None], None),
    ],
)
def test_the_sealed_driver_stops_the_run_after_two_verifications_agree(
    history: list[str | None], expected: str | None
) -> None:
    assert sealed_driver_cases(history) == expected


def test_only_a_verification_that_reached_the_mapping_counts_for_the_streak() -> None:
    found = ("A-001", ("a", "b"))
    assert with_mapping(["A-001"], None, mapped=False) == ["A-001"]
    assert with_mapping(["A-001"], None, mapped=True) == ["A-001", None]
    assert with_mapping(["A-001"], found, mapped=False) == ["A-001", "A-001"]
    assert with_mapping([], ("B-002", ()), mapped=True) == ["B-002"]


def test_a_person_stops_the_run_for_a_path_first_and_for_the_sealed_driver_after() -> None:
    found = ("A-001", ("a", "b"))
    assert person_stop_reason("B-002", [], ["A-001"], found) is None
    assert person_stop_reason("B-002", [], ["A-001", "A-001"], None) is None
    sealed = person_stop_reason("B-002", [], ["A-001", "A-001"], found)
    assert sealed == sealed_driver_reason("A-001", ("a", "b"))
    paths = person_stop_reason("B-002", ["pyproject.toml"], ["A-001", "A-001"], found)
    assert paths == person_only_reason("B-002", ["pyproject.toml"])


def test_the_implementer_hears_both_causes_and_the_person_what_to_do() -> None:
    cases = ["Test_PLZ_007/a", "Test_PLZ_007"]
    assert ambiguous_with_sealed_driver("PLZ-007", cases) == (
        "El escenario 'PLZ-007' sale en 2 casos del informe de la aceptación: "
        "['Test_PLZ_007/a', 'Test_PLZ_007'], y cada escenario tiene que ser un solo caso. Si un "
        "test tuyo lleva el ID en el nombre y la aceptación lo ejecuta, renómbralo. Si los casos "
        "son del driver sellado (un subtest aparece cuando el código ya llega a él), no puedes "
        "cambiarlos: no toques nada por eso, y si vuelve a pasar Lictor parará para pedírselo a "
        "una persona."
    )
    assert sealed_driver_reason("PLZ-007", cases) == (
        "El driver sellado da 2 casos para el escenario PLZ-007: ['Test_PLZ_007/a', "
        "'Test_PLZ_007'], y cada escenario tiene que ser un solo caso (un subtest o un test "
        "parametrizado sale como otro). El implementador no puede cambiar un driver sellado, y "
        "así no se verifica ningún escenario. Los verdes ya confirmados en la rama siguen "
        "valiendo: intégrala o descártala, retira esta ejecución y lanza otra, que construye su "
        "driver de nuevo; antes, corrige `driver_hints` para que no lo repita."
    )
    assert str(AmbiguousMappingError("PLZ-007", cases)) == (
        "El escenario 'PLZ-007' sale en 2 casos del informe: ['Test_PLZ_007/a', "
        "'Test_PLZ_007']. Cada escenario es un solo caso: un subtest o un test parametrizado sale "
        "como otro caso con el mismo ID. Recorre los casos dentro de un solo test, y que el "
        "mensaje de cada fallo diga cuál falló."
    )


@pytest.mark.parametrize(
    ("driver_files", "any_green", "shallow", "pending"),
    [
        (False, False, False, True),
        # A driver, or a scenario once green, is a project that started: its acceptance runs.
        (True, False, False, False),
        (False, True, False, False),
        # Without the whole history no green can be seen: nothing passes for not started.
        (False, False, True, False),
    ],
)
def test_the_acceptance_is_pending_only_before_a_first_run(
    driver_files: bool, any_green: bool, shallow: bool, pending: bool
) -> None:
    from lictor.domain.rules import acceptance_pending

    assert (
        acceptance_pending(driver_files=driver_files, any_green=any_green, shallow=shallow)
        is pending
    )
