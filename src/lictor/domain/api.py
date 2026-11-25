"""What breaks in the public API, against one base: the commit a run started from (ADR-0017).

The report of each tree only says which names are public and what each one is; what breaks is
decided here, by comparing the report of a candidate with the one of its base. A break is a name
of the base a user can no longer use as before: it is no longer reachable, it is something else
(a function that became a property, a `def` that became `async def`), or a call that worked no
longer does (a parameter removed, renamed, moved, made keyword-only, left without its default,
or a new one without a default). Moving a definition and re-exporting it, or moving a method up
to a base class, keeps every name, so it breaks nothing.

Which break a scenario asks for can't be told apart: the person accepts it in `api.accepted`.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, get_args

from lictor.domain.reports import (
    ApiBreak,
    ApiComparison,
    ApiKind,
    ApiParam,
    ApiReport,
    ApiSymbol,
    BreakKind,
    InvalidReport,
    ParamKind,
    ReportOf,
)

# How many breaks a message names before it only counts the rest.
NAMED: Final[int] = 20
SHORT_SHA: Final[int] = 12

# Each kind by its text, as a report or a record writes it.
_KINDS: Final[Mapping[object, ApiKind]] = {kind: kind for kind in get_args(ApiKind)}
_PARAM_KINDS: Final[Mapping[object, ParamKind]] = {kind: kind for kind in get_args(ParamKind)}
_CHANGES: Final[Mapping[object, BreakKind]] = {kind: kind for kind in get_args(BreakKind)}
_VARIADIC: Final[frozenset[ParamKind]] = frozenset({"var_positional", "var_keyword"})
_POSITIONAL: Final[frozenset[ParamKind]] = frozenset({"positional", "positional_or_keyword"})
# How a caller can pass each kind of parameter: by position, by name, or both.
_PASSED: Final[Mapping[ParamKind, frozenset[str]]] = {
    "positional": frozenset({"position"}),
    "positional_or_keyword": frozenset({"position", "name"}),
    "keyword": frozenset({"name"}),
    "var_positional": frozenset({"position"}),
    "var_keyword": frozenset({"name"}),
}
# What a name of a class or a module holds: the names under it go with it.
_CONTAINERS: Final[frozenset[str]] = frozenset({"class", "module"})

KIND_WORDS: Final[Mapping[ApiKind, str]] = {
    "module": "módulo",
    "function": "función",
    "class": "clase",
    "method": "método",
    "staticmethod": "método estático",
    "classmethod": "método de clase",
    "property": "propiedad",
    "constant": "constante",
    "attribute": "atributo",
    "field": "campo",
    "member": "miembro",
    "external": "nombre de fuera del proyecto",
    "unresolved": "nombre sin definición",
}
_PARAM_WORDS: Final[Mapping[ParamKind, str]] = {
    "positional": "solo posicional",
    "positional_or_keyword": "posicional o por nombre",
    "keyword": "solo por nombre",
    "var_positional": "*args",
    "var_keyword": "**kwargs",
}

IMPLEMENTER_API: Final[str] = (
    "- No quites ni cambies de forma incompatible la API pública que ya existía; lo que rompas "
    "sale en el informe."
)
REFACTOR_API: Final[str] = (
    "API pública: la verificación descarta el refactor si rompe algo de la API pública que el "
    "verde no rompía (quitar un nombre, cambiar un parámetro o la clase de algo). Mover una "
    "definición y reexportarla con el mismo nombre, o subir un método a una clase base, no la "
    "rompe."
)
_CONFLICT: Final[str] = (
    "Si el escenario no se puede cumplir sin romperla, declara `LICTOR: SPEC_CONFLICT`. Una "
    "rotura que la persona acepta se apunta en `api.accepted` de lictor.json."
)


@dataclass(frozen=True)
class ApiBase:
    """The base the API is compared with: its commit, its report or why there is none, and what
    the person accepts to break (`api.accepted`)."""

    sha: str
    found: ApiReport | str
    accepted: tuple[str, ...] = ()


# Reading a report.


def _param(raw: object) -> ApiParam | None:
    if not isinstance(raw, dict):
        return None
    name, kind, default = raw.get("name"), _PARAM_KINDS.get(raw.get("kind")), raw.get("default")
    if not isinstance(name, str) or not name or kind is None:
        return None
    if not isinstance(default, bool):
        return None
    return ApiParam(name, kind, default)


def _params(raw: object) -> tuple[ApiParam, ...] | InvalidReport | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return InvalidReport("unos parámetros que no son una lista")
    found: list[ApiParam] = []
    for index, item in enumerate(raw, start=1):
        param = _param(item)
        if param is None:
            return InvalidReport(f"un parámetro {index} que no es válido: {item!r}")
        found.append(param)
    return tuple(found)


def _place(raw: Mapping[str, object]) -> tuple[str, int] | None:
    file, line = raw.get("file"), raw.get("line")
    if not isinstance(file, str) or not file or file.startswith("/") or ".." in file.split("/"):
        return None
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        return None
    return file, line


def _symbol(index: int, raw: object) -> ApiSymbol | InvalidReport:
    where = f"el nombre {index}"
    if not isinstance(raw, dict):
        return InvalidReport(f"{where} no es un objeto")
    name, kind, origin = raw.get("name"), _KINDS.get(raw.get("kind")), raw.get("origin")
    if not isinstance(name, str) or not name or not isinstance(origin, str) or not origin:
        return InvalidReport(f"{where} no tiene 'name' y 'origin'")
    if kind is None:
        return InvalidReport(f"{where} ({name}) tiene una clase desconocida: {raw.get('kind')!r}")
    facts = _facts(raw)
    if isinstance(facts, str):
        return InvalidReport(f"{where} ({name}) {facts}")
    (file, line), is_async, params = facts
    return ApiSymbol(name, kind, origin, file, line, is_async, params)


def _facts(
    raw: Mapping[str, object],
) -> tuple[tuple[str, int], bool, tuple[ApiParam, ...] | None] | str:
    """Where a name is defined, whether it is async and its parameters, or what is wrong."""
    place = _place(raw)
    if place is None:
        return "está fuera del árbol medido"
    is_async = raw.get("async", False)
    if not isinstance(is_async, bool):
        return "tiene un 'async' que no es booleano"
    params = _params(raw.get("params"))
    if isinstance(params, InvalidReport):
        return f"tiene {params.reason}"
    return place, is_async, params


def api_report(data: object) -> ApiReport | InvalidReport:
    """The public names of an API export, or why they can't be what a real command found."""
    symbols = data.get("symbols") if isinstance(data, dict) else None
    if not isinstance(symbols, list):
        return InvalidReport("no tiene 'symbols'")
    found: list[ApiSymbol] = []
    for item in [_symbol(index, raw) for index, raw in enumerate(symbols, start=1)]:
        if isinstance(item, InvalidReport):
            return item
        found.append(item)
    return _once_each(tuple(found))


def _once_each(symbols: tuple[ApiSymbol, ...]) -> ApiReport | InvalidReport:
    """A report names each public name once: two of one name can't both be what it is."""
    twice = [name for name, count in Counter(s.name for s in symbols).items() if count > 1]
    return InvalidReport(f"el nombre {twice[0]} está dos veces") if twice else ApiReport(symbols)


# What breaks.


def _goes_with_its_parent(
    name: str, before: Mapping[str, ApiSymbol], now: Mapping[str, ApiSymbol]
) -> bool:
    """Whether what holds a name is already a break: gone, or no longer a class or a module.

    Its parent is enough: if what holds the parent went, the parent went too.
    """
    parent = name.rpartition(".")[0]
    if parent not in before:
        return False
    current = now.get(parent)
    return current is None or (
        before[parent].kind in _CONTAINERS and current.kind not in _CONTAINERS
    )


def _kind_break(before: ApiSymbol, after: ApiSymbol) -> ApiBreak:
    detail = f"pasa de {KIND_WORDS[before.kind]} a {KIND_WORDS[after.kind]}"
    return ApiBreak(before.name, "kind", detail=detail)


def _changes(before: ApiSymbol, after: ApiSymbol) -> list[ApiBreak]:
    if before.kind != after.kind:
        return [_kind_break(before, after)]
    found: list[ApiBreak] = []
    if before.is_async != after.is_async:
        detail = "pasa a async" if after.is_async else "deja de ser async"
        found.append(ApiBreak(before.name, "async", detail=detail))
    if before.params is not None and after.params is not None:
        found.extend(param_breaks(before.name, before.params, after.params))
    return found


def _positional(params: Sequence[ApiParam]) -> list[ApiParam]:
    return [p for p in params if p.kind in _POSITIONAL]


def _match(
    param: ApiParam, before: Sequence[ApiParam], after: Sequence[ApiParam]
) -> ApiParam | None:
    """The parameter of `after` that is `param`: by name, or by position if it was positional only,
    whose name no caller can use."""
    named = [p for p in after if p.kind not in _VARIADIC and p.name == param.name]
    if named or param.kind != "positional":
        return named[0] if named else None
    position, positional = _positional(before).index(param), _positional(after)
    there = positional[position] if position < len(positional) else None
    return there if there is not None and there.kind == "positional" else None


def _variadic_break(name: str, param: ApiParam, after: Sequence[ApiParam]) -> list[ApiBreak]:
    """A `*args` or `**kwargs` of the base that the candidate no longer takes."""
    if any(p.kind == param.kind for p in after):
        return []
    return [ApiBreak(name, "param_removed", param.name, f"pierde {_PARAM_WORDS[param.kind]}")]


def param_breaks(
    name: str, before: Sequence[ApiParam], after: Sequence[ApiParam]
) -> list[ApiBreak]:
    """What a call that worked against `before` may no longer do against `after`."""
    found: list[ApiBreak] = []
    matched: list[ApiParam] = []
    for param in before:
        if param.kind in _VARIADIC:
            found.extend(_variadic_break(name, param, after))
            continue
        current = _match(param, before, after)
        if current is None:
            detail = f"pierde el parámetro {param.name}"
            found.append(ApiBreak(name, "param_removed", param.name, detail))
            continue
        matched.append(current)
        found.extend(_param_changes(name, (param, current), (before, after)))
    found.extend(
        ApiBreak(name, "param_new", p.name, f"parámetro nuevo {p.name} sin valor por defecto")
        for p in after
        if p not in matched and p.kind not in _VARIADIC and not p.default
    )
    return found


def _param_changes(
    name: str,
    pair: tuple[ApiParam, ApiParam],
    signatures: tuple[Sequence[ApiParam], Sequence[ApiParam]],
) -> list[ApiBreak]:
    """How one parameter of the base changed in the candidate, given both signatures."""
    before, after = pair
    found: list[ApiBreak] = []
    if not _PASSED[before.kind] <= _PASSED[after.kind]:
        detail = f"el parámetro {before.name} pasa a ser {_PARAM_WORDS[after.kind]}"
        found.append(ApiBreak(name, "param_kind", before.name, detail))
    elif before.kind in _POSITIONAL and after.kind in _POSITIONAL:
        was = _positional(signatures[0]).index(before)
        now = _positional(signatures[1]).index(after)
        if was != now:
            detail = f"el parámetro {before.name} pasa de la posición {was + 1} a la {now + 1}"
            found.append(ApiBreak(name, "param_moved", before.name, detail))
    if before.default and not after.default:
        detail = f"el parámetro {before.name} pierde su valor por defecto"
        found.append(ApiBreak(name, "param_default", before.name, detail))
    return found


def _canonical(names: Sequence[str]) -> str:
    """The name a break is told by: the shortest way to reach the object, then the first."""
    return min(names, key=lambda n: (n.count("."), n))


def all_breaks(base: ApiReport, candidate: ApiReport) -> tuple[ApiBreak, ...]:
    """Every break of the base's API in the candidate's, each object once, in order of name.

    A name gone with what holds it isn't told again: its class or its module is. Two public
    names of one object that break alike are one break, told by its shortest name.
    """
    before = {s.name: s for s in base.symbols}
    now = {s.name: s for s in candidate.symbols}
    grouped: dict[tuple[str, BreakKind, str, str], list[str]] = {}
    for symbol in base.symbols:
        if _goes_with_its_parent(symbol.name, before, now):
            continue
        current = now.get(symbol.name)
        changes = (
            [ApiBreak(symbol.name, "removed", detail="ya no existe")]
            if current is None
            else _changes(symbol, current)
        )
        for item in changes:
            key = (symbol.origin, item.change, item.param, item.detail)
            grouped.setdefault(key, []).append(item.name)
    found: list[ApiBreak] = []
    for (_, change, param, detail), names in grouped.items():
        name = _canonical(names)
        aliases = tuple(sorted(n for n in names if n != name))
        found.append(ApiBreak(name, change, param, detail, aliases))
    return tuple(sorted(found, key=lambda b: (b.name, b.change, b.param)))


def is_accepted(item: ApiBreak, accepted: Sequence[str]) -> bool:
    """Whether the person accepts a break: `api.accepted` names it, or what holds it."""
    return any(
        name == chosen or name.startswith(f"{chosen}.")
        for name in (item.name, *item.aliases)
        for chosen in accepted
    )


def api_breaks(
    base: ApiReport, candidate: ApiReport, accepted: Sequence[str] = ()
) -> frozenset[ApiBreak]:
    """The breaks of the candidate against the base that the person hasn't accepted."""
    return frozenset(b for b in all_breaks(base, candidate) if not is_accepted(b, accepted))


def compare_api(
    report: ReportOf[ApiReport], base: ApiBase | None
) -> ReportOf[ApiReport] | ApiComparison:
    """The candidate's report compared with its base, or the report alone if there is no base or
    nothing to compare."""
    if base is None or not isinstance(report, ApiReport):
        return report
    if isinstance(base.found, str):
        problem = f"No se pudo medir la API de la base {base.sha[:SHORT_SHA]}: {base.found}"
        return ApiComparison(report, base.sha, problem=problem)
    breaks = all_breaks(base.found, report)
    broken = tuple(b for b in breaks if not is_accepted(b, base.accepted))
    taken = tuple(b for b in breaks if is_accepted(b, base.accepted))
    return ApiComparison(report, base.sha, broken, taken)


# What is said of it.


def break_line(item: ApiBreak) -> str:
    """One break as a message names it."""
    return f"`{item.name}`: {item.detail}"


def named_breaks(breaks: Sequence[ApiBreak]) -> list[str]:
    """The lines of a list of breaks, the first `NAMED` by name and the rest counted."""
    lines = [f"- {break_line(b)}" for b in breaks[:NAMED]]
    if len(breaks) > NAMED:
        lines.append(f"- y {len(breaks) - NAMED} más")
    return lines


def broken_reason(comparison: ApiComparison) -> str:
    """Why an API control fails: what of the base's public API breaks, and what to do."""
    count = len(comparison.broken)
    base = comparison.base[:SHORT_SHA]
    head = (
        f"Se rompe un nombre de la API pública de la base ({base}):"
        if count == 1
        else f"Se rompen {count} nombres de la API pública de la base ({base}):"
    )
    return "\n".join([head, *named_breaks(comparison.broken), _CONFLICT])


def describe_api(
    symbols: int, base: str | None, broken: int, accepted: int, problem: str | None = None
) -> str:
    """What an API measure says in one phrase, as `report.md` and `check` show it."""
    names = "1 nombre público" if symbols == 1 else f"{symbols} nombres públicos"
    if base is None:
        return names
    if problem is not None:
        return f"{names}; sin comparar: {problem}"
    against = f"respecto a {base[:SHORT_SHA]}"
    if broken == 0 and accepted == 0:
        return f"{names}; nada roto {against}"
    parts = [f"{broken} rotura{'s' if broken != 1 else ''}"] if broken else []
    if accepted:
        parts.append(f"{accepted} aceptada{'s' if accepted != 1 else ''}")
    return f"{names}; {', '.join(parts)} {against}"


def break_record(item: ApiBreak) -> dict[str, object]:
    """What the journal and the checkpoint keep of a break."""
    return {"name": item.name, "change": item.change, "param": item.param, "detail": item.detail}


def break_from_record(raw: object) -> ApiBreak | None:
    """A break read back, or None if it doesn't follow `break_record`."""
    if not isinstance(raw, Mapping):
        return None
    name, change = raw.get("name"), _CHANGES.get(raw.get("change"))
    param, detail = raw.get("param", ""), raw.get("detail", "")
    if not isinstance(name, str) or change is None:
        return None
    if not isinstance(param, str) or not isinstance(detail, str):
        return None
    return ApiBreak(name, change, param, detail)


def breaks_from_record(raw: object) -> tuple[ApiBreak, ...] | None:
    """A list of breaks read back, or None if any of them doesn't follow `break_record`."""
    if not isinstance(raw, list):
        return None
    found = [break_from_record(item) for item in raw]
    if any(item is None for item in found):
        return None
    return tuple(item for item in found if item is not None)
