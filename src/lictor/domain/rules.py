"""Pure domain rules for Lictor."""

import re
from collections.abc import Sequence

from lictor.domain.errors import DriverError, ExitCode
from lictor.domain.model import AgentStatus, ScenarioId, TestCaseResult

AGENT_FAILURE_LIMIT = 2
_MAX_EXIT_CODE_TAIL_LINES = 40
_MAX_JUNIT_DETAIL_LINES = 15
_MAX_ASSERTION_LINES = 10
_MAX_MESSAGE_CHARS = 300
_ASSERTION_LINE = re.compile(r"^\s*E\s|\bassert\b|\w*(Error|Exception)\b")
_CLIPPED = " […] "
_OMISSION_RESERVE = 60
_FAILED_STATUSES = ("failure", "error")


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
            msg = (
                f"No se encontró caso de test para el escenario '{sid_str}': "
                f"el nombre del test debe contener '{norm_id}'"
            )
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


def red_rule_offenders(
    scenario_results: dict[str, str],
    green_scenario_ids: Sequence[str] | set[str],
) -> list[str]:
    """Return the scenarios that break the red rule, and only those.

    A green scenario must be present and passed; any other scenario must have failed.
    """
    green_set = set(green_scenario_ids)
    offenders = [sid for sid in sorted(green_set) if scenario_results.get(sid) != "passed"]
    offenders.extend(
        sid
        for sid, status in scenario_results.items()
        if sid not in green_set and status not in _FAILED_STATUSES
    )
    return offenders


def check_red_rule(
    scenario_results: dict[str, str],
    green_scenario_ids: Sequence[str] | set[str],
) -> bool:
    """Verify red rule: green scenarios must pass, all other scenarios must fail."""
    return not red_rule_offenders(scenario_results, green_scenario_ids)


def describe_red_violation(
    mapped: dict[str, TestCaseResult],
    green_scenario_ids: Sequence[str] | set[str],
) -> tuple[str, tuple[TestCaseResult, ...]]:
    """Explain a broken red rule, leaving out the pending scenarios that fail as expected."""
    results = {sid: str(tc.status) for sid, tc in mapped.items()}
    green_set = set(green_scenario_ids)
    reasons: list[str] = []
    cases: list[TestCaseResult] = []
    for sid in red_rule_offenders(results, green_set):
        case = mapped.get(sid)
        if sid not in green_set and case is not None:
            reasons.append(
                f"El test {case.name} del escenario {sid} debía fallar, "
                f"pero su estado es {case.status}"
            )
        elif case is not None:
            reasons.append(f"El escenario {sid} estaba en verde y ahora no pasa: {case.name}")
            cases.append(case)
        else:
            reasons.append(f"El escenario {sid} estaba en verde y no tiene resultado")
    return "\n".join(reasons), tuple(cases)


def acceptance_failure(
    scenario_id: str,
    green_scenario_ids: Sequence[str],
    mapped: dict[str, TestCaseResult],
) -> tuple[str, tuple[TestCaseResult, ...]] | None:
    """Explain why an attempt fails acceptance, or return None if it passes.

    Only the current scenario and broken greens matter: pending scenarios are expected to fail.
    """
    reasons: list[str] = []
    cases: list[TestCaseResult] = []
    current = mapped.get(scenario_id)
    if current is None:
        reasons.append(f"Escenario {scenario_id} no pasó (sin resultado)")
    elif current.status != "passed":
        reasons.append(f"Escenario {scenario_id} no pasó ({current.status})")
        cases.append(current)
    for green_id in green_scenario_ids:
        case = mapped.get(green_id)
        if case is None or case.status != "passed":
            reasons.append(f"Regresión detectada en escenario verde previo: {green_id}")
            if case is not None:
                cases.append(case)
    return ("\n".join(reasons), tuple(cases)) if reasons else None


def suite_crash_reason(
    exit_code: int,
    failure_exit_codes: Sequence[int],
    test_cases: Sequence[TestCaseResult],
) -> str | None:
    """Tell a suite that crashed from one that ran, whose report can then be trusted.

    A non-zero exit is normal while scenarios are pending, but only with a code that means
    "tests failed" and a report that shows it.
    """
    if not test_cases:
        return f"La suite terminó con código {exit_code} sin casos de test en su informe"
    if exit_code == 0:
        return None
    if exit_code not in failure_exit_codes:
        return f"La suite se interrumpió con código {exit_code}: su informe no es fiable"
    if not any(tc.status in _FAILED_STATUSES for tc in test_cases):
        return f"La suite terminó con código {exit_code} pero ningún test falló en su informe"
    return None


def _clip(text: str, limit: int) -> str:
    """Bound a text keeping both ends, where the reason and the summary usually are."""
    if len(text) <= limit:
        return text
    room = limit - len(_CLIPPED)
    if room <= 0:
        return text[:limit]
    return f"{text[: room - room // 2]}{_CLIPPED}{text[len(text) - room // 2 :]}"


def _condense_reason(raw_error: str) -> str:
    """Keep the first line, where callers put the reason, and the tail of what follows."""
    lines = raw_error.strip().splitlines()
    if len(lines) <= _MAX_EXIT_CODE_TAIL_LINES:
        return "\n".join(lines)
    return "\n".join([lines[0], *lines[-_MAX_EXIT_CODE_TAIL_LINES:]])


def _case_summary(case: TestCaseResult) -> str:
    if not case.message:
        return f"- {case.name}"
    return f"- {case.name}\n  Mensaje: {_clip(case.message.strip(), _MAX_MESSAGE_CHARS)}"


def _case_details(case: TestCaseResult) -> str:
    lines = (case.details or "").splitlines()
    kept = lines[:_MAX_JUNIT_DETAIL_LINES]
    assertions = [ln for ln in lines[_MAX_JUNIT_DETAIL_LINES:] if _ASSERTION_LINE.search(ln)]
    if assertions:
        kept = [*kept, "...", *assertions[-_MAX_ASSERTION_LINES:]]
    return "\n".join(["  Detalle:", *(f"    {line}" for line in kept)]) if kept else ""


def _condense_cases(cases: Sequence[TestCaseResult], limit: int) -> str:
    """List as many cases as fit, then share what is left among their details."""
    summaries: list[str] = []
    used = 0
    for case in cases:
        summary = _case_summary(case)
        if used + len(summary) + 2 > limit - _OMISSION_RESERVE:
            break
        summaries.append(summary)
        used += len(summary) + 2

    items: list[str] = []
    for index, summary in enumerate(summaries):
        share = (limit - _OMISSION_RESERVE - used) // (len(summaries) - index)
        details = _clip(_case_details(cases[index]), share - 1) if share > 1 else ""
        used += len(details) + 1 if details else 0
        items.append(f"{summary}\n{details}" if details else summary)

    omitted = len(cases) - len(summaries)
    if omitted:
        items.append(f"… y {omitted} casos fallidos más")
    return "\n\n".join(items)


def condense_error(
    failed_at: str,
    raw_error: str,
    test_cases: Sequence[TestCaseResult] = (),
    max_chars: int = 4000,
) -> str:
    """Condense an error for the agent's next prompt, so that the reason survives the limit.

    In order of priority: header, reason (`raw_error`), and for each failed case its name and
    message, then its first lines and assertion lines. Callers pass only the cases that matter;
    the rest belongs in the full error, under `runs/`.
    """
    header = f"Falló {failed_at}"
    limit = max_chars - len(header) - 2
    reason = _condense_reason(raw_error)
    failed_cases = [tc for tc in test_cases if tc.status in _FAILED_STATUSES]

    if failed_cases:
        kept_reason = _clip(reason, limit // 2)
        room = limit - len(kept_reason) - 2
        body = "\n\n".join(filter(None, [kept_reason, _condense_cases(failed_cases, room)]))
    else:
        body = _clip(reason, limit)

    full = f"{header}\n\n{body}" if body else header
    return full[:max_chars]


def render_full_error(
    failed_at: str,
    raw_error: str,
    test_cases: Sequence[TestCaseResult] = (),
    output: str = "",
) -> str:
    """Render the error without any limit, for the attempt's directory under `runs/`."""
    parts = [f"Falló {failed_at}"]
    if raw_error.strip():
        parts.append(raw_error.strip())
    for case in test_cases:
        if case.status in _FAILED_STATUSES:
            lines = [f"- {case.name}"]
            if case.message:
                lines.append(f"  Mensaje: {case.message.strip()}")
            if case.details:
                lines.append(case.details.rstrip())
            parts.append("\n".join(lines))
    if output.strip():
        parts.append(f"Salida:\n{output.strip()}")
    return "\n\n".join(parts) + "\n"


def run_outcome(status: dict[str, str], reasons: dict[str, str]) -> tuple[int, str]:
    """Return the exit code of a run that reached the end, and the reason for it."""
    conflicts = sorted(sid for sid, reason in reasons.items() if reason == "spec_conflict")
    if conflicts:
        return int(ExitCode.SPEC_CONFLICT), (
            f"Conflicto de especificación en {', '.join(conflicts)}. Detalle en .lictor/report.md"
        )
    quarantined = sorted({sid for sid, st in status.items() if st == "quarantined"} | set(reasons))
    if quarantined:
        return int(ExitCode.QUARANTINED), (
            f"Escenarios en cuarentena: {', '.join(quarantined)}. Detalle en .lictor/report.md"
        )
    greens = sum(1 for st in status.values() if st == "green")
    return int(ExitCode.OK), f"Ejecución completada: {greens} escenarios en verde"


def count_agent_failures(status: AgentStatus, failures: int) -> int:
    """Count consecutive calls the agent itself failed, as opposed to failing a verification."""
    return failures + 1 if status == "error" else 0


def is_agent_unavailable(status: AgentStatus, failures: int) -> bool:
    """Tell whether the backend is at fault, so that no scenario should pay for it."""
    return status == "auth" or failures >= AGENT_FAILURE_LIMIT
