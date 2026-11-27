"""What the refactor of a scenario may change, and what it is asked to improve (F84, ADR-0010).

The refactor improves what its scenario changed. It is asked for what the green touched, by
function: the functions above the limit whose lines the green changed, and the clones with a place
on them. It may work by file: the code files the green changed, the files those clones reach on
their other side (removing a clone takes editing both), new files, and anything that isn't code,
which unit tests are. What the verification guards stays global: no measure of the whole code may
end worse than the green left it. The scope changes what is asked and where, not what is guarded.

Lines are those of the green's commit, which is the tree its verification measured; paths are
compared as text, relative to the project, so a report must name files as git does.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

from lictor.domain.complexity import OverLimit
from lictor.domain.duplication import Duplication
from lictor.domain.model import TreeChange
from lictor.domain.refactor import Findings
from lictor.domain.reports import Clone, FunctionComplexity
from lictor.domain.rules import path_within

REFACTOR_SCOPE = "L0-alcance"


@dataclass(frozen=True)
class CodePaths:
    """Where the code is: the profile's paths and the project's, less what the project excludes.

    `exclude` holds globs over the whole path (`*_test.go`): in Go and Node the unit tests live
    beside the code, and the refactor may change them as it moves the code. Only a project whose
    profile declares no code paths may write it, since over the profile's it would weaken the gate.
    """

    paths: tuple[str, ...]
    exclude: tuple[str, ...] = ()

    def holds(self, path: str) -> bool:
        """Tell whether `path` is code: under a code path and matching no exclusion."""
        return path_within(path, self.paths) and not any(
            fnmatchcase(path, glob) for glob in self.exclude
        )


NO_CODE = CodePaths(())


def excluded_everything(files: Sequence[str], code: CodePaths) -> str | None:
    """Why `code_exclude` leaves `L0-alcance` nothing to guard in these files, or None.

    `files` are a tree's. With code under its paths and every file of it excluded, the gate would
    pass any refactor, and nobody would see. With none there yet, nothing is judged: a project
    may start with its features alone.
    """
    under = [path for path in files if path_within(path, code.paths)]
    if not under or not code.exclude or any(code.holds(path) for path in under):
        return None
    return (
        f"code_exclude ({', '.join(code.exclude)}) excluye todos los ficheros de code_paths "
        f"({', '.join(code.paths)}): la puerta L0-alcance del refactor no comprobaría nada. Deja "
        "en code_exclude solo lo que no es código, como los tests junto a él (*_test.go); si ahí "
        "solo hay tests todavía, quita code_exclude hasta que haya código."
    )


@dataclass(frozen=True)
class LineSpan:
    """Lines a commit adds or changes, `first` to `last` both included, in its own numbering.

    A hunk that only deletes is the line before it, so the function it deleted from counts.
    """

    first: int
    last: int

    def overlaps(self, first: int, last: int) -> bool:
        """Tell whether the lines `first` to `last` share one with this span."""
        return self.first <= last and first <= self.last


def hunk_span(start: int, count: int) -> LineSpan:
    """The span of a hunk `+start,count` of a diff with no context.

    One that only deletes (`count` 0) sits after line `start`: it is that line, the end of what
    held the deleted lines, or the first line when it deleted the start of the file.
    """
    if count == 0:
        line = max(start, 1)
        return LineSpan(line, line)
    return LineSpan(start, start + count - 1)


# Every file a commit changes and still has, with the spans it added or changed: a file with
# none (an empty file added, a change of mode, a binary) is still one it changed.
type ChangedLines = Mapping[str, Sequence[LineSpan]]


@dataclass(frozen=True)
class RefactorScope:
    """Where the refactor of a scenario may work, and what it is asked to improve there."""

    files: frozenset[str]  # the code files the green changed
    clone_files: frozenset[str]  # the other code files of the clones it names
    functions: tuple[FunctionComplexity, ...] = ()  # above the limit, on the green's lines
    clones: tuple[Clone, ...] = ()  # with a place on the green's lines

    @property
    def allowed(self) -> frozenset[str]:
        """The code files that already exist and the refactor may change."""
        return self.files | self.clone_files

    def functions_of(self, over: OverLimit | None) -> list[FunctionComplexity]:
        """The functions of one measure the prompt names, as that measure orders them."""
        return [f for f in over.functions if f in self.functions] if over else []

    def clones_of(self, duplication: Duplication | None) -> list[Clone]:
        """The clones of one measure the prompt names, as that measure orders them."""
        return [c for c in duplication.clones if c in self.clones] if duplication else []


def _touches(changed: ChangedLines, file: str, first: int, last: int) -> bool:
    return any(span.overlaps(first, last) for span in changed.get(file, ()))


def _function_touched(changed: ChangedLines, function: FunctionComplexity) -> bool:
    return _touches(changed, function.file, function.line, function.last_line)


def _clone_touched(changed: ChangedLines, clone: Clone) -> bool:
    return any(_touches(changed, o.file, o.first_line, o.last_line) for o in clone.occurrences)


def refactor_scope(
    changed: ChangedLines, code: CodePaths, findings: Iterable[Findings]
) -> RefactorScope:
    """The scope of a refactor, from what its green changed and what its verification found.

    A function is named when a line of it is on the green's lines, a nested one and the one that
    holds it alike: both are what the agent reads there. A clone is named when one of its places
    is, and its other places open their files. Without a measure nothing is named, and the files
    come from the diff alone.
    """
    found = list(findings)
    functions = _named_functions(changed, _of(found, OverLimit))
    clones = _named_clones(changed, _of(found, Duplication))
    files = frozenset(path for path in changed if code.holds(path))
    reached = frozenset(o.file for clone in clones for o in clone.occurrences if code.holds(o.file))
    return RefactorScope(files, reached - files, functions, clones)


def _named_functions(
    changed: ChangedLines, measures: Sequence[OverLimit]
) -> tuple[FunctionComplexity, ...]:
    return tuple(f for m in measures for f in m.functions if _function_touched(changed, f))


def _named_clones(changed: ChangedLines, measures: Sequence[Duplication]) -> tuple[Clone, ...]:
    return tuple(c for m in measures for c in m.clones if _clone_touched(changed, c))


def _of[T](findings: Sequence[Findings], kind: type[T]) -> list[T]:
    return [f for f in findings if isinstance(f, kind)]


def outside_scope(findings: Iterable[Findings], scope: RefactorScope) -> str:
    """The line on what the measures found outside the scope: counted, never named."""
    found = list(findings)
    functions = sum(len(o.functions) for o in _of(found, OverLimit)) - len(scope.functions)
    clones = sum(len(d.clones) for d in _of(found, Duplication)) - len(scope.clones)
    return outside_scope_text(functions, clones)


def out_of_scope(scope: RefactorScope, changes: Sequence[TreeChange], code: CodePaths) -> list[str]:
    """The code files that already existed, the refactor changed and its scope doesn't allow.

    A new file is never outside: what is taken out of the scenario's files goes somewhere. A file
    deleted, or moved (a deletion and an addition), is changed like any other.
    """
    return sorted(
        {
            change.path
            for change in changes
            if code.holds(change.path) and not change.is_new and change.path not in scope.allowed
        }
    )


def out_of_scope_reason(outside: Sequence[str], scope: RefactorScope) -> str:
    """Why a refactor that left its scope is discarded: which files, and which it may change."""
    allowed = ", ".join(sorted(scope.allowed)) or "ninguno"
    return (
        "El refactor cambia ficheros de código que este escenario no cambió: "
        f"{', '.join(outside)}. De código solo puede cambiar los que ya existen entre estos: "
        f"{allowed}; y crear ficheros nuevos."
    )


def scope_rule(scope: RefactorScope) -> str:
    """The rule of the refactor's prompt on where it may work."""
    own = ", ".join(sorted(scope.files))
    reached = ", ".join(sorted(scope.clone_files))
    if own:
        files = f"los ficheros que cambió el escenario ({own})"
    else:
        files = (
            "ninguno de los que ya existen, porque el escenario no cambió ningún fichero de código"
        )
    clones = (
        f", y los que comparten con ellos un clon de los que se nombran abajo ({reached})"
        if reached
        else ""
    )
    return (
        f"- Este refactor mejora lo que cambió el escenario. De código solo puedes modificar "
        f"{files}{clones}. Puedes crear ficheros nuevos para lo que saques de ellos, y los tests "
        "unitarios pueden seguir al código que muevas. Modificar, mover o borrar cualquier otro "
        "fichero de código que ya exista descarta el refactor. Si un cambio te obligaría a tocar "
        "otro fichero de código, por ejemplo a quien importa lo que mueves, no hagas ese cambio."
    )


def outside_scope_text(functions: int, clones: int) -> str:
    """The line on what the measures found outside the scope: counted, never named."""
    parts = [
        *(
            [f"{functions} {'función' if functions == 1 else 'funciones'} por encima del límite"]
            if functions
            else []
        ),
        *([f"{clones} {'clon' if clones == 1 else 'clones'}"] if clones else []),
    ]
    if not parts:
        return ""
    return (
        f"Fuera de lo que cambió este escenario hay {' y '.join(parts)}: no son de este refactor."
    )


def code_paths_problem(code: Sequence[str], writable: Sequence[str]) -> str | None:
    """Why the code paths, the profile's and the project's together, can't scope a refactor here.

    With none of them in `paths.writable`, `L0-alcance` would check nothing, and nobody would see.
    """
    if not code:
        return (
            "ni el perfil ni lictor.json declaran code_paths: la puerta L0-alcance no sabría qué "
            "es código. Escribe en lictor.json code_paths con las rutas de código."
        )
    reached = [c for c in code if any(path_within(c, [w]) or path_within(w, [c]) for w in writable)]
    if reached:
        return None
    return (
        f"paths.writable ({', '.join(writable)}) no incluye ninguna ruta de código "
        f"({', '.join(code)}): la puerta L0-alcance del refactor no comprobaría nada. Añade a "
        "paths.writable la ruta donde está el código."
    )
