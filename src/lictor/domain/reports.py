"""What a report says, as facts, and whether its counts are possible (ADR-0009).

A reader turns a file into one of these and decides nothing: the verdict is a rule of the domain
(`verdicts.py`). A report whose counts can't be true is not a report, so building one checks them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from lictor.domain.model import CoverageScore, MutationScore, TestCaseResult
from lictor.domain.rules import coverage_score, mutation_score

ReportFormat = Literal[
    "exit-code", "junit", "coverage", "mutation", "complexity", "duplication", "api"
]


@dataclass(frozen=True)
class MissingReport:
    """Nothing was left where the report should be."""


@dataclass(frozen=True)
class InvalidReport:
    """Something was left where the report should be, and it can't be read as one."""

    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("Un informe no válido necesita un motivo")


@dataclass(frozen=True)
class JUnitReport:
    """The test cases a JUnit report lists; none is a run with no tests, not a missing report."""

    cases: tuple[TestCaseResult, ...] = ()


@dataclass(frozen=True)
class CoverageReport:
    """The totals coverage.py exported: lines and branches, run and in all."""

    covered_lines: int
    num_statements: int
    covered_branches: int = 0
    num_branches: int = 0

    @property
    def measured(self) -> bool:
        """Whether there was anything to measure: with no statement and no branch, there wasn't."""
        return self.num_statements + self.num_branches > 0

    @property
    def score(self) -> CoverageScore:
        return coverage_score(
            {
                "covered_lines": self.covered_lines,
                "num_statements": self.num_statements,
                "covered_branches": self.covered_branches,
                "num_branches": self.num_branches,
            }
        )


@dataclass(frozen=True)
class MutationReport:
    """The mutants of a pass by outcome, as the mutation tool exported them."""

    counts: Mapping[str, int]

    @property
    def tried(self) -> int:
        """The mutants the tests were run against: all of them but the skipped ones."""
        return self.counts.get("total", 0) - self.counts.get("skipped", 0)

    @property
    def score(self) -> MutationScore:
        return mutation_score(self.counts)


@dataclass(frozen=True)
class FunctionComplexity:
    """One function and its complexity, where the code under test has it (ADR-0013)."""

    file: str  # relative to the tree that was measured
    function: str  # qualified by its class or its enclosing function: `Clase.metodo`
    line: int
    complexity: int
    last_line: int  # where it ends: what a change of the scenario touches of it (F84)


@dataclass(frozen=True)
class ComplexityReport:
    """Every function the complexity command measured, with its value: facts, no limit."""

    functions: tuple[FunctionComplexity, ...] = ()


@dataclass(frozen=True)
class CloneOccurrence:
    """One place a clone appears (ADR-0014): its file, its lines, and its run of tokens.

    `start` and `end` count the tokens of the file that count, `[start, end)`.
    """

    file: str  # relative to the tree that was measured
    first_line: int
    last_line: int
    start: int
    end: int

    @property
    def tokens(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Clone:
    """A family of places with the same code: every place it appears, in file order."""

    occurrences: tuple[CloneOccurrence, ...]

    @property
    def tokens(self) -> int:
        """The tokens of its longest place: how large the clone is."""
        return max(o.tokens for o in self.occurrences)


@dataclass(frozen=True)
class CodeFile:
    """How many tokens of a file count, and which runs of them are in a clone, merged."""

    file: str
    tokens: int
    covered: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class DuplicationReport:
    """Every file the duplication command read and every clone it found, largest first."""

    min_tokens: int
    files: tuple[CodeFile, ...] = ()
    clones: tuple[Clone, ...] = ()

    @property
    def in_clones(self) -> int:
        """The tokens in a clone, every copy counted: what a refactor may not raise."""
        return sum(end - start for f in self.files for start, end in f.covered)

    @property
    def code_tokens(self) -> int:
        return sum(f.tokens for f in self.files)


ApiKind = Literal[
    "module",
    "function",
    "class",
    "method",
    "staticmethod",
    "classmethod",
    "property",
    "constant",
    "attribute",
    "field",
    "member",
    "external",
    "unresolved",
]
ParamKind = Literal[
    "positional", "positional_or_keyword", "keyword", "var_positional", "var_keyword"
]


@dataclass(frozen=True)
class ApiParam:
    """One parameter of a public callable: its name, its kind and whether it has a default."""

    name: str
    kind: ParamKind
    default: bool = False


@dataclass(frozen=True)
class ApiSymbol:
    """One public name of the code under test and what it is (ADR-0017).

    `origin` is where it is defined, by module and qualified name: two public names with one
    origin are one object reached two ways. `params` is None when it isn't called, or when
    something outside the project decides how (a class whose base is a library's).
    """

    name: str
    kind: ApiKind
    origin: str
    file: str  # relative to the tree that was measured
    line: int
    is_async: bool = False
    params: tuple[ApiParam, ...] | None = None


@dataclass(frozen=True)
class ApiReport:
    """Every public name the API command found, in order of name: facts, no base."""

    symbols: tuple[ApiSymbol, ...] = ()


BreakKind = Literal[
    "removed",
    "kind",
    "async",
    "param_removed",
    "param_kind",
    "param_moved",
    "param_default",
    "param_new",
]


@dataclass(frozen=True)
class ApiBreak:
    """One way a public name of the base no longer works as it did (ADR-0017).

    Two breaks are the same when they name the same thing of the same name: `detail` only says it
    in words, and `aliases` are the other public names of the same object that break alike.
    """

    name: str
    change: BreakKind
    param: str = ""
    detail: str = field(default="", compare=False)
    aliases: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class ApiComparison:
    """A report of the API compared with the one of the run's base commit (ADR-0017).

    `broken` are the breaks the person hasn't accepted and `accepted` the ones `api.accepted`
    names; `problem` says why there is no comparison, when the base couldn't be measured.
    """

    report: ApiReport
    base: str  # the base commit
    broken: tuple[ApiBreak, ...] = ()
    accepted: tuple[ApiBreak, ...] = ()
    problem: str | None = None

    @property
    def breaks(self) -> frozenset[ApiBreak]:
        """Every break, accepted or not: what a refactor may not add to (ADR-0017)."""
        return frozenset((*self.broken, *self.accepted))


type ReportOf[R] = R | MissingReport | InvalidReport
type Report = (
    JUnitReport
    | CoverageReport
    | MutationReport
    | ComplexityReport
    | DuplicationReport
    | ApiReport
    | ApiComparison
    | MissingReport
    | InvalidReport
)

_COVERAGE_TOTALS = ("covered_lines", "num_statements", "covered_branches", "num_branches")
_COVERAGE_REQUIRED = ("covered_lines", "num_statements")
MUTATION_OUTCOMES = ("killed", "survived", "timeout", "no_tests", "suspicious", "segfault")
_MUTATION_COUNTS = (*MUTATION_OUTCOMES, "skipped", "total")


def _count(value: object) -> int | None:
    """A count is a non-negative integer; a bool is not one, though Python says it is an int."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def coverage_report(data: object) -> CoverageReport | InvalidReport:
    """The totals of a coverage export, or why they can't be the totals of a real run."""
    totals = data.get("totals") if isinstance(data, dict) else None
    if not isinstance(totals, dict):
        return InvalidReport("no tiene 'totals'")
    for name in _COVERAGE_REQUIRED:
        if name not in totals:
            return InvalidReport(f"no tiene '{name}' en 'totals'")
    counts: dict[str, int] = {}
    for name in _COVERAGE_TOTALS:
        value = _count(totals.get(name, 0))
        if value is None:
            return InvalidReport(f"'{name}' no es un número: {totals.get(name)!r}")
        counts[name] = value
    pairs = (("covered_lines", "num_statements"), ("covered_branches", "num_branches"))
    for covered, total in pairs:
        if counts[covered] > counts[total]:
            return InvalidReport(
                f"'{covered}' ({counts[covered]}) es mayor que '{total}' ({counts[total]})"
            )
    return CoverageReport(**counts)


def mutation_report(data: object) -> MutationReport | InvalidReport:
    """The counts of a mutation export, or why they can't be the counts of a real pass."""
    if not isinstance(data, dict):
        return InvalidReport("no es un objeto JSON")
    counts: dict[str, int] = {}
    for name in _MUTATION_COUNTS:
        value = _count(data.get(name, 0))
        if value is None:
            return InvalidReport(f"'{name}' no es un número de mutantes: {data.get(name)!r}")
        counts[name] = value
    total = counts["total"]
    for name in (*MUTATION_OUTCOMES, "skipped"):
        if counts[name] > total:
            return InvalidReport(f"'{name}' ({counts[name]}) es mayor que 'total' ({total})")
    summed = sum(counts[name] for name in (*MUTATION_OUTCOMES, "skipped"))
    if summed > total:
        return InvalidReport(f"los mutantes por resultado ({summed}) son más que 'total' ({total})")
    return MutationReport(counts)


def _function_complexity(index: int, raw: object) -> FunctionComplexity | InvalidReport:
    if not isinstance(raw, dict):
        return InvalidReport(f"la función {index} no es un objeto")
    where = _function_place(index, raw)
    if isinstance(where, InvalidReport):
        return where
    line, value = _count(raw.get("line")), _count(raw.get("complexity"))
    if line is None or line < 1:
        return InvalidReport(f"la línea de la función {index} no es válida: {raw.get('line')!r}")
    if value is None or value < 1:
        return InvalidReport(
            f"la complejidad de la función {index} no es válida: {raw.get('complexity')!r}"
        )
    last = _count(raw.get("end_line"))
    if last is None or last < line:
        return InvalidReport(
            f"el final de la función {index} no es válido: {raw.get('end_line')!r}"
        )
    return FunctionComplexity(where[0], where[1], line, value, last)


def _function_place(index: int, raw: dict[str, object]) -> tuple[str, str] | InvalidReport:
    """The file and the name of a function, or why they can't be where the tree has code."""
    file, name = raw.get("file"), raw.get("function")
    if not isinstance(file, str) or not file or not isinstance(name, str) or not name:
        return InvalidReport(f"la función {index} no tiene 'file' y 'function'")
    path = PurePosixPath(file)
    if path.is_absolute() or ".." in path.parts:
        return InvalidReport(f"la función {index} está fuera del árbol medido: {file!r}")
    return path.as_posix(), name


def complexity_report(data: object) -> ComplexityReport | InvalidReport:
    """The functions of a complexity export, or why they can't be what a real command measured.

    Every function has at least one path, so a complexity below 1 is no measure; a file outside
    the measured tree names code nobody verified.
    """
    functions = data.get("functions") if isinstance(data, dict) else None
    if not isinstance(functions, list):
        return InvalidReport("no tiene 'functions'")
    found: list[FunctionComplexity] = []
    for index, raw in enumerate(functions, start=1):
        function = _function_complexity(index, raw)
        if isinstance(function, InvalidReport):
            return function
        found.append(function)
    return ComplexityReport(tuple(found))


def _relative(file: object) -> str | None:
    """A path inside the measured tree, or None if it is empty, absolute or leaves it."""
    if not isinstance(file, str) or not file:
        return None
    path = PurePosixPath(file)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


_SPAN = 2  # a run of tokens is [start, end]
_PLACES = 2  # a clone appears in two places at least


def _spans(raw: object) -> list[tuple[int, int]] | None:
    if not isinstance(raw, list):
        return None
    found: list[tuple[int, int]] = []
    for span in raw:
        if not isinstance(span, list) or len(span) != _SPAN:
            return None
        start, end = _count(span[0]), _count(span[1])
        if start is None or end is None:
            return None
        found.append((start, end))
    return found


def _code_file(index: int, raw: object) -> CodeFile | InvalidReport:
    if not isinstance(raw, dict):
        return InvalidReport(f"el fichero {index} no es un objeto")
    file, tokens = _relative(raw.get("file")), _count(raw.get("tokens"))
    if file is None:
        return InvalidReport(f"el fichero {index} está fuera del árbol medido: {raw.get('file')!r}")
    if tokens is None:
        return InvalidReport(f"los tokens de {file} no son un número: {raw.get('tokens')!r}")
    covered = _spans(raw.get("covered"))
    if covered is None:
        return InvalidReport(f"los tramos en clones de {file} no son válidos")
    return CodeFile(file, tokens, tuple(covered))


def _occurrence(
    where: str, raw: object, files: Mapping[str, CodeFile], min_tokens: int
) -> CloneOccurrence | InvalidReport:
    if not isinstance(raw, dict):
        return InvalidReport(f"{where}: una aparición no es un objeto")
    file = _relative(raw.get("file"))
    if file is None or file not in files:
        return InvalidReport(
            f"{where}: una aparición está en un fichero que no lista: {raw.get('file')!r}"
        )
    lines = _lines(raw)
    if lines is None:
        return InvalidReport(f"{where}: las líneas de una aparición en {file} no son válidas")
    run = _run(raw, files[file].tokens)
    if run is None:
        return InvalidReport(f"{where}: los tokens de una aparición en {file} no son válidos")
    start, end = run
    if end - start < min_tokens:
        return InvalidReport(
            f"{where}: una aparición en {file} tiene {end - start} tokens, menos que el mínimo "
            f"de {min_tokens}"
        )
    return CloneOccurrence(file, lines[0], lines[1], start, end)


def _lines(raw: dict[str, object]) -> tuple[int, int] | None:
    """The first and last line of a place: from 1, and the last not before the first."""
    first, last = _count(raw.get("first_line")), _count(raw.get("last_line"))
    if first is None or last is None or not 1 <= first <= last:
        return None
    return first, last


def _run(raw: dict[str, object], tokens: int) -> tuple[int, int] | None:
    """The run of tokens of a place: not empty, and inside its file's `tokens`."""
    start, end = _count(raw.get("start")), _count(raw.get("end"))
    if start is None or end is None or not start < end <= tokens:
        return None
    return start, end


def _clone(
    index: int, raw: object, files: Mapping[str, CodeFile], min_tokens: int
) -> Clone | InvalidReport:
    where = f"el clon {index}"
    occurrences = raw.get("occurrences") if isinstance(raw, dict) else None
    if not isinstance(occurrences, list) or len(occurrences) < _PLACES:
        return InvalidReport(f"{where} no tiene al menos dos apariciones")
    found: list[CloneOccurrence] = []
    for item in occurrences:
        occurrence = _occurrence(where, item, files, min_tokens)
        if isinstance(occurrence, InvalidReport):
            return occurrence
        if any(_overlap(other, occurrence) for other in found):
            return InvalidReport(
                f"{where} tiene dos apariciones que se solapan en {occurrence.file}"
            )
        found.append(occurrence)
    return Clone(tuple(found))


def _overlap(a: CloneOccurrence, b: CloneOccurrence) -> bool:
    return a.file == b.file and a.start < b.end and b.start < a.end


def merged_runs(spans: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Runs of tokens merged where they overlap or touch, in order."""
    runs: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if runs and start <= runs[-1][1]:
            runs[-1] = (runs[-1][0], max(runs[-1][1], end))
        else:
            runs.append((start, end))
    return tuple(runs)


def _files(raw_files: list[object]) -> dict[str, CodeFile] | InvalidReport:
    files: dict[str, CodeFile] = {}
    for index, raw in enumerate(raw_files, start=1):
        code_file = _code_file(index, raw)
        if isinstance(code_file, InvalidReport):
            return code_file
        if code_file.file in files:
            return InvalidReport(f"el fichero {code_file.file} está dos veces")
        files[code_file.file] = code_file
    return files


def _clones(
    raw_clones: list[object], files: Mapping[str, CodeFile], min_tokens: int
) -> tuple[Clone, ...] | InvalidReport:
    clones: list[Clone] = []
    for index, raw in enumerate(raw_clones, start=1):
        clone = _clone(index, raw, files, min_tokens)
        if isinstance(clone, InvalidReport):
            return clone
        clones.append(clone)
    by_file: dict[str, list[tuple[int, int]]] = {}
    for clone in clones:
        for o in clone.occurrences:
            by_file.setdefault(o.file, []).append((o.start, o.end))
    for code_file in files.values():
        if code_file.covered != merged_runs(by_file.get(code_file.file, [])):
            return InvalidReport(
                f"los tramos en clones de {code_file.file} no son los de sus clones"
            )
    return tuple(clones)


def duplication_report(data: object) -> DuplicationReport | InvalidReport:
    """The files and clones of a duplication export, or why they can't be what a command found.

    What the files say is in a clone must be exactly where the clones are, merged: the count the
    refactor may not raise is that sum (ADR-0014), and it can't mean anything else.
    """
    if not isinstance(data, dict):
        return InvalidReport("no es un objeto JSON")
    min_tokens = _count(data.get("min_tokens"))
    if min_tokens is None or min_tokens < 1:
        return InvalidReport(f"'min_tokens' no es válido: {data.get('min_tokens')!r}")
    raw_files, raw_clones = data.get("files"), data.get("families")
    if not isinstance(raw_files, list) or not isinstance(raw_clones, list):
        return InvalidReport("no tiene 'files' y 'families'")
    files = _files(raw_files)
    if isinstance(files, InvalidReport):
        return files
    clones = _clones(raw_clones, files, min_tokens)
    if isinstance(clones, InvalidReport):
        return clones
    return DuplicationReport(min_tokens, tuple(files.values()), clones)
