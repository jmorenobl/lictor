"""What a feature must be for Lictor to load it: the same rule for `load_spec` and `doctor`.

The adapter reads each file and says what it found, with no type of the Gherkin parser; this
module decides what is wrong, where, and what to do about it. Every problem is listed, not only
the first, so a person fixes them all in one pass.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from lictor.domain.errors import SpecError
from lictor.domain.model import ScenarioId

ID_FORM = "@PREFIJO-NNN"
FEATURES_DIR = "features/"


@dataclass(frozen=True)
class TagSeen:
    name: str
    line: int


@dataclass(frozen=True)
class ScenarioSeen:
    """A scenario as written: its tags, and whether its keyword or examples make it an outline."""

    name: str
    line: int
    tags: tuple[TagSeen, ...] = ()
    outline: bool = False


@dataclass(frozen=True)
class FeatureFile:
    """One `.feature` file: what couldn't be parsed, or its feature, tags and scenarios.

    `level_tags` are the tags of the feature and of its rules, as ("Feature" | "Rule", tag).
    A file without a feature has `feature_name` None.
    """

    file: str
    error: str | None = None
    error_line: int | None = None
    feature_name: str | None = None
    feature_line: int | None = None
    level_tags: tuple[tuple[str, TagSeen], ...] = ()
    scenarios: tuple[ScenarioSeen, ...] = ()


@dataclass(frozen=True)
class SpecProblem:
    """Where a feature is wrong, what is wrong and what to do; `line` None means the whole file.

    A problem that isn't `blocking` keeps nothing from loading: an empty file or a feature without
    scenarios demands nothing. `doctor` shows it as a warning and the run goes on without it.
    """

    file: str
    line: int | None
    message: str
    fix: str
    blocking: bool = True

    def __str__(self) -> str:
        place = self.file if self.line is None else f"{self.file}:{self.line}"
        return f"{place}: {self.message}. {self.fix}"


def _is_id(tag: str) -> bool:
    try:
        ScenarioId.from_tag(tag)
    except SpecError:
        return False
    return True


_LEVEL_NAMES = {"Feature": "la característica", "Rule": "una regla"}


def _level_problems(feature: FeatureFile) -> list[SpecProblem]:
    return [
        SpecProblem(
            feature.file,
            tag.line,
            f"la etiqueta {tag.name} está en {_LEVEL_NAMES.get(level, level)}, "
            "y los IDs van en cada escenario",
            "Muévela al escenario que identifica.",
        )
        for level, tag in feature.level_tags
        if _is_id(tag.name)
    ]


def _scenario_problem(file: str, scenario: ScenarioSeen) -> SpecProblem | None:
    """What keeps one scenario from loading, its ID's uniqueness aside."""
    where = f"el escenario «{scenario.name}»"
    names = [tag.name for tag in scenario.tags]
    if scenario.outline:
        return SpecProblem(
            file,
            scenario.line,
            f"{where} es un Esquema del escenario (Scenario Outline), que Lictor no admite",
            "Escribe un escenario por cada fila de Examples, cada uno con su ID.",
        )
    if not names:
        return SpecProblem(
            file,
            scenario.line,
            f"{where} no tiene ID",
            f"Ponle encima una etiqueta única con la forma {ID_FORM}, por ejemplo @ROM-001.",
        )
    if len(names) > 1:
        return SpecProblem(
            file,
            scenario.line,
            f"{where} tiene varias etiquetas: {', '.join(names)}",
            f"Deja solo su ID, {ID_FORM}: Lictor no admite otras etiquetas en un escenario.",
        )
    if not _is_id(names[0]):
        return SpecProblem(
            file,
            scenario.line,
            f"{where} tiene la etiqueta «{names[0]}», que no es un ID",
            f"Usa la forma {ID_FORM}: mayúsculas, un guion y un número, por ejemplo @ROM-001.",
        )
    return None


def _file_problems(feature: FeatureFile, seen: dict[str, tuple[str, int]]) -> list[SpecProblem]:
    if feature.error is not None:
        return [
            SpecProblem(
                feature.file,
                feature.error_line,
                f"no se puede leer: {feature.error}",
                "Corrige la sintaxis Gherkin.",
            )
        ]
    if feature.feature_name is None:
        return [
            SpecProblem(
                feature.file,
                None,
                "está vacío: no tiene ninguna característica",
                "Escribe en él una característica con sus escenarios, o bórralo.",
                blocking=False,
            )
        ]
    problems = _level_problems(feature)
    if not feature.scenarios:
        problems.append(
            SpecProblem(
                feature.file,
                feature.feature_line,
                f"la característica «{feature.feature_name}» no tiene escenarios",
                "Añade al menos uno, o borra el fichero.",
                blocking=False,
            )
        )
    for scenario in feature.scenarios:
        problem = _scenario_problem(feature.file, scenario)
        if problem is not None:
            problems.append(problem)
            continue
        sid = scenario.tags[0].name
        if sid in seen:
            first_file, first_line = seen[sid]
            problems.append(
                SpecProblem(
                    feature.file,
                    scenario.line,
                    f"el escenario «{scenario.name}» usa el ID {sid}, "
                    f"que ya tiene {first_file}:{first_line}",
                    f"Cambia uno de los dos: cada ID es único en {FEATURES_DIR}.",
                )
            )
        else:
            seen[sid] = (feature.file, scenario.line)
    return problems


def spec_problems(files: Sequence[FeatureFile]) -> list[SpecProblem]:
    """Everything that keeps these features from loading, file by file and in order."""
    seen: dict[str, tuple[str, int]] = {}
    problems = [problem for feature in files for problem in _file_problems(feature, seen)]
    if not any(feature.scenarios or feature.error for feature in files):
        problems.append(
            SpecProblem(
                FEATURES_DIR,
                None,
                "no hay ningún escenario",
                "Escribe al menos un fichero .feature con un escenario.",
            )
        )
    return problems


def blocking(problems: Sequence[SpecProblem]) -> list[SpecProblem]:
    """The problems that keep the features from loading."""
    return [problem for problem in problems if problem.blocking]


def describe_spec_problems(problems: Sequence[SpecProblem]) -> str:
    """One problem per line, for an error that has to list them all."""
    return "\n".join(f"- {problem}" for problem in problems)
