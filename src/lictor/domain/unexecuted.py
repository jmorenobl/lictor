"""What a run changed that no control executed (H2 of 0.20.0).

The rule is the start contract's, over the survey of the final tree, so a test the run added is
judged as it is there: `blind_spots` already says what no control runs, what only `check` runs,
what the tests load out of the agent's reach and what reads the code without any control. Two
facts the survey lacks complete it. A test file a command names (`run_by`) may still run no case
of it, as a command that deselects every test of the file by a mark: the JUnit of that command
says which files had cases. And what isn't Python is read by no control at all.

The code under `code_dir` has no class of its own: a lint and a type check read all of it, and
calling that «checked» would promise more than they do. These are facts for the person, never a
verdict (ADR-0009): nothing here blocks or changes an exit code.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, Literal

from lictor.domain.blind_spots import BlindSpots, StartRules, accepted_paths
from lictor.domain.rules import path_within
from lictor.domain.spec_rules import FEATURES_DIR
from lictor.domain.survey import SurveyedTests, SurveyReport

# How `report.md` and `check` title the facts.
UNEXECUTED_TITLE: Final[str] = "Cambios que ningún control ejecutó"
# The event a run leaves once it ends, with the facts or why there are none.
UNEXECUTED: Final[str] = "lictor.run.unexecuted.v1"

UnexecutedKind = Literal[
    "sin_control",
    "solo_check",
    "sin_ejecutar",
    "sin_lector",
    "sin_leer",
    "no_es_codigo",
]
# What each kind says, as the person reads it in `report.md` and in `check --base`.
KIND_TEXT: Final[Mapping[UnexecutedKind, str]] = {
    "sin_control": "tests que ningún control ejecuta",
    "solo_check": "tests que solo ejecuta `lictor check`",
    "sin_ejecutar": "tests que un control nombra y no ejecutó: ningún caso suyo en su informe",
    "sin_lector": "ficheros que importan el código y ningún control lee",
    "sin_leer": "Python fuera del código que ningún control lee",
    "no_es_codigo": "no es Python: ningún control lo ejecuta",
}
# The kinds of `blind_spots` that say a file isn't executed; the rest are about the CI, or about
# files that do run out of the agent's reach (`sin_cambiar`, `cargado`).
_FROM_SPOTS: Final[Collection[str]] = ("sin_control", "solo_check", "sin_lector")
CODE_NOTE: Final[str] = (
    "El código del proyecto no se lista: los controles estáticos lo leen entero, y eso no dice "
    "que algo lo ejecute."
)


@dataclass(frozen=True)
class Unexecuted:
    """A file the change touched that no control executed, and why."""

    path: str
    kind: UnexecutedKind


@dataclass(frozen=True)
class UnexecutedChanges:
    """What changed with no control executing it, what was deleted, and what can't be known."""

    files: tuple[Unexecuted, ...] = ()
    deleted: tuple[str, ...] = ()
    accepted: int = 0
    notes: tuple[str, ...] = ()


def dotted_test_module(path: str) -> str:
    """The dotted name JUnit gives a test file's cases: `tests/a/test_b.py` is `tests.a.test_b`."""
    return ".".join(PurePosixPath(path).with_suffix("").parts)


def files_with_cases(classnames: Iterable[str], test_files: Iterable[str]) -> frozenset[str]:
    """The test files a JUnit report has any case of, by the class name of each case."""
    names = set(classnames)
    found = set()
    for path in test_files:
        module = dotted_test_module(path)
        if any(name == module or name.startswith(f"{module}.") for name in names):
            found.add(path)
    return frozenset(found)


@dataclass(frozen=True)
class _Facts:
    """What decides each file: the spots, the survey's tests and readers, and the JUnit of each
    command."""

    by_spot: Mapping[str, str]
    tests: Mapping[str, SurveyedTests]
    read: Collection[str]  # loaded by the tests, importing the code, or read by a static check
    code_dir: str
    oracle: Sequence[str]
    ran: Mapping[str, frozenset[str] | None]
    unknown: bool

    def kind(self, path: str) -> UnexecutedKind | None:
        spot = self.by_spot.get(path)
        if spot is not None:
            return _as_kind(spot)
        test = self.tests.get(path)
        if test is not None:
            return None if self.unknown or self._executed(test) else "sin_ejecutar"
        if path_within(path, self.oracle):
            return None  # the scenarios and the driver: the acceptance runs them
        # Only `python-uv` has a survey, so only Python is code here. A profile with a survey of
        # another language will have to say its extensions (0.24.0 and later).
        if not path.endswith(".py"):
            return "no_es_codigo"
        if path_within(path, [self.code_dir]) or path in self.read:
            return None
        return "sin_leer"

    def _executed(self, test: SurveyedTests) -> bool:
        """Whether a command that names the file ran any case of it: its JUnit says, or,
        without one, the survey is trusted."""
        for command in test.run_by:
            files = self.ran.get(command)
            if files is None or test.path in files:
                return True
        return False


@dataclass(frozen=True)
class FinalTree:
    """The tree the change ends in: what the survey saw of it, its blind spots and its paths."""

    report: SurveyReport
    spots: BlindSpots
    present: Collection[str]


def unexecuted_changes(
    changed: Sequence[str],
    tree: FinalTree,
    rules: StartRules,
    ran: Mapping[str, frozenset[str] | None],
) -> UnexecutedChanges:
    """Of `changed`, what no control executed in `tree`.

    A changed path missing from the tree was deleted. `ran` gives, for each command id, the test
    files its JUnit had cases of, or None when it has no JUnit to read: then what the survey says
    it runs is trusted.
    """
    deleted = tuple(p for p in changed if p not in tree.present)
    accepted, judged = _split_accepted([p for p in changed if p in tree.present], rules)
    facts = _facts(tree, rules, ran)
    files = tuple(
        Unexecuted(path, kind) for path in judged if (kind := facts.kind(path)) is not None
    )
    return UnexecutedChanges(files, deleted, len(accepted), _notes(judged, facts))


def _split_accepted(paths: Sequence[str], rules: StartRules) -> tuple[list[str], list[str]]:
    """The paths the person accepted in `blind_spots.accepted`, and the rest."""
    roots = accepted_paths(rules)
    accepted = [p for p in paths if path_within(p, roots)]
    return accepted, [p for p in paths if p not in accepted]


def _facts(tree: FinalTree, rules: StartRules, ran: Mapping[str, frozenset[str] | None]) -> _Facts:
    report = tree.report
    commands = report.commands
    return _Facts(
        by_spot={
            path: spot.kind
            for spot in tree.spots.spots
            if spot.kind in _FROM_SPOTS
            for path in spot.paths
        },
        tests={t.path: t for t in report.tests},
        read={f.path for f in report.loaded}
        | {u.path for u in report.code_users}
        | {path for c in commands for path in c.covers},
        code_dir=report.code_dir,
        oracle=(FEATURES_DIR, *rules.driver),
        ran=ran,
        unknown=any(not c.understood for c in commands),
    )


def _notes(judged: Sequence[str], facts: _Facts) -> tuple[str, ...]:
    notes = [CODE_NOTE] if any(path_within(p, [facts.code_dir]) for p in judged) else []
    if facts.unknown:
        notes.append(
            "Hay comandos que no se sabe qué ejecutan: los tests que nombran no se juzgan aquí."
        )
    return tuple(notes)


def _as_kind(spot: str) -> UnexecutedKind:
    for kind in KIND_TEXT:
        if kind == spot:
            return kind
    raise ValueError(spot)


# Why nothing is said without the profile's survey: there is no reading of what each control runs.
UNREAD_BY_PROFILE = "el perfil no declara cómo leer lo que ejecuta cada control"


def unexecuted_record(
    found: UnexecutedChanges | str, started_at: str, head: str
) -> dict[str, object]:
    """What `lictor.run.unexecuted.v1` keeps: the facts, or why there are none."""
    if isinstance(found, str):
        return {"started_at": started_at, "head": head, "problem": found}
    return {
        "started_at": started_at,
        "head": head,
        "files": [{"path": f.path, "kind": f.kind} for f in found.files],
        "deleted": list(found.deleted),
        "accepted": found.accepted,
        "notes": list(found.notes),
        "problem": None,
    }


def unexecuted_lines(found: UnexecutedChanges) -> list[str]:
    """The lines a person reads, grouped by kind in a fixed order, deletions and notes last."""
    lines = _kind_lines(found.files)
    if found.deleted:
        lines.append(f"- Borrados: {_quoted(found.deleted)}")
    if found.accepted:
        lines.append(f"- Aceptados en blind_spots.accepted, solo contados: {found.accepted}")
    if not lines:
        lines.append("- Nada: cada fichero que cambió lo ejecuta o lo lee algún control.")
    return [*lines, *(f"- {note}" for note in found.notes)]


def _kind_lines(files: Sequence[Unexecuted]) -> list[str]:
    lines = []
    for kind, text in KIND_TEXT.items():
        paths = [f.path for f in files if f.kind == kind]
        if paths:
            lines.append(f"- {text[0].upper()}{text[1:]}: {_quoted(paths)}")
    return lines


def _quoted(paths: Sequence[str]) -> str:
    return ", ".join(f"`{p}`" for p in paths)


def unexecuted_from_record(record: object) -> UnexecutedChanges | str | None:
    """Read back what `run.unexecuted` kept: the facts, why there are none, or None if it
    isn't one."""
    if not isinstance(record, Mapping):
        return None
    if isinstance(problem := record.get("problem"), str):
        return problem
    raw = record.get("files")
    if not isinstance(raw, list):
        return None
    accepted = record.get("accepted")
    return UnexecutedChanges(
        tuple(_files_of(raw)),
        _strings(record.get("deleted")),
        accepted if isinstance(accepted, int) and not isinstance(accepted, bool) else 0,
        _strings(record.get("notes")),
    )


def _files_of(raw: Sequence[object]) -> list[Unexecuted]:
    files = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        path, kind = item.get("path"), item.get("kind")
        if isinstance(path, str) and isinstance(kind, str) and kind in KIND_TEXT:
            files.append(Unexecuted(path, _as_kind(kind)))
    return files


def _strings(raw: object) -> tuple[str, ...]:
    return tuple(item for item in raw if isinstance(item, str)) if isinstance(raw, list) else ()
