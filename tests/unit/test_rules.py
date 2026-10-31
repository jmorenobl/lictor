"""Unit tests for pure domain rules."""

import pytest

from lictor.domain.errors import DriverError
from lictor.domain.model import AgentStatus, ScenarioId, TestCaseResult
from lictor.domain.rules import (
    AGENT_FAILURE_LIMIT,
    check_red_rule,
    condense_error,
    count_agent_failures,
    is_agent_unavailable,
    map_test_cases_to_scenarios,
)


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
