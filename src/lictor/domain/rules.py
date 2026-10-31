"""Pure domain rules for Lictor."""

import re
from collections.abc import Sequence

from lictor.domain.errors import DriverError
from lictor.domain.model import AgentStatus, ScenarioId, TestCaseResult

AGENT_FAILURE_LIMIT = 2
_MAX_EXIT_CODE_TAIL_LINES = 40
_MAX_JUNIT_DETAIL_LINES = 15


def map_test_cases_to_scenarios(
    test_cases: Sequence[TestCaseResult],
    scenario_ids: Sequence[ScenarioId | str],
) -> dict[str, TestCaseResult]:
    """Map test cases to scenario IDs with strict 1:1 validation.

    Each scenario ID is normalized to lowercase alphanumeric with underscores (e.g. 'iva_001').
    Matching uses boundaries (^|_)norm_id(_|$) against normalized test case name and classname.
    Raises DriverError if any scenario has zero or multiple matching test cases, or if
    a test case matches multiple scenarios.
    """
    mapped: dict[str, TestCaseResult] = {}
    matched_tc_indices: set[int] = set()

    for sid in scenario_ids:
        sid_str = str(sid.value if isinstance(sid, ScenarioId) else sid).strip()
        norm_id = re.sub(r"[^a-z0-9]+", "_", sid_str.lower()).strip("_")
        pattern = re.compile(rf"(^|_){re.escape(norm_id)}(_|$)")

        matches: list[tuple[int, TestCaseResult]] = []
        for idx, tc in enumerate(test_cases):
            norm_name = re.sub(r"[^a-z0-9]+", "_", tc.name.lower())
            norm_classname = re.sub(r"[^a-z0-9]+", "_", tc.classname.lower())
            if pattern.search(norm_name) or pattern.search(norm_classname):
                matches.append((idx, tc))

        if not matches:
            msg = f"No se encontró caso de test para el escenario '{sid_str}'"
            raise DriverError(msg)

        if len(matches) > 1:
            names = [m[1].name for m in matches]
            msg = f"Múltiples casos de test encontrados para el escenario '{sid_str}': {names}"
            raise DriverError(msg)

        match_idx, matched_tc = matches[0]
        if match_idx in matched_tc_indices:
            msg = f"El caso de test '{matched_tc.name}' coincide con múltiples escenarios"
            raise DriverError(msg)

        matched_tc_indices.add(match_idx)
        mapped[sid_str] = matched_tc

    return mapped


def check_red_rule(
    scenario_results: dict[str, str],
    green_scenario_ids: Sequence[str] | set[str],
) -> bool:
    """Verify red rule: green scenarios must pass, all other scenarios must fail.

    Returns True if:
    - All scenarios in green_scenario_ids are present in scenario_results and passed.
    - All other scenarios in scenario_results have status 'failure' or 'error'.
    Otherwise returns False.
    """
    green_set = set(green_scenario_ids)

    # All green scenarios must be present and passed
    for green_id in green_set:
        if scenario_results.get(green_id) != "passed":
            return False

    # All non-green scenarios must have failed
    for sid, status in scenario_results.items():
        if sid not in green_set and status not in ("failure", "error"):
            return False

    return True


def condense_error(
    failed_at: str,
    raw_error: str,
    test_cases: Sequence[TestCaseResult] = (),
    max_chars: int = 4000,
) -> str:
    """Condense error output for prompt feedback to agent.

    - Header: 'Falló <failed_at>'
    - For JUnit: failed test cases with name, message, and first 15 lines of details.
    - For exit-code: tail of stdout/stderr up to 40 lines.
    - Strict truncation to max_chars.
    """
    header = f"Falló {failed_at}"
    failed_cases = [tc for tc in test_cases if tc.status in ("failure", "error")]

    if failed_cases:
        items: list[str] = []
        for tc in failed_cases:
            parts = [f"- {tc.name}"]
            if tc.message:
                parts.append(f"  Mensaje: {tc.message.strip()}")
            if tc.details:
                detail_lines = tc.details.splitlines()[:_MAX_JUNIT_DETAIL_LINES]
                parts.append("  Detalle:")
                for dl in detail_lines:
                    parts.append(f"    {dl}")
            items.append("\n".join(parts))
        body = "\n\n".join(items)
    else:
        lines = raw_error.splitlines()
        tail = (
            lines[-_MAX_EXIT_CODE_TAIL_LINES:] if len(lines) > _MAX_EXIT_CODE_TAIL_LINES else lines
        )
        body = "\n".join(tail).strip()

    full = f"{header}\n\n{body}" if body else header
    if len(full) > max_chars:
        return full[:max_chars]
    return full


def count_agent_failures(status: AgentStatus, failures: int) -> int:
    """Count consecutive calls the agent itself failed, as opposed to failing a verification."""
    return failures + 1 if status == "error" else 0


def is_agent_unavailable(status: AgentStatus, failures: int) -> bool:
    """Tell whether the backend is at fault, so that no scenario should pay for it."""
    return status == "auth" or failures >= AGENT_FAILURE_LIMIT
