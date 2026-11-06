from lictor.domain.spec_rules import (
    FeatureFile,
    ScenarioSeen,
    SpecProblem,
    TagSeen,
    blocking,
    describe_spec_problems,
    spec_problems,
)


def _scenario(
    name: str = "Uno", line: int = 4, tags: tuple[str, ...] = ("@ROM-001",), outline: bool = False
) -> ScenarioSeen:
    return ScenarioSeen(
        name=name,
        line=line,
        tags=tuple(TagSeen(tag, line - 1) for tag in tags),
        outline=outline,
    )


def _file(*scenarios: ScenarioSeen, file: str = "features/a.feature") -> FeatureFile:
    return FeatureFile(file=file, feature_name="A", feature_line=2, scenarios=scenarios)


def test_valid_features_have_no_problems() -> None:
    files = [
        _file(_scenario()),
        _file(_scenario("Dos", tags=("@ROM-002",)), file="features/b.feature"),
    ]

    assert spec_problems(files) == []


def test_no_feature_file_at_all_is_a_problem() -> None:
    (problem,) = spec_problems([])

    assert problem.blocking
    assert problem.file == "features/"
    assert problem.line is None
    assert "no hay ningún escenario" in problem.message
    assert ".feature" in problem.fix


def test_a_file_that_cant_be_parsed_says_where() -> None:
    files = [FeatureFile(file="features/a.feature", error="esperaba un paso", error_line=7)]

    (problem,) = spec_problems(files)

    assert (problem.file, problem.line) == ("features/a.feature", 7)
    assert "esperaba un paso" in problem.message


def test_a_file_without_a_feature_is_only_a_warning() -> None:
    files = [FeatureFile(file="features/a.feature"), _file(_scenario(), file="features/b.feature")]

    (problem,) = spec_problems(files)

    assert problem.file == "features/a.feature"
    assert problem.line is None
    assert not problem.blocking
    assert "vacío" in problem.message
    assert "bórralo" in problem.fix


def test_a_feature_without_scenarios_is_only_a_warning() -> None:
    files = [_file(), _file(_scenario(), file="features/b.feature")]

    (problem,) = spec_problems(files)

    assert problem.line == 2
    assert not problem.blocking
    assert "«A»" in problem.message
    assert "no tiene escenarios" in problem.message


def test_only_empty_files_means_there_is_no_scenario() -> None:
    problems = spec_problems([FeatureFile(file="features/a.feature"), _file()])

    assert [(p.file, p.blocking) for p in problems] == [
        ("features/a.feature", False),
        ("features/a.feature", False),
        ("features/", True),
    ]
    assert "no hay ningún escenario" in problems[-1].message


def test_every_other_problem_blocks() -> None:
    files = [
        _file(_scenario(tags=()), _scenario("Dos", line=8, outline=True)),
        FeatureFile(file="features/b.feature", error="mal", error_line=1),
    ]

    assert all(problem.blocking for problem in spec_problems(files))


def test_an_outline_is_refused_and_says_what_to_write_instead() -> None:
    (problem,) = spec_problems([_file(_scenario(outline=True))])

    assert problem.line == 4
    assert "Esquema del escenario" in problem.message
    assert "un escenario por cada fila" in problem.fix


def test_a_scenario_without_tags_has_no_id() -> None:
    (problem,) = spec_problems([_file(_scenario(tags=()))])

    assert "no tiene ID" in problem.message
    assert "@PREFIJO-NNN" in problem.fix


def test_a_scenario_with_more_than_its_id_names_every_tag() -> None:
    (problem,) = spec_problems([_file(_scenario(tags=("@ROM-001", "@lento")))])

    assert "@ROM-001, @lento" in problem.message
    assert "Deja solo su ID" in problem.fix


def test_a_tag_that_isnt_an_id() -> None:
    (problem,) = spec_problems([_file(_scenario(tags=("@rom-1",)))])

    assert "«@rom-1»" in problem.message
    assert "@ROM-001" in problem.fix


def test_a_duplicate_id_names_where_it_was_first() -> None:
    files = [
        _file(_scenario()),
        _file(_scenario("Otro", line=9), file="features/b.feature"),
    ]

    (problem,) = spec_problems(files)

    assert (problem.file, problem.line) == ("features/b.feature", 9)
    assert "@ROM-001" in problem.message
    assert "features/a.feature:4" in problem.message


def test_an_id_on_the_feature_or_a_rule_belongs_on_a_scenario() -> None:
    files = [
        FeatureFile(
            file="features/a.feature",
            feature_name="A",
            feature_line=2,
            level_tags=(("Feature", TagSeen("@ROM-009", 1)), ("Rule", TagSeen("@ROM-010", 5))),
            scenarios=(_scenario(line=8),),
        )
    ]

    problems = spec_problems(files)

    assert [(p.line, "característica" in p.message) for p in problems] == [(1, True), (5, False)]
    assert "regla" in problems[1].message
    assert all("Muévela" in p.fix for p in problems)


def test_tags_on_the_feature_that_arent_ids_are_fine() -> None:
    files = [
        FeatureFile(
            file="features/a.feature",
            feature_name="A",
            feature_line=2,
            level_tags=(("Feature", TagSeen("@lento", 1)),),
            scenarios=(_scenario(),),
        )
    ]

    assert spec_problems(files) == []


def test_every_problem_is_listed_not_only_the_first() -> None:
    files = [
        _file(_scenario(tags=()), _scenario("Dos", line=8, outline=True)),
        FeatureFile(file="features/b.feature"),
    ]

    assert len(spec_problems(files)) == 3  # an invalid scenario still is one: no «ningún escenario»


def test_a_problem_reads_as_place_message_and_fix() -> None:
    problem = SpecProblem("features/a.feature", 4, "el escenario no tiene ID", "Ponle uno.")

    assert str(problem) == "features/a.feature:4: el escenario no tiene ID. Ponle uno."
    assert str(SpecProblem("features/", None, "vacío", "Escribe.")) == "features/: vacío. Escribe."
    assert problem.blocking


def test_each_problem_reads_exactly() -> None:
    files = [
        FeatureFile(
            file="features/a.feature",
            feature_name="A",
            feature_line=2,
            level_tags=(("Feature", TagSeen("@ROM-009", 1)), ("Rule", TagSeen("@ROM-010", 5))),
            scenarios=(
                _scenario("Esquema", line=8, outline=True),
                _scenario("Sin ID", line=12, tags=()),
                _scenario("Varias", line=16, tags=("@ROM-001", "@lento")),
                _scenario("Mala", line=20, tags=("@rom-1",)),
                _scenario("Uno", line=24),
                _scenario("Repetido", line=28),
            ),
        ),
        FeatureFile(file="features/b.feature", error="esperaba un paso", error_line=3),
        FeatureFile(file="features/c.feature"),
        FeatureFile(file="features/d.feature", feature_name="D", feature_line=1),
    ]

    assert [(str(p), p.blocking) for p in spec_problems(files)] == [
        (
            "features/a.feature:1: la etiqueta @ROM-009 está en la característica, y los IDs van "
            "en cada escenario. Muévela al escenario que identifica.",
            True,
        ),
        (
            "features/a.feature:5: la etiqueta @ROM-010 está en una regla, y los IDs van en cada "
            "escenario. Muévela al escenario que identifica.",
            True,
        ),
        (
            "features/a.feature:8: el escenario «Esquema» es un Esquema del escenario (Scenario "
            "Outline), que Lictor no admite. Escribe un escenario por cada fila de Examples, cada "
            "uno con su ID.",
            True,
        ),
        (
            "features/a.feature:12: el escenario «Sin ID» no tiene ID. Ponle encima una etiqueta "
            "única con la forma @PREFIJO-NNN, por ejemplo @ROM-001.",
            True,
        ),
        (
            "features/a.feature:16: el escenario «Varias» tiene varias etiquetas: "
            "@ROM-001, @lento. Deja solo su ID, @PREFIJO-NNN: Lictor no admite otras etiquetas "
            "en un escenario.",
            True,
        ),
        (
            "features/a.feature:20: el escenario «Mala» tiene la etiqueta «@rom-1», que no es un "
            "ID. Usa la forma @PREFIJO-NNN: mayúsculas, un guion y un número, por ejemplo "
            "@ROM-001.",
            True,
        ),
        (
            "features/a.feature:28: el escenario «Repetido» usa el ID @ROM-001, que ya tiene "
            "features/a.feature:24. Cambia uno de los dos: cada ID es único en features/.",
            True,
        ),
        (
            "features/b.feature:3: no se puede leer: esperaba un paso. Corrige la sintaxis "
            "Gherkin.",
            True,
        ),
        (
            "features/c.feature: está vacío: no tiene ninguna característica. Escribe en él una "
            "característica con sus escenarios, o bórralo.",
            False,
        ),
        (
            "features/d.feature:1: la característica «D» no tiene escenarios. Añade al menos uno, "
            "o borra el fichero.",
            False,
        ),
    ]


def test_no_scenario_at_all_reads_exactly() -> None:
    (problem,) = spec_problems([])

    assert str(problem) == (
        "features/: no hay ningún escenario. Escribe al menos un fichero .feature con un escenario."
    )


def test_a_file_that_cant_be_read_is_no_empty_features_dir() -> None:
    files = [FeatureFile(file="features/a.feature", error="mal", error_line=1)]

    assert len(spec_problems(files)) == 1


def test_an_unknown_level_is_named_as_it_comes() -> None:
    files = [
        FeatureFile(
            file="features/a.feature",
            feature_name="A",
            feature_line=2,
            level_tags=(("Otro", TagSeen("@ROM-009", 1)),),
            scenarios=(_scenario(),),
        )
    ]

    (problem,) = spec_problems(files)

    assert problem.message.startswith("la etiqueta @ROM-009 está en Otro,")


def test_the_blocking_ones_and_how_they_are_listed() -> None:
    files = [FeatureFile(file="features/a.feature"), _file(_scenario(tags=()))]

    problems = spec_problems(files)

    assert blocking(problems) == [problems[1]]
    assert describe_spec_problems(problems[:2]) == f"- {problems[0]}\n- {problems[1]}"
