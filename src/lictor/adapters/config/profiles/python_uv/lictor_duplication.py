"""The repeated code of a tree, with the one definition of a clone Lictor uses (ADR-0014).

Lictor copies this file into the verification environment, where it runs as the `duplication`
control, and next to each refactor call, where the agent measures with it. It imports nothing of
Lictor, reads the code without importing or running it, reads no configuration and honours no
comment: what it counts is fixed here, and its tests fix each case.

A clone is a run of at least `MIN_TOKENS` Python tokens found in two places, in one file or in
two, with names and literals renamed consistently: within a function, each name or literal of one
place always stands for the same one of the other, and the other way round. Besides:

- comments, blank lines, line breaks inside brackets and indentation don't count, so formatting
  changes nothing;
- imports, function signatures (from `def` to its colon) and docstrings neither count nor cut a
  clone: two equal bodies under different signatures are a clone, two equal signatures are not;
- an f-string is one token, its source text, whatever the version of Python;
- the correspondence of names starts again in each function, so renaming a local variable of one
  function doesn't break a clone with the next one;
- a table neither counts nor joins what is around it: a short unit (fewer than `MIN_TOKENS`
  tokens, with no statement keyword such as `if`, `for` or `return`) repeated one after another
  for `MIN_TOKENS` tokens or more, as the entries of a dict, a list of fields or of arguments;
- a run that repeats a longer unit (a function copied many times) counts as the copies of it;
- a clone never starts at the end of the statement before it, and a run that repeats overlapping
  itself is cut where it starts to overlap.

Places that overlap are one, so the clones come as families: each with every place it appears.

    python -I -B -m lictor_duplication src [--out report.json]

It prints how many tokens are in clones and every family, largest first; with `--out` it also
writes them as JSON, the report the control reads. A file that can't be parsed fails the command
and leaves no report.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import keyword
import sys
import tokenize
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypeVar

MIN_TOKENS = 50
DEFINITION = (
    f"Clon: {MIN_TOKENS} tokens o más que se repiten en dos sitios, con los nombres y los "
    "literales cambiados de forma coherente dentro de cada función; no cuentan los comentarios, "
    "el formato, las importaciones, las firmas ni los docstrings."
)
_LAYOUT = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)
# Absent before 3.12 (f-strings) and 3.14 (t-strings): then a whole string is one STRING token.
_OPEN = frozenset(
    t for t in (getattr(tokenize, n, None) for n in ("FSTRING_START", "TSTRING_START")) if t
)
_CLOSE = frozenset(
    t for t in (getattr(tokenize, n, None) for n in ("FSTRING_END", "TSTRING_END")) if t
)
_RENAMED = frozenset({"ID", "NUM", "STR"})
# A unit with one of these is logic, not data: repeated, it is a clone, not a table.
_STATEMENTS = frozenset(
    {
        "if",
        "elif",
        "else",
        "for",
        "while",
        "try",
        "except",
        "finally",
        "with",
        "return",
        "yield",
        "raise",
        "break",
        "continue",
        "def",
        "class",
        "pass",
        "del",
        "assert",
        "global",
        "nonlocal",
        "async",
    }
)
_END = "\n"
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

Position = tuple[int, int]  # line, column in characters
# A place in a file: a position, or the index of a token.
_At = TypeVar("_At", Position, int)


@dataclass(frozen=True)
class Token:
    kind: str  # what must match: an operator or keyword as written, or ID, NUM, STR
    text: str  # what it says: renamed consistently when kind is ID, NUM or STR
    line: int
    scope: int  # the function it belongs to; 0 outside every function


def _char_column(lines: Sequence[bytes], line: int, byte_column: int) -> int:
    """`ast` gives columns in bytes of UTF-8 and `tokenize` in characters: this is the latter."""
    return len(lines[line - 1][:byte_column].decode("utf-8", "replace"))


def _span(
    lines: Sequence[bytes], start: ast.stmt, end: ast.stmt | None = None
) -> tuple[Position, Position]:
    """Where a statement starts, and where it ends or `end` starts."""
    s_line, s_col = start.lineno, start.col_offset
    if end is None:
        e_line, e_col = start.end_lineno or s_line, start.end_col_offset or 0
    else:
        e_line, e_col = end.lineno, end.col_offset
    return (
        (s_line, _char_column(lines, s_line, s_col)),
        (e_line, _char_column(lines, e_line, e_col)),
    )


def _docstring(node: ast.AST) -> ast.Expr | None:
    body = getattr(node, "body", [])
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return first
    return None


def _layout(
    tree: ast.AST, lines: Sequence[bytes]
) -> tuple[list[tuple[Position, Position]], list[tuple[Position, Position]]]:
    """What doesn't count (imports, signatures, docstrings), and where each function's body is."""
    ignored: list[tuple[Position, Position]] = []
    bodies: list[tuple[Position, Position]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            ignored.append(_span(lines, node))
        if isinstance(node, _FUNCTIONS):
            ignored.append(_span(lines, node, node.body[0]))
            body_start, _ = _span(lines, node.body[0])
            _, function_end = _span(lines, node)
            bodies.append((body_start, function_end))
        if isinstance(node, _DOCUMENTED):
            doc = _docstring(node)
            if doc is not None:
                ignored.append(_span(lines, doc))
    ignored.sort()
    bodies.sort()
    return ignored, bodies


def _kind(tok: tokenize.TokenInfo) -> str:
    if tok.type == tokenize.NAME and not keyword.iskeyword(tok.string):
        return "ID"
    if tok.type == tokenize.NUMBER:
        return "NUM"
    if tok.type == tokenize.STRING:
        return "STR"
    return tok.string


def _source_text(lines: Sequence[str], start: Position, end: Position) -> str:
    if start[0] == end[0]:
        return lines[start[0] - 1][start[1] : end[1]]
    parts = [lines[start[0] - 1][start[1] :]]
    parts.extend(lines[start[0] : end[0] - 1])
    parts.append(lines[end[0] - 1][: end[1]])
    return "".join(parts)


def _raw_tokens(source: bytes) -> Iterator[tuple[str, str, Position, Position]]:
    """(kind, text, start, end) of each token that counts, an f-string as one STR."""
    text_lines = source.decode("utf-8").splitlines(keepends=True)
    depth = 0
    opened: Position = (0, 0)
    for tok in tokenize.tokenize(io.BytesIO(source).readline):
        if tok.type in _OPEN:
            if depth == 0:
                opened = tok.start
            depth += 1
            continue
        if depth:
            if tok.type in _CLOSE:
                depth -= 1
                if depth == 0:
                    yield "STR", _source_text(text_lines, opened, tok.end), opened, tok.end
            continue
        if tok.type in _LAYOUT:
            continue
        if tok.type == tokenize.NEWLINE:
            yield _END, _END, tok.start, tok.end
            continue
        yield _kind(tok), tok.string, tok.start, tok.end


def _merged(spans: Sequence[tuple[_At, _At]]) -> tuple[tuple[_At, _At], ...]:
    """Spans merged where they overlap or touch, in order."""
    merged: list[tuple[_At, _At]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


class _Ignored:
    """Where the imports, signatures and docstrings of a module are, merged."""

    def __init__(self, spans: Sequence[tuple[Position, Position]]) -> None:
        self._spans = _merged(spans)
        self._starts = [start for start, _ in self._spans]

    def __contains__(self, position: Position) -> bool:
        at = bisect_right(self._starts, position) - 1
        return at >= 0 and position < self._spans[at][1]


class _Scopes:
    """The function each position belongs to, asked in the order of the module's tokens.

    Function bodies are numbered from 1 in the order they start; 0 is outside every function.
    """

    def __init__(self, bodies: Sequence[tuple[Position, Position]]) -> None:
        self._pending = list(enumerate(bodies, start=1))[::-1]  # the next one last
        self._open: list[tuple[int, Position]] = []  # (number, end), the innermost last

    def at(self, position: Position) -> int:
        while self._pending and self._pending[-1][1][0] <= position:
            number, (_, end) = self._pending.pop()
            self._close_before(position)
            self._open.append((number, end))
        self._close_before(position)
        return self._open[-1][0] if self._open else 0

    def _close_before(self, position: Position) -> None:
        while self._open and self._open[-1][1] <= position:
            self._open.pop()


def tokens(source: bytes) -> list[Token]:
    """The tokens of a module that count, each with its line and the function it belongs to."""
    tree = ast.parse(source)
    ignored_spans, bodies = _layout(tree, source.splitlines(keepends=True) or [b""])
    ignored, scopes = _Ignored(ignored_spans), _Scopes(bodies)
    found: list[Token] = []
    skipping = False
    for kind, text, start, _ in _raw_tokens(source):
        # The end of an ignored statement (an import) goes with it.
        skipping = start in ignored or (kind == _END and skipping)
        scope = scopes.at(start)
        if not skipping:
            found.append(Token(kind, text, start[0], scope))
    return found


def smallest_period(kinds: Sequence[str]) -> int:
    """The length of the shortest unit whose repetition makes `kinds` (prefix function)."""
    prefix = [0] * len(kinds)
    for i in range(1, len(kinds)):
        k = prefix[i - 1]
        while k and kinds[i] != kinds[k]:
            k = prefix[k - 1]
        if kinds[i] == kinds[k]:
            k += 1
        prefix[i] = k
    return len(kinds) - prefix[-1] if kinds else 0


class _Correspondence:
    """Which name of one run stands for which of the other, from a token on (Baker's p-match)."""

    def __init__(self, a: Sequence[Token], b: Sequence[Token]) -> None:
        self._a, self._b = a, b
        self.since = 0
        self._forward: dict[str, str] = {}
        self._backward: dict[str, str] = {}

    def restart(self, at: int) -> None:
        """Start again at `at`, with what the runs say from there up to now."""
        self.since = at
        self._forward.clear()
        self._backward.clear()

    def takes(self, k: int) -> bool:
        """Whether token `k` keeps the correspondence; if it does, it is part of it."""
        if self._a[k].kind not in _RENAMED:
            return True
        x, y = self._a[k].text, self._b[k].text
        if self._forward.get(x, y) != y or self._backward.get(y, x) != x:
            return False
        self._forward[x], self._backward[y] = y, x
        return True

    def clash(self, k: int) -> int:
        """The token after the earlier use that token `k` clashes with."""
        x, y = self._a[k].text, self._b[k].text
        return 1 + next(
            j
            for j in range(k - 1, self.since - 1, -1)
            if self._a[j].kind in _RENAMED and (self._a[j].text == x or self._b[j].text == y)
        )

    def new_function(self, k: int) -> bool:
        a, b = self._a, self._b
        return k > 0 and (a[k].scope != a[k - 1].scope or b[k].scope != b[k - 1].scope)


def _consistent(a: Sequence[Token], b: Sequence[Token], n: int) -> Iterator[tuple[int, int]]:
    """(offset, length) of each part of two equal-kind runs where the renaming is consistent.

    The correspondence starts again in each function, on either side. On a conflict the part
    starts again right after the earlier use it clashes with.
    """
    names = _Correspondence(a, b)
    start = k = 0
    while k < len(a):
        if names.new_function(k):
            names.restart(k)
        if names.takes(k):
            k += 1
            continue
        if k - start >= n:
            yield start, k - start
        start = max(start, names.clash(k))
        names.restart(max(names.since, start))
        k = names.since  # what the runs say from there is taken again
    if len(a) - start >= n:
        yield start, len(a) - start


def _trimmed(run: Sequence[Token], offset: int, length: int) -> tuple[int, int]:
    """A part without the end of the statement before it."""
    while length and run[offset].kind == _END:
        offset, length = offset + 1, length - 1
    return offset, length


Pair = tuple[str, int, str, int, int]  # file, start, file, start, length


def _tables(kinds: Sequence[str], n: int) -> list[tuple[int, int]]:
    """Where a short unit (fewer than `n` tokens) repeats for `n` tokens or more: a table.

    Each is (start, end) within `kinds`; they may overlap.
    """
    found: list[tuple[int, int]] = []
    for unit in range(1, min(n, len(kinds) // 2 + 1)):
        run = 0
        for i in range(len(kinds) - unit + 1):
            if i + unit < len(kinds) and kinds[i] == kinds[i + unit]:
                run += 1
                continue
            # kinds[i - run : i + unit] repeats a unit of `unit` tokens
            start = i - run
            if (
                run >= unit
                and run + unit >= n
                and not _STATEMENTS & set(kinds[start : start + unit])
            ):
                found.append((start, i + unit))
            run = 0
    return found


def _outside_tables(kinds: Sequence[str], n: int) -> Iterator[tuple[int, int]]:
    """(offset, length) of each part of a run of at least `n` tokens that isn't in a table."""
    tables = sorted(_tables(kinds, n))
    at = 0
    for start, end in [*tables, (len(kinds), len(kinds))]:
        if start - at >= n:
            yield at, start - at
        at = max(at, end)


def _units(
    kinds: Sequence[str], a: int, b: int, length: int, n: int
) -> Iterator[tuple[int, int, int]]:
    """A pair as the copies of its unit, when it repeats one long enough to be a clone itself.

    Two places that each repeat the same function, say, are as many copies of it: that is what the
    family lists, and what the agent removes. A shorter unit (logic unrolled) stays whole.
    """
    unit = smallest_period(kinds[a : a + length])
    if 2 * unit > length or unit < n:
        yield a, b, length
        return
    for k in range(length // unit):
        yield a + k * unit, b + k * unit, unit


def _adjacent_copies(
    file: str, kinds: Sequence[str], start: int, end: int, n: int
) -> Iterator[Pair]:
    """A run that overlaps itself repeats one unit from `start` to `end`: each copy and the next.

    Each copy is a place; one shorter than `n` is no clone, as two places that overlap are one.
    """
    unit = smallest_period(kinds[start:end])
    if unit < n:
        return
    for x in range(start, end - 2 * unit + 1, unit):
        yield file, x, file, x + unit, unit


def _windows(kinds: dict[str, list[str]], n: int) -> dict[tuple[str, ...], list[tuple[str, int]]]:
    """Every run of `n` kinds, with each file and position where it starts."""
    windows: dict[tuple[str, ...], list[tuple[str, int]]] = defaultdict(list)
    for file in sorted(kinds):
        ks = kinds[file]
        for i in range(len(ks) - n + 1):
            windows[tuple(ks[i : i + n])].append((file, i))
    return windows


def _equal_length(ka: Sequence[str], kb: Sequence[str], ia: int, ib: int, n: int) -> int:
    """How far two runs that start equal for `n` kinds go on being equal."""
    length = n
    while ia + length < len(ka) and ib + length < len(kb) and ka[ia + length] == kb[ib + length]:
        length += 1
    return length


def _equal_runs(kinds: dict[str, list[str]], n: int) -> Iterator[Pair]:
    """Every maximal pair of runs of at least `n` equal kinds, from where each starts."""
    for places in _windows(kinds, n).values():
        for x, (fa, ia) in enumerate(places):
            for fb, ib in places[x + 1 :]:
                ka, kb = kinds[fa], kinds[fb]
                if ia and ib and ka[ia - 1] == kb[ib - 1]:
                    continue  # not where an equal run starts
                yield fa, ia, fb, ib, _equal_length(ka, kb, ia, ib, n)


def _clones_of(
    run: Pair, files: dict[str, list[Token]], kinds: dict[str, list[str]], n: int
) -> Iterator[Pair]:
    """The clones in a run of equal kinds: consistent, not tables, copy by copy."""
    fa, ia, fb, ib, length = run
    ka = kinds[fa]
    run_a, run_b = files[fa][ia : ia + length], files[fb][ib : ib + length]
    for found_at, found in _consistent(run_a, run_b, n):
        offset, part = _trimmed(run_a, found_at, found)
        a, b = ia + offset, ib + offset
        if fa == fb and b < a + part:
            yield from _adjacent_copies(fa, ka, a, b + part, n)
            continue
        for off, ln in _outside_tables(ka[a : a + part], n):
            for x, y, unit in _units(ka, a + off, b + off, ln, n):
                yield fa, x, fb, y, unit


def pairs(files: dict[str, list[Token]], n: int = MIN_TOKENS) -> list[Pair]:
    """Every pair of places that are a clone of at least `n` tokens, copy by copy."""
    kinds = {f: [t.kind for t in files[f]] for f in files}
    found = {c for run in _equal_runs(kinds, n) for c in _clones_of(run, files, kinds, n)}
    return sorted(c for c in found if c[4] >= n)


@dataclass
class _Place:
    file: str
    start: int
    end: int


def _places(pairs_found: Sequence[Pair]) -> list[_Place]:
    """The places of the pairs, file by file, those that overlap merged."""
    by_file: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for fa, ia, fb, ib, length in pairs_found:
        by_file[fa].append((ia, ia + length))
        by_file[fb].append((ib, ib + length))
    places: list[_Place] = []
    for file in sorted(by_file):
        first = len(places)
        for start, end in sorted(by_file[file]):
            if len(places) > first and places[-1].end > start:
                places[-1].end = max(places[-1].end, end)
            else:
                places.append(_Place(file, start, end))
    return places


def families(pairs_found: Sequence[Pair]) -> list[list[_Place]]:
    """The places of the pairs, grouped by the pairs that join them: each family a clone."""
    places = _places(pairs_found)
    starts: dict[str, list[int]] = defaultdict(list)
    ids: dict[str, list[int]] = defaultdict(list)
    for i, place in enumerate(places):
        starts[place.file].append(place.start)
        ids[place.file].append(i)

    def place_of(file: str, at: int) -> int:
        return ids[file][bisect_right(starts[file], at) - 1]

    parent = list(range(len(places)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for fa, ia, fb, ib, _ in pairs_found:
        parent[root(place_of(fa, ia))] = root(place_of(fb, ib))
    groups: dict[int, list[_Place]] = defaultdict(list)
    for i, place in enumerate(places):
        groups[root(i)].append(place)
    return [g for g in groups.values() if len(g) > 1]


@dataclass(frozen=True)
class Occurrence:
    """One place a clone appears: its file, lines, and its run of tokens `[start, end)`."""

    file: str
    first_line: int
    last_line: int
    start: int
    end: int


@dataclass(frozen=True)
class FileTokens:
    """How many tokens of a file count, and which runs of them are in a clone, merged."""

    file: str
    tokens: int
    covered: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class Report:
    """What the command reports: every file and every clone, largest first."""

    files: tuple[FileTokens, ...]
    families: tuple[tuple[Occurrence, ...], ...]

    @property
    def in_clones(self) -> int:
        """Tokens in a clone, every copy counted: what the agent compares before and after."""
        return sum(end - start for f in self.files for start, end in f.covered)

    @property
    def code_tokens(self) -> int:
        return sum(f.tokens for f in self.files)

    def as_json(self) -> dict[str, object]:
        return {
            "min_tokens": MIN_TOKENS,
            "files": [
                {"file": f.file, "tokens": f.tokens, "covered": [list(c) for c in f.covered]}
                for f in self.files
            ],
            "families": [{"occurrences": [asdict(o) for o in family]} for family in self.families],
        }


def measure(roots: Sequence[str]) -> Report:
    """The report of the trees under the roots; a file that doesn't parse raises."""
    sources: dict[str, bytes] = {}
    for root in roots:
        base = Path(root)
        for path in [base] if base.is_file() else sorted(base.rglob("*.py")):
            sources[path.as_posix()] = path.read_bytes()
    return report_of(sources)


def _size(family: Sequence[Occurrence]) -> int:
    return max(o.end - o.start for o in family)


def report_of(sources: dict[str, bytes]) -> Report:
    """The report of some modules, by the path each is named by."""
    files = {name: tokens(source) for name, source in sources.items()}
    found: list[tuple[Occurrence, ...]] = []
    covered: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for group in families(pairs(files)):
        occurrences = []
        for place in sorted(group, key=lambda p: (p.file, p.start)):
            run = files[place.file][place.start : place.end]
            occurrences.append(
                Occurrence(place.file, run[0].line, run[-1].line, place.start, place.end)
            )
            covered[place.file].append((place.start, place.end))
        found.append(tuple(occurrences))
    found.sort(key=lambda f: (-_size(f), f[0].file, f[0].start))
    return Report(
        tuple(FileTokens(f, len(files[f]), _merged(covered[f])) for f in sorted(files)),
        tuple(found),
    )


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="lictor_duplication", description=DEFINITION)
    parser.add_argument("roots", nargs="+", metavar="ruta")
    parser.add_argument("--out", metavar="informe.json")
    args = parser.parse_args(argv)  # a usage error exits 2, as one that doesn't parse
    try:
        report = measure(args.roots)
    except (SyntaxError, ValueError, OSError, UnicodeDecodeError) as exc:
        print(f"No se pudo analizar: {exc}", file=sys.stderr)
        return 2
    print(DEFINITION)
    print(
        f"{report.in_clones} tokens en clones, de {report.code_tokens}; "
        f"{len(report.families)} clones."
    )
    for family in report.families:
        where = ", ".join(f"{o.file}:{o.first_line}-{o.last_line}" for o in family)
        print(f"{_size(family):>5}  {where}")
    if args.out is not None:
        Path(args.out).write_text(json.dumps(report.as_json(), ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
