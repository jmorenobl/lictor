"""Pure domain rules for Lictor."""

import fnmatch
import re
import shlex
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Literal

from lictor.domain.errors import DriverError, ExitCode
from lictor.domain.model import (
    AgentSignal,
    AgentStatus,
    ControlResult,
    CoverageScore,
    MutationScore,
    PreconditionProblem,
    ScenarioId,
    Score,
    TestCaseResult,
)

ScenarioClass = Literal["green", "reopened", "new"]
MapLink = Literal["name", "scenario"]

AGENT_FAILURE_LIMIT = 2
SYNTHETIC_MODEL = "<synthetic>"  # what the backend writes as the model of a message it made up
_MAX_EXIT_CODE_TAIL_LINES = 40
_MAX_JUNIT_DETAIL_LINES = 15
_MAX_ASSERTION_LINES = 10
_MAX_MESSAGE_CHARS = 300
_ASSERTION_LINE = re.compile(r"^\s*E\s|\bassert\b|\w*(Error|Exception)\b")
_CLIPPED = " […] "
_OMISSION_RESERVE = 60
_FAILED_STATUSES = ("failure", "error")
_NOT_IMPLEMENTED = "not_implemented"
SUSPICIOUS_DRIVER = "driver_sospechoso"
# What the clock generates: the date first, so that sorting the names sorts the runs.
RUN_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
_ENV_PLACEHOLDER = "{env_dir}"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower())


def _under(path: str, roots: Sequence[str]) -> bool:
    """Tell whether a relative path is one of `roots` or inside one of them."""
    for root in roots:
        base = root.strip("/")
        if base and (path == base or path.startswith(f"{base}/")):
            return True
    return False


def map_test_cases_to_scenarios(
    test_cases: Sequence[TestCaseResult],
    scenario_ids: Sequence[ScenarioId | str],
    link: MapLink = "name",
    files: Mapping[str, str] | None = None,
) -> dict[str, TestCaseResult]:
    """Map test cases to scenario IDs with strict 1:1 validation.

    By `name`, each scenario ID is normalized to lowercase alphanumeric with underscores (e.g.
    'iva_001') and matched with boundaries (^|_)norm_id(_|$) against the normalized test case name
    and classname. By `scenario`, a test belongs to the scenario its runner says it executed, read
    from the scenario's own file in `files` (relative to the project); a scenario with no file
    there can't be linked. Raises DriverError if any scenario has zero or multiple matching test
    cases, or if a test case matches multiple scenarios.
    """
    if link != "name":
        return _map_by_scenario(test_cases, [str(sid) for sid in scenario_ids], files or {})
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


def _named_ids(case: TestCaseResult, scenario_ids: Sequence[str]) -> list[str]:
    """Return the scenarios whose ID the test carries in its name or class."""
    names = (_normalize(case.name), _normalize(case.classname))
    found: list[str] = []
    for sid in scenario_ids:
        pattern = re.compile(rf"(^|_){re.escape(_normalize(sid))}(_|$)")
        if any(pattern.search(name) for name in names):
            found.append(sid)
    return found


def _linked_id(
    case: TestCaseResult, scenario_ids: Sequence[str], files: Mapping[str, str]
) -> str | None:
    """Return the scenario a test executed, checking its name and the file it read it from."""
    linked = [sid for sid in scenario_ids if sid in case.scenario_tags]
    named = _named_ids(case, scenario_ids)
    if not linked:
        if named:
            raise DriverError(
                f"El test {case.name} lleva el ID de {named[0]} en el nombre, pero no ejecuta "
                "ningún escenario del Gherkin: enlázalo con su escenario"
            )
        return None
    if len(linked) > 1:
        raise DriverError(f"El test {case.name} ejecuta varios escenarios: {linked}")
    if named and named != linked:
        raise DriverError(
            f"El test {case.name} lleva el ID de {named[0]} en el nombre, "
            f"pero ejecuta el escenario {linked[0]}"
        )
    expected = files.get(linked[0])
    if not expected or case.feature != expected:
        # Another file may carry the same tag: the runner reads any file it's given.
        raise DriverError(
            f"El test {case.name} ejecuta {linked[0]} desde "
            f"{case.feature or 'un fichero fuera del proyecto'}, no desde "
            f"{expected or 'el fichero de su escenario'}"
        )
    return linked[0]


def _map_by_scenario(
    test_cases: Sequence[TestCaseResult], scenario_ids: Sequence[str], files: Mapping[str, str]
) -> dict[str, TestCaseResult]:
    """Map each scenario to the one test that executed it, not to a test that is named after it."""
    found: dict[str, list[TestCaseResult]] = {sid: [] for sid in scenario_ids}
    for case in test_cases:
        sid = _linked_id(case, scenario_ids, files)
        if sid is not None:
            found[sid].append(case)
    mapped: dict[str, TestCaseResult] = {}
    for sid, cases in found.items():
        if not cases:
            raise DriverError(
                f"Ningún test ejecuta el escenario '{sid}': cada escenario necesita un test "
                "enlazado con su escenario del Gherkin"
            )
        if len(cases) > 1:
            names = [case.name for case in cases]
            raise DriverError(f"Múltiples casos de test ejecutan el escenario '{sid}': {names}")
        mapped[sid] = cases[0]
    return mapped


def unmapped_cases(
    test_cases: Sequence[TestCaseResult], mapped: Mapping[str, TestCaseResult]
) -> list[TestCaseResult]:
    """Return the test cases that belong to no scenario: the oracle is only the scenarios."""
    return [case for case in test_cases if not any(case is m for m in mapped.values())]


def classify_scenarios(
    current: Mapping[str, str], recorded: Mapping[str, str | None]
) -> dict[str, ScenarioClass]:
    """Say which scenarios are still green, which changed since they were, and which never were.

    `recorded` holds, for each scenario that has a green commit, the hash its specification had in
    that commit's tree, or None if the scenario wasn't found there.
    """
    classes: dict[str, ScenarioClass] = {}
    for sid, spec_hash in current.items():
        if sid not in recorded:
            classes[sid] = "new"
        elif recorded[sid] == spec_hash:
            classes[sid] = "green"
        else:
            classes[sid] = "reopened"
    return classes


@dataclass(frozen=True)
class RedOracle:
    """What a red has to look like: the failure kinds that count, and where code may live."""

    red_kinds: Sequence[str] = ()
    writable: Sequence[str] = ()
    driver: Sequence[str] = ()


@dataclass(frozen=True)
class RedVerdict:
    """What the red run says of each scenario that isn't green."""

    problems: tuple[str, ...] = ()
    cases: tuple[TestCaseResult, ...] = ()
    pending: tuple[str, ...] = ()
    register: tuple[str, ...] = ()  # pass with a validated test: green without the agent
    unproven: tuple[str, ...] = ()  # new, pass with a test nobody has validated yet
    respecified: tuple[str, ...] = ()  # reopened, pass with a test nobody has validated yet

    @property
    def ok(self) -> bool:
        return not self.problems


def red_failure_problem(case: TestCaseResult, oracle: RedOracle) -> str | None:
    """Explain why a failed test isn't a valid red, or return None if it is.

    Without `red_kinds` any failure counts, as for a profile whose runner reports no kind.
    """
    if not oracle.red_kinds:
        return None
    kind, origin = case.failure_kind, case.origin
    where = f" en {origin}" if origin else ""
    if kind not in oracle.red_kinds:
        return f"falla con {kind or 'un fallo sin clasificar'}{where}"
    if kind == _NOT_IMPLEMENTED and (
        not _under(origin, oracle.writable) or _under(origin, oracle.driver)
    ):
        return (
            f"lanza NotImplementedError desde {origin or 'fuera del proyecto'}, "
            f"no desde la interfaz en {', '.join(oracle.writable)}"
        )
    return None


def judge_red(
    mapped: Mapping[str, TestCaseResult],
    classes: Mapping[str, ScenarioClass],
    oracle: RedOracle,
    *,
    driver_fresh: bool,
) -> RedVerdict:
    """Judge each scenario of the red run by its class.

    A green must pass. A pending one must fail with a valid red, unless the sealed driver already
    proved its test: then passing registers a reopened one again and makes a new one a collateral
    green. With a fresh driver nothing tells a pass from a vacuous test: a new one is `unproven`,
    for the agent to fix, and a reopened one is `respecified`, for a person to decide whether its
    specification changed only in wording.
    """
    problems: list[str] = []
    cases: list[TestCaseResult] = []
    pending: list[str] = []
    register: list[str] = []
    unproven: list[str] = []
    respecified: list[str] = []
    for sid, cls in classes.items():
        case = mapped.get(sid)
        if case is None:
            problems.append(f"El escenario {sid} no tiene resultado")
        elif cls == "green":
            if case.status != "passed":
                problems.append(f"El escenario {sid} estaba en verde y ahora no pasa: {case.name}")
                cases.append(case)
        elif case.status == "passed":
            if not driver_fresh:
                register.append(sid)
            else:
                (respecified if cls == "reopened" else unproven).append(sid)
        elif case.status not in _FAILED_STATUSES:
            problems.append(
                f"El test {case.name} del escenario {sid} debía fallar, "
                f"pero su estado es {case.status}"
            )
        else:
            reason = red_failure_problem(case, oracle)
            if reason is None:
                pending.append(sid)
            else:
                problems.append(
                    f"El test {case.name} del escenario {sid} no falla por un rojo válido: {reason}"
                )
                cases.append(case)
    return RedVerdict(
        tuple(problems),
        tuple(cases),
        tuple(pending),
        tuple(register),
        tuple(unproven),
        tuple(respecified),
    )


@dataclass(frozen=True)
class Impostors:
    """How to turn a stub into a constant: the profile knows its language's stubs."""

    pattern: str
    replacements: tuple[str, ...]


def impostor_source(text: str, pattern: str, replacement: str) -> tuple[str, int]:
    """Replace every line that matches `pattern` and say how many were replaced."""
    regex = re.compile(pattern)
    lines = text.splitlines(keepends=True)
    replaced = 0
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        if regex.fullmatch(body):
            lines[index] = regex.sub(replacement, body) + line[len(body) :]
            replaced += 1
    return "".join(lines), replaced


def impostor_problem(
    crash: str | None, mapping_error: str | None, test_cases: Sequence[TestCaseResult]
) -> str | None:
    """Tell an impostor that can prove nothing: it crashed, didn't map, or kept a stub."""
    if crash is not None:
        return crash
    if mapping_error is not None:
        return mapping_error
    kept = [case.name for case in test_cases if case.failure_kind == _NOT_IMPLEMENTED]
    if kept:
        return (
            f"{', '.join(kept)} siguen lanzando NotImplementedError: escribe cada "
            "`raise NotImplementedError` en una sola línea"
        )
    return None


def impostor_survivors(scenario_ids: Sequence[str], runs: Sequence[Mapping[str, str]]) -> list[str]:
    """Return the scenarios whose test passed with every impostor: nothing it asserts can fail."""
    return [sid for sid in scenario_ids if all(results.get(sid) == "passed" for results in runs)]


def collateral_greens(
    scenario_results: Mapping[str, str], pending_ids: Sequence[str], current: str
) -> list[str]:
    """Return the pending scenarios, other than `current`, that the same tree also makes pass."""
    return [sid for sid in pending_ids if sid != current and scenario_results.get(sid) == "passed"]


def registration_subject(scenario_ids: Sequence[str], what: str) -> str:
    """Return the subject of a commit that registers scenarios without changing a file.

    One scenario keeps its ID as the scope. Several are counted: the `Scenario:` trailers name
    each of them, and eleven IDs made a subject of 120 characters (F38).
    """
    if len(scenario_ids) == 1:
        return f"test({scenario_ids[0]}): {what}"
    return f"test: {what}, {len(scenario_ids)} escenarios"


def suspicious_driver(
    failures: Sequence[tuple[str, str] | None], driver_paths: Sequence[str]
) -> bool:
    """Tell whether every attempt failed with an error raised inside the driver itself.

    It suggests the driver, not the implementation, is wrong; it doesn't prove it. An attempt
    without a test result (`None`) says nothing, so it rules the suspicion out.
    """
    return bool(failures) and all(
        failure is not None and failure[0] == "error" and _under(failure[1], driver_paths)
        for failure in failures
    )


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


STATE_CHANGED_OUTSIDE_COPY = (
    "El intento se descartó sin verificarlo: el estado de la ejecución cambió fuera de tu copia "
    "de trabajo, que no lo contiene. Implementa el escenario de nuevo."
)


NO_COMMITS = "El repositorio no tiene commits: haz el primero."


def error_for_agent(failed_at: str | None, error: str) -> str:
    """What the next prompt says of a failed attempt.

    A failure at `L0-estado` names files the agent never sees, in `.lictor/` and `.git`: the list
    would accuse it of something it can't do, and anything that reads the checkpoint while it works
    can cause one. The journal (`state.tampered`) and the evidence keep the names.
    """
    return STATE_CHANGED_OUTSIDE_COPY if failed_at == "L0-estado" else error


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
        suspicious = sorted(sid for sid, reason in reasons.items() if reason == SUSPICIOUS_DRIVER)
        note = (
            f" Posible driver defectuoso en {', '.join(suspicious)}: sus tests fallaron por un "
            "error del propio driver en cada intento."
            if suspicious
            else ""
        )
        return int(ExitCode.QUARANTINED), (
            f"Escenarios en cuarentena: {', '.join(quarantined)}.{note} "
            "Detalle en .lictor/report.md"
        )
    greens = sum(1 for st in status.values() if st == "green")
    return int(ExitCode.OK), f"Ejecución completada: {greens} escenarios en verde"


ControlPathStatus = Literal["present", "absent", "removed"]


def control_path_status(path: str, *, in_tree: bool, deleted: Sequence[str]) -> ControlPathStatus:
    """Tell whether a control's path is there, was never there, or the attempt removed it.

    Only a path that neither the base nor the candidate has lets the control be skipped: removing
    it would otherwise turn a failing control into a skipped one.
    """
    if in_tree:
        return "present"
    root = path.strip("/")
    if any(gone == root or gone.startswith(f"{root}/") for gone in deleted):
        return "removed"
    return "absent"


def missing_plugin(required: str | None, installed: Sequence[str] | None) -> str | None:
    """The plugin a control needs and the environment is known to lack, or None.

    `installed` is None when the profile can't list what the environment has: the control then
    runs, and its own failure says what is missing.
    """
    if required is None or installed is None or required in installed:
        return None
    return required


def plugin_skip_reason(plugin: str) -> str:
    """Why a control whose plugin the environment lacks didn't run, and what makes it run."""
    return (
        f"El entorno de verificación no tiene el plugin {plugin}: es una dependencia de "
        "desarrollo que solo puede añadir una persona"
    )


def forbidden_paths(paths: Sequence[str], patterns: Sequence[str]) -> list[str]:
    """Return the paths with any component matching a pattern, as `*.pth` or `conftest.py`.

    Case doesn't count: on a filesystem that ignores it, as macOS's does by default, whatever
    opens `CLAUDE.md` or `.claude/` reads `claude.md` or `.Claude/` just the same.
    """
    folded = [pattern.casefold() for pattern in patterns]
    return [
        path
        for path in paths
        if any(
            fnmatch.fnmatchcase(part.casefold(), pattern)
            for part in path.split("/")
            for pattern in folded
        )
    ]


def banned_matches(lines: Sequence[str], patterns: Sequence[str]) -> list[str]:
    """Return the regular expressions, ignoring case, that some line matches."""
    return sorted(
        pattern
        for pattern in patterns
        if any(re.search(pattern, line, re.IGNORECASE) for line in lines)
    )


def suppression_matches(
    lines: Sequence[str], suppressions: Sequence[str], patterns: Sequence[str]
) -> list[str]:
    """What added lines carry that L0-supresiones refuses: literal suppressions and patterns.

    The same rule judges what the implementer adds and what a fresh driver adds, which is sealed
    with the oracle and never judged again (F12).
    """
    literal = [s for s in suppressions if any(s in line for line in lines)]
    return sorted({*banned_matches(lines, patterns), *literal})


def describe_suppressions(found: Sequence[str]) -> str:
    """The message of L0-supresiones, naming what was found."""
    return f"Se detectaron supresiones no permitidas: {', '.join(sorted(set(found)))}"


def count_agent_failures(status: AgentStatus, failures: int) -> int:
    """Count consecutive calls the agent itself failed, as opposed to failing a verification."""
    return failures + 1 if status == "error" else 0


def is_agent_unavailable(status: AgentStatus, failures: int) -> bool:
    """Tell whether the backend is at fault, so that no scenario should pay for it."""
    return status == "auth" or failures >= AGENT_FAILURE_LIMIT


def classify_agent(
    status: AgentStatus,
    failures: int,
    *,
    spec_conflict: bool = False,
    tampered: bool = False,
    unreadable: bool = False,
) -> AgentSignal:
    """Decide what a call means: tampering first, then the backend, then what the agent left.

    `failures` already counts this call. A copy that can't be read only matters when the agent
    finished its turn: a declared conflict or a backend at fault says more.
    """
    if tampered:
        return "rejected"
    if status == "rate_limited":
        return "rate_limited"
    if is_agent_unavailable(status, failures):
        return "unavailable"
    if spec_conflict:
        return "spec_conflict"
    if unreadable:
        return "rejected"
    return "ok" if status == "ok" else "error"


def keeps_attempt(signal: AgentSignal, failures: int) -> bool:
    """Tell whether a call leaves the attempt unspent: the backend failed, not the work."""
    if signal in ("rate_limited", "unavailable"):
        return True
    return signal == "error" and failures > 0


PERSON_ONLY_STREAK = 2
ROUTES_REJECTED = "Archivos modificados fuera de las rutas permitidas"
ROUTES_GUIDANCE = (
    "Esos cambios se han descartado: solo una persona puede hacerlos. Si el escenario no se puede "
    "cumplir sin ellos, repite exactamente el mismo cambio y Lictor parará para pedírselo a una "
    "persona; si no hacen falta, no los toques."
)
ORACLE_GUIDANCE = (
    "Los tests de aceptación y los features no se tocan nunca: si contradicen el escenario, "
    "declara el conflicto como se indica al final."
)
FEATURES_ROOT = "features"


def _oracle(paths_driver: Sequence[str]) -> list[str]:
    """What tells whether a scenario is met: its features and the sealed driver."""
    return [FEATURES_ROOT, *paths_driver]


def routes_rejected(paths: Sequence[str], paths_driver: Sequence[str] = ()) -> str:
    """What `L0-rutas` says of an attempt: what it discarded, and what the agent may do next.

    Only a change outside the oracle may be asked of a person; one to the oracle is never needed
    to meet a scenario, and a test that contradicts it is a conflict to declare.
    """
    oracle = _oracle(paths_driver)
    guidance = []
    if any(not path_within(path, oracle) for path in paths):
        guidance.append(ROUTES_GUIDANCE)
    if any(path_within(path, oracle) for path in paths):
        guidance.append(ORACLE_GUIDANCE)
    return f"{ROUTES_REJECTED}: {', '.join(paths)}. {' '.join(guidance)}"


def paths_only_a_person_can_change(
    rejected: Sequence[Sequence[str] | None], paths_driver: Sequence[str] = ()
) -> list[str]:
    """The paths the last attempts of a scenario insisted on, though only a person may change them.

    `rejected` holds, attempt by attempt, what `L0-rutas` discarded, or None if it didn't stop
    there. A slip, as a `conftest.py` out of habit, happens once, or changes from one attempt to
    the next. The same path again, after the retry said it was discarded and to make it again only
    if the scenario needs it, is the agent saying that it does: nothing it can write will do.
    The oracle never counts: insisting on rewriting a sealed test is cheating, not a need, and
    stays an attempt spent like any other.
    """
    last = rejected[-PERSON_ONLY_STREAK:]
    if len(last) < PERSON_ONLY_STREAK or any(not paths for paths in last):
        return []
    common = set(last[0] or ())
    for paths in last[1:]:
        common &= set(paths or ())
    oracle = _oracle(paths_driver)
    return sorted(path for path in common if not path_within(path, oracle))


def person_only_reason(scenario_id: str, paths: Sequence[str]) -> str:
    """Why the run stops: the scenario needs a change that is a person's to make."""
    joined = ", ".join(paths)
    return (
        f"{scenario_id} necesita cambiar {joined}, que solo puede tocar una persona. Haz tú el "
        "cambio, confírmalo y vuelve a lanzar `lictor run`; si el escenario no debería "
        "necesitarlo, revisa el escenario."
    )


def model_responded(events: Iterable[Mapping[str, Any]]) -> bool:
    """Tell whether the model itself wrote anything in a stream of agent events.

    Only an assistant message counts, and not one the backend made up on the model's behalf:
    the stream's own start and its rate limit notices don't come from the model.
    """
    for event in events:
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        model = message.get("model") if isinstance(message, Mapping) else None
        if model != SYNTHETIC_MODEL:
            return True
    return False


def path_within(path: str, roots: Sequence[str]) -> bool:
    """Tell whether a relative path is one of `roots` or inside one; "" stands for the project."""
    target = PurePosixPath(path)
    for root in roots:
        base = PurePosixPath(root.strip("/"))
        if target == base or target.is_relative_to(base):
            return True
    return False


_MUTATION_COUNTS = ("killed", "survived", "timeout", "no_tests", "suspicious", "segfault")


def mutation_score(counts: Mapping[str, int]) -> MutationScore:
    """Score a mutation run: what the tests killed, out of every mutant that was tried.

    Only skipped mutants leave the denominator. A timeout isn't counted as killed: it may come
    from a loaded machine as well as from the mutant, so it stays apart where it can be seen.
    """
    found = {name: counts.get(name, 0) for name in (*_MUTATION_COUNTS, "skipped", "total")}
    tried = found["total"] - found["skipped"]
    percent = 100 * found["killed"] / tried if tried > 0 else 0.0
    return MutationScore(
        killed=found["killed"],
        survived=found["survived"],
        timeout=found["timeout"],
        no_tests=found["no_tests"],
        suspicious=found["suspicious"],
        segfault=found["segfault"],
        skipped=found["skipped"],
        total=found["total"],
        percent=percent,
    )


def coverage_score(totals: Mapping[str, int]) -> CoverageScore:
    """Score a coverage run as coverage.py does: lines and branches run, out of all of them.

    With nothing to measure nothing is left uncovered, so it is complete, as coverage.py says.
    """
    lines, statements = totals.get("covered_lines", 0), totals.get("num_statements", 0)
    branches, all_branches = totals.get("covered_branches", 0), totals.get("num_branches", 0)
    measured = statements + all_branches
    percent = 100 * (lines + branches) / measured if measured > 0 else 100.0
    return CoverageScore(
        covered_lines=lines,
        num_statements=statements,
        covered_branches=branches,
        num_branches=all_branches,
        percent=percent,
    )


def _percent_text(percent: float) -> str:
    """One decimal, cut and not rounded: 89,99 must never read as a 90 it doesn't reach."""
    tenths = int(percent * 10 + 1e-9)
    return f"{tenths // 10},{tenths % 10} %"


def describe_score(score: Score) -> str:
    """Say a score with what it counts beside it, so a ✓ never stands alone."""
    if isinstance(score, CoverageScore):
        counted = f"{score.covered_lines} de {score.num_statements} líneas"
        if score.num_branches:
            counted += f", {score.covered_branches} de {score.num_branches} ramas"
        return f"{_percent_text(score.percent)} ({counted})"
    tried = score.total - score.skipped
    apart = [f"{score.survived} vivos", f"{score.timeout} por tiempo"]
    for count, label in (
        (score.no_tests, "sin tests"),
        (score.suspicious, "sospechosos"),
        (score.segfault, "con segfault"),
        (score.skipped, "omitidos"),
    ):
        if count:
            apart.append(f"{count} {label}")
    return f"{_percent_text(score.percent)} ({score.killed} de {tried} muertos; {', '.join(apart)})"


def score_from_record(raw: object) -> Score | None:
    """Read back a score the journal recorded, or None if it can't be read.

    0.2.0 only had mutation scores and wrote no `kind`, so a record without one is a mutation's.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    fields = {key: value for key, value in raw.items() if key != "kind"}
    kind = raw.get("kind", "mutation")
    try:
        if kind == "coverage":
            return CoverageScore(**fields)
        if kind == "mutation":
            return MutationScore(**fields)
    except TypeError:
        return None
    return None


def baseline_head_accepted(
    *,
    pending_node: str | None,
    head_parent: str,
    head_tree: str,
    anchored_head: str,
    anchored_tree: str,
) -> bool:
    """Accept the commit a red check registers on top of the anchored head, and nothing else.

    It changes no file and the resume repeats the red check, which tests every green again: a
    trailer forged in that gap is refuted on the spot.
    """
    return (
        pending_node == "red_check" and head_parent == anchored_head and head_tree == anchored_tree
    )


# 0.3.0 also hashes the common directory of a linked worktree; 0.2.0 anchors carry no format.
ANCHOR_FORMAT = "2"


def anchor_from_older_version(anchor: Mapping[str, str]) -> bool:
    """Tell an anchor another version wrote: comparing it would read as tampering.

    A run that `preflight` hasn't anchored yet has nothing to compare, whoever started it.
    """
    return bool(anchor.get("repo")) and anchor.get("format") != ANCHOR_FORMAT


def anchor_problems(
    anchor: Mapping[str, str],
    observed: Mapping[str, str],
    accepted_heads: Collection[str],
) -> list[str]:
    """Say what differs from what a run recorded, the only thing a resume can trust.

    `seal` is compared only once there is one: before sealing nothing is anchored to it.
    """
    problems: list[str] = []
    if anchor.get("head") and observed.get("head") not in accepted_heads:
        problems.append(
            f"HEAD está en {observed.get('head', '')[:12]} y la ejecución lo dejó en "
            f"{anchor['head'][:12]}"
        )
    if anchor.get("seal") and observed.get("seal") != anchor["seal"]:
        problems.append(".lictor/seal.json no es el que escribió la ejecución")
    return problems


def precondition_exit_code(problems: Sequence[PreconditionProblem]) -> int:
    """Exit 10 if the project has to change, 40 if only the machine has to.

    The project comes first whatever the order of the checks: fixing the machine alone would
    still not let the command start.
    """
    if not problems:
        return int(ExitCode.OK)
    if all(problem.kind == "environment" for problem in problems):
        return int(ExitCode.ENVIRONMENT_UNAVAILABLE)
    return int(ExitCode.PRECONDITION)


def missing_executables(
    commands: Mapping[str, str], found: Callable[[str], bool]
) -> list[PreconditionProblem]:
    """Say which commands can't start, by their first word, as `found` resolves it.

    Each label completes "el comando …", as in "del control 'lint'".

    One under `{env_dir}` only exists once the environment is built, which checks it by running
    its own questions, so it is left alone here.
    """
    problems: list[PreconditionProblem] = []
    for label, command in commands.items():
        words = shlex.split(command)
        if not words:
            problems.append(PreconditionProblem("config", f"El comando {label} está vacío"))
            continue
        program = words[0]
        if _ENV_PLACEHOLDER in program or found(program):
            continue
        problems.append(
            PreconditionProblem(
                "environment",
                f"No se encuentra '{program}', con el que empieza el comando {label} "
                f"('{command}'). Instálalo o corrige el comando en lictor.json.",
            )
        )
    return problems


def runs_to_prune(run_ids: Collection[str], keep: int, protected: Collection[str]) -> list[str]:
    """Return the runs past the `keep` most recent, oldest first, sparing the protected ones.

    Only names with the shape of a run id count: anything else under `runs/` isn't a run's.
    """
    runs = sorted((rid for rid in run_ids if RUN_ID.fullmatch(rid)), reverse=True)
    return sorted(rid for rid in runs[max(keep, 0) :] if rid not in protected)


def score_meets(score: Score, minimum: float | None) -> bool:
    """Tell whether a score reaches the minimum its control declares, if any."""
    return minimum is None or score.percent >= minimum


def registration_problems(
    requested: Sequence[str], classes: Mapping[str, ScenarioClass]
) -> list[str]:
    """Say why some of the scenarios a person asks to register can't be: all or nothing."""
    if not requested:
        return ["Indica al menos un escenario que registrar."]
    problems: list[str] = []
    for sid in dict.fromkeys(requested):
        cls = classes.get(sid)
        if cls is None:
            problems.append(f"No existe el escenario {sid} en features/ de HEAD.")
        elif cls == "green":
            problems.append(f"{sid} ya está en verde para su especificación actual.")
    return problems


def enforce_min_score(result: ControlResult, minimum: float | None) -> ControlResult:
    """Fail a control whose score is below the minimum it declares.

    A result that already failed is left as it is: a run that crashed says so itself. One that
    passed with no score measured nothing (a coverage run with no tests), which no minimum meets.
    """
    if minimum is None or not result.ok:
        return result
    floor = f"{minimum:g}".replace(".", ",")
    if result.score is None:
        error = f"El control no midió nada y declara un mínimo de {floor} %"
        return replace(result, ok=False, error=error)
    if score_meets(result.score, minimum):
        return result
    what = (
        "La cobertura" if isinstance(result.score, CoverageScore) else "La puntuación de mutación"
    )
    error = f"{what}, {describe_score(result.score)}, no llega al mínimo de {floor} %"
    return replace(result, ok=False, error=error)
