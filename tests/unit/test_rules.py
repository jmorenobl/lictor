"""Unit tests for pure domain rules."""

import pytest

from lictor.domain.errors import DriverError, ExitCode
from lictor.domain.model import AgentStatus, ScenarioId, TestCaseResult
from lictor.domain.rules import (
    AGENT_FAILURE_LIMIT,
    acceptance_failure,
    anchor_problems,
    banned_matches,
    check_red_rule,
    condense_error,
    count_agent_failures,
    describe_red_violation,
    forbidden_paths,
    is_agent_unavailable,
    map_test_cases_to_scenarios,
    red_rule_offenders,
    render_full_error,
    run_outcome,
    suite_crash_reason,
)


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

    with pytest.raises(DriverError, match="Múltiples casos de test"):
        map_test_cases_to_scenarios(test_cases, scenario_ids)


def test_map_test_cases_same_test_for_multiple_scenarios_raises_driver_error() -> None:
    test_cases = [
        TestCaseResult(name="test_iva_001_and_iva_002", status="passed"),
    ]
    scenario_ids = ["IVA-001", "IVA-002"]

    with pytest.raises(DriverError):
        map_test_cases_to_scenarios(test_cases, scenario_ids)


def test_check_red_rule_all_valid() -> None:
    scenario_results = {
        "IVA-001": "passed",
        "IVA-002": "failure",
        "IVA-003": "error",
    }
    green_ids = ["IVA-001"]
    assert check_red_rule(scenario_results, green_ids) is True


def test_check_red_rule_green_scenario_regression() -> None:
    scenario_results = {
        "IVA-001": "failure",
        "IVA-002": "failure",
    }
    green_ids = ["IVA-001"]
    assert check_red_rule(scenario_results, green_ids) is False


def test_check_red_rule_green_scenario_missing() -> None:
    scenario_results = {
        "IVA-002": "failure",
    }
    green_ids = ["IVA-001"]
    assert check_red_rule(scenario_results, green_ids) is False


def test_check_red_rule_pending_scenario_passed_fails_red() -> None:
    scenario_results = {
        "IVA-001": "passed",
        "IVA-002": "passed",  # Pending scenario passed before implementation!
    }
    green_ids = ["IVA-001"]
    assert check_red_rule(scenario_results, green_ids) is False


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


def test_red_rule_offender_is_the_pending_that_passed() -> None:
    results = {"IVA-001": "failure", "IVA-002": "passed", "IVA-003": "error"}
    assert red_rule_offenders(results, []) == ["IVA-002"]


def test_red_rule_offender_is_the_green_that_failed() -> None:
    results = {"IVA-001": "failure", "IVA-002": "failure"}
    assert red_rule_offenders(results, ["IVA-001"]) == ["IVA-001"]


def test_red_rule_offender_includes_a_green_without_result() -> None:
    assert red_rule_offenders({}, ["IVA-001"]) == ["IVA-001"]


def test_red_rule_offender_includes_a_skipped_pending() -> None:
    assert red_rule_offenders({"IVA-001": "skipped"}, []) == ["IVA-001"]


def test_red_rule_has_no_offenders_when_it_holds() -> None:
    assert red_rule_offenders({"IVA-001": "passed", "IVA-002": "error"}, ["IVA-001"]) == []


def test_red_rule_violation_names_only_the_offender() -> None:
    bad = TestCaseResult("test_iva_003", status="passed")
    mapped = {"IVA-001": TestCaseResult("test_iva_001", status="failure"), "IVA-003": bad}
    reason, cases = describe_red_violation(mapped, [])
    assert "test_iva_003" in reason
    assert "IVA-003" in reason
    assert "debía fallar" in reason
    assert "test_iva_001" not in reason
    assert cases == ()


def test_red_rule_violation_lists_one_offender_per_line() -> None:
    mapped = {
        "IVA-001": TestCaseResult("test_iva_001", status="passed"),
        "IVA-002": TestCaseResult("test_iva_002", status="skipped"),
    }
    reason, _ = describe_red_violation(mapped, [])
    assert reason == (
        "El test test_iva_001 del escenario IVA-001 debía fallar, pero su estado es passed\n"
        "El test test_iva_002 del escenario IVA-002 debía fallar, pero su estado es skipped"
    )


def test_red_rule_violation_keeps_the_failure_of_a_green() -> None:
    broken = TestCaseResult("test_iva_001", status="failure", message="m")
    reason, cases = describe_red_violation({"IVA-001": broken}, ["IVA-001"])
    assert "IVA-001" in reason
    assert "verde" in reason
    assert "test_iva_001" in reason
    assert cases == (broken,)


def test_red_rule_violation_with_a_green_without_test() -> None:
    reason, cases = describe_red_violation({}, ["IVA-001"])
    assert "IVA-001" in reason
    assert cases == ()


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


@pytest.mark.parametrize(
    ("exit_code", "cases"),
    [(0, (PASSED_CASE,)), (0, (FAILED_CASE,)), (1, (FAILED_CASE,)), (1, (ERROR_CASE,))],
)
def test_suite_ran_when_exit_code_matches_the_report(
    exit_code: int, cases: tuple[TestCaseResult, ...]
) -> None:
    assert suite_crash_reason(exit_code, (1,), cases) is None


def test_suite_crashed_without_a_report() -> None:
    reason = suite_crash_reason(3, (1,), ())
    assert reason is not None
    assert "código 3" in reason
    assert "sin" in reason


def test_suite_without_cases_counts_as_crashed_even_on_exit_zero() -> None:
    assert suite_crash_reason(0, (1,), ()) is not None


def test_suite_crashed_with_a_code_that_does_not_mean_failed_tests() -> None:
    reason = suite_crash_reason(3, (1,), (FAILED_CASE,))
    assert reason is not None
    assert "código 3" in reason


def test_suite_failure_codes_come_from_the_caller() -> None:
    assert suite_crash_reason(2, (1, 2), (FAILED_CASE,)) is None


def test_suite_crashed_when_it_failed_but_no_test_did() -> None:
    reason = suite_crash_reason(1, (1,), (PASSED_CASE,))
    assert reason is not None
    assert "ningún test" in reason


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
