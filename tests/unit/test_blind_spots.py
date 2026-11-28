"""The start contract's judgment of the survey's facts (ADR-0015)."""

import copy
from typing import Any

import pytest

from lictor.domain.blind_spots import (
    BlindSpots,
    StartRules,
    blind_spot_lines,
    blind_spots,
    blind_spots_record,
    configuration_problems,
    metadata_facts,
    missing_plugins,
    noted_plugins,
    program_problems,
)
from lictor.domain.reports import InvalidReport
from lictor.domain.survey import (
    CodeUser,
    CoverageGate,
    LaunchedProgram,
    LoadedFile,
    ProjectImport,
    ProjectMetadata,
    SurveyedCi,
    SurveyedCommand,
    SurveyedTests,
    SurveyReport,
    survey_report,
)

FORBIDDEN = ("conftest.py", "pyproject.toml")
RULES = StartRules(
    writable=("src/", "tests/unit/"), driver=("tests/acceptance/",), forbidden=FORBIDDEN
)
UNIT = SurveyedCommand("unit", True, tests=True, paths=("tests/unit",))
ACCEPTANCE = SurveyedCommand("acceptance", True, tests=True, paths=("tests/acceptance",))
LINT = SurveyedCommand("lint", True, paths=("src",))
IMPORT = (ProjectImport(8, "pkg.chain", ("Chain",)),)


def _report(**fields: Any) -> SurveyReport:
    base: dict[str, Any] = {
        "code_dir": "src",
        "modules": ("pkg",),
        "commands": (UNIT, ACCEPTANCE, LINT),
    }
    return SurveyReport(**{**base, **fields})


def _kinds(spots: BlindSpots) -> dict[str, tuple[str, ...]]:
    return {s.kind: s.paths for s in spots.spots}


def test_a_project_whose_controls_see_everything_has_no_blind_spots() -> None:
    report = _report(tests=(SurveyedTests("tests/unit/test_a.py", 3, ("unit",)),))

    spots = blind_spots(report, RULES)

    assert spots == BlindSpots()
    assert spots.lines == ()


def test_pyverifactu_before_its_fix_says_the_three_things_the_review_saw() -> None:
    report = _report(
        tests=(
            SurveyedTests("tests/integration/test_a.py", 10),
            SurveyedTests("tests/integration/test_b.py", 5),
            SurveyedTests("tests/test_async.py", 2),
            SurveyedTests("tests/unit/test_ok.py", 4, ("unit",)),
        ),
        loaded=(
            LoadedFile("tests/conftest.py", "conftest", ("unit", "acceptance"), IMPORT),
            LoadedFile("tests/fixtures/sample.py", "module", ("unit",), IMPORT),
            LoadedFile("tests/unit/helpers.py", "module", ("unit",), IMPORT),
            LoadedFile("tests/integration/conftest.py", "conftest", ("unit",)),
        ),
        code_users=(CodeUser("examples/demo.py", (), IMPORT),),
    )

    spots = blind_spots(report, RULES)

    assert _kinds(spots) == {
        "sin_control": (
            "tests/integration/test_a.py",
            "tests/integration/test_b.py",
            "tests/test_async.py",
        ),
        "cargado": ("tests/conftest.py", "tests/fixtures/sample.py"),
        "sin_lector": ("examples/demo.py",),
    }
    unrun, loaded, unread = spots.lines
    assert "tests/integration/ (2), tests/test_async.py, 17 tests" in unrun
    assert "tests/conftest.py (línea 8: Chain)" in loaded
    assert "fallan 'unit', 'acceptance'" in loaded
    assert "examples/demo.py" in unread


def test_tests_the_run_runs_and_the_agent_cant_change_are_named() -> None:
    report = _report(
        commands=(SurveyedCommand("unit", True, tests=True, paths=("tests",)), ACCEPTANCE),
        tests=(
            SurveyedTests("tests/test_async.py", 2, ("unit",)),
            SurveyedTests("tests/unit/test_ok.py", 4, ("unit",)),
            SurveyedTests("tests/unit/conftest_like/conftest.py", 1, ("unit",)),
            SurveyedTests("tests/acceptance/test_x.py", 1, ("unit", "acceptance")),
        ),
    )

    spots = blind_spots(report, RULES)

    # A forbidden name can't be changed even inside `writable`; the driver is never named.
    assert _kinds(spots) == {
        "sin_cambiar": ("tests/test_async.py", "tests/unit/conftest_like/conftest.py")
    }


def test_tests_only_lictor_check_runs_are_told_apart() -> None:
    rules = StartRules(RULES.writable, RULES.driver, FORBIDDEN, check_only=("slow",))
    report = _report(
        commands=(UNIT, ACCEPTANCE, SurveyedCommand("slow", True, tests=True, paths=("tests",))),
        tests=(
            SurveyedTests("tests/slow/test_s.py", 1, ("slow",)),
            SurveyedTests("tests/unit/test_ok.py", 1, ("unit", "slow")),
        ),
        loaded=(LoadedFile("tests/slow/conftest.py", "conftest", ("slow",), IMPORT),),
    )

    spots = blind_spots(report, rules)

    # What only `check` loads stops no run, and isn't counted as fixed for it either.
    assert _kinds(spots) == {"solo_check": ("tests/slow/test_s.py",)}


def test_a_command_whose_reach_is_unknown_silences_what_it_might_see() -> None:
    report = _report(
        commands=(
            SurveyedCommand("unit", False, reason="make puede ejecutar cualquier cosa"),
            LINT,
        ),
        tests=(SurveyedTests("tests/integration/test_a.py", 1),),
        loaded=(LoadedFile("tests/conftest.py", "conftest", ("lint",), IMPORT),),
        code_users=(CodeUser("examples/demo.py", (), IMPORT),),
    )

    spots = blind_spots(report, RULES)

    assert _kinds(spots) == {"cargado": ("tests/conftest.py",)}
    assert spots.unknown == (
        "No se puede saber qué ejecuta el comando del control 'unit': make puede ejecutar "
        "cualquier cosa.",
        # What it silences is counted, so a blind command doesn't read as a project with none.
        "Por eso quedan sin juzgar 1 ficheros de tests (1 tests) que ningún otro comando ejecuta.",
    )
    assert spots.unknown[0] in spots.lines


def test_what_the_person_accepted_is_only_counted() -> None:
    rules = StartRules(
        RULES.writable, RULES.driver, FORBIDDEN, accepted=("tests/integration/", "examples/")
    )
    report = _report(
        tests=(
            SurveyedTests("tests/integration/test_a.py", 1),
            SurveyedTests("tests/other/test_b.py", 1),
        ),
        code_users=(CodeUser("examples/demo.py", (), IMPORT),),
    )

    spots = blind_spots(report, rules)

    assert _kinds(spots) == {"sin_control": ("tests/other/test_b.py",)}
    assert spots.accepted == 2
    assert spots.lines[-1] == "Aceptados en lictor.json (blind_spots.accepted): 2 ficheros."


def test_notes_go_only_with_something_to_tell() -> None:
    selecting = SurveyedCommand("unit", True, tests=True, paths=("tests",), selects=True)
    quiet = _report(commands=(selecting,), projects=("examples/other",), unreadable=("x.py",))
    loud = _report(
        commands=(selecting,),
        tests=(SurveyedTests("other/test_a.py", 1),),
        projects=("examples/other",),
        unreadable=("x.py",),
    )

    assert blind_spots(quiet, RULES).lines == ()
    assert blind_spots(loud, RULES).lines[1:] == (
        "El control 'unit' selecciona qué tests corre: lo que deja fuera no se cuenta.",
        "No se pudieron leer, y no se miran: x.py.",
        "Son otros proyectos, y no se miran: examples/other.",
    )


def test_without_the_projects_code_nothing_is_judged() -> None:
    spots = blind_spots(_report(modules=(), tests=(SurveyedTests("t/test_a.py", 1),)), RULES)

    assert spots.skipped == "no comprobado: no hay código del proyecto en src/"
    assert spots.spots == ()


def test_a_plugin_the_acceptance_needs_and_the_environment_lacks_is_the_projects() -> None:
    needs = {"bdd.plugin": "añade bdd", "otro.plugin": "añade otro"}

    problems = missing_plugins(needs, ("otro.plugin", "x"))

    assert [p.kind for p in problems] == ["config"]
    assert "bdd.plugin" in problems[0].message
    assert "(añade bdd)" in problems[0].message
    assert missing_plugins({}, ()) == []


def test_a_program_the_environment_lacks_is_the_projects_and_only_warns_for_check() -> None:
    report = _report(
        commands=(
            SurveyedCommand("unit", True, program=LaunchedProgram("tester", "/env/bin/t", True)),
            SurveyedCommand("lint", True, program=LaunchedProgram("linter", None, False)),
            SurveyedCommand("types", True, program=LaunchedProgram("typer", "/usr/bin/t", False)),
            SurveyedCommand("audit", True, program=LaunchedProgram("auditor", None, False)),
            SurveyedCommand("acceptance", False, reason="x", program=None),
        ),
    )

    problems, warnings = program_problems(report, ("audit",))

    assert [p.kind for p in problems] == ["config", "config"]
    assert "'linter'" in problems[0].message
    assert "usaría el de la máquina" not in problems[0].message
    assert "usaría el de la máquina (/usr/bin/t)" in problems[1].message
    assert len(warnings) == 1
    assert "'auditor'" in warnings[0]
    assert warnings[0].endswith("solo lo ejecuta `lictor check`.")


def test_the_record_keeps_what_the_report_shows() -> None:
    spots = blind_spots(_report(tests=(SurveyedTests("t/test_a.py", 1),)), RULES)

    record = blind_spots_record(spots)

    assert record is not None
    assert record["spots"] == [{"kind": "sin_control", "paths": ["t/test_a.py"]}]
    assert blind_spot_lines(record) == list(spots.lines)
    assert blind_spots_record(None) is None
    assert blind_spot_lines(None) is None
    assert blind_spot_lines({"skipped": "no comprobado"}) == ["no comprobado"]
    assert blind_spot_lines({"lines": "no"}) is None


GOOD: dict[str, Any] = {
    "code": {"dir": "src", "modules": ["pkg"]},
    "commands": [
        {
            "id": "unit",
            "status": "understood",
            "tests": True,
            "paths": ["tests/unit"],
            "selects": False,
            "program": {"name": "pytest", "path": "/env/bin/pytest", "in_env": True},
        },
        {"id": "make", "status": "unknown", "reason": "make", "program": None},
    ],
    "tests": [{"path": "tests/unit/test_a.py", "tests": 2, "run_by": ["unit"]}],
    "loaded": [
        {
            "path": "tests/conftest.py",
            "kind": "conftest",
            "by": ["unit"],
            "imports": [{"line": 3, "module": "pkg", "names": ["f"]}],
        }
    ],
    "code_users": [
        {"path": "examples/a.py", "read_by": [], "imports": [{"line": 1, "module": "pkg"}]}
    ],
    "skipped": {"projects": [], "unreadable": []},
}


def test_a_survey_report_is_read_as_facts() -> None:
    data = copy.deepcopy(GOOD)
    data["commands"][0]["selects"] = True
    data["skipped"] = {"projects": ["examples/x"], "unreadable": ["y.py"]}

    assert survey_report(data) == SurveyReport(
        code_dir="src",
        modules=("pkg",),
        commands=(
            SurveyedCommand(
                "unit",
                True,
                tests=True,
                paths=("tests/unit",),
                selects=True,
                program=LaunchedProgram("pytest", "/env/bin/pytest", True),
            ),
            SurveyedCommand("make", False, reason="make"),
        ),
        tests=(SurveyedTests("tests/unit/test_a.py", 2, ("unit",)),),
        loaded=(
            LoadedFile(
                "tests/conftest.py", "conftest", ("unit",), (ProjectImport(3, "pkg", ("f",)),)
            ),
        ),
        code_users=(CodeUser("examples/a.py", (), (ProjectImport(1, "pkg"),)),),
        projects=("examples/x",),
        unreadable=("y.py",),
    )


def test_a_command_that_runs_no_tests_and_a_module_loaded_are_read_too() -> None:
    data = copy.deepcopy(GOOD)
    data["commands"][0].update(tests=False, program=None)
    data["loaded"][0].update(kind="module")
    del data["loaded"][0]["imports"]

    report = survey_report(data)

    assert isinstance(report, SurveyReport)
    assert report.commands[0].tests is False
    assert report.commands[0].program is None
    assert report.loaded[0] == LoadedFile("tests/conftest.py", "module", ("unit",))


def _with(path: tuple[str | int, ...], value: object) -> dict[str, Any]:
    data: Any = copy.deepcopy(GOOD)
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return dict(data)


DELETE = object()


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        ((), "x", "el informe no es un objeto"),
        (("code",), DELETE, "code no es un objeto"),
        (("code", "dir"), "", "code.dir no es un texto"),
        (("code", "modules"), "pkg", "code.modules no es una lista"),
        (("code", "modules"), [1], "code.modules no es un texto"),
        (("commands",), {}, "commands no es una lista"),
        (("commands", 0), [], "el comando 0 no es un objeto"),
        (("commands", 0, "id"), 3, "el comando 0.id no es un texto"),
        (("commands", 0, "status"), "maybe", "el comando 0 tiene un estado que no existe: 'maybe'"),
        (("commands", 1, "reason"), "", "el comando 1.reason no es un texto"),
        (("commands", 1, "id"), "unit", "commands repite un id"),
        (("commands", 0, "paths"), "tests", "el comando 0.paths no es una lista"),
        (("commands", 0, "paths"), ["/etc"], "el comando 0.paths sale del árbol: '/etc'"),
        (("commands", 0, "paths"), ["a/../../x"], "el comando 0.paths sale del árbol: 'a/../../x'"),
        (("commands", 0, "tests"), "sí", "el comando 0.tests no es verdadero ni falso"),
        (("commands", 0, "selects"), 1, "el comando 0.selects no es verdadero ni falso"),
        (("commands", 0, "program"), [], "el comando 0.program no es un objeto"),
        (("commands", 0, "program", "name"), "", "el comando 0.program.name no es un texto"),
        (("commands", 0, "program", "path"), 7, "el comando 0.program.path no es un texto"),
        (
            ("commands", 0, "program", "in_env"),
            "sí",
            "el comando 0.program.in_env no es verdadero ni falso",
        ),
        (("tests",), None, "tests no es una lista"),
        (("tests", 0), "x", "el fichero de tests 0 no es un objeto"),
        (("tests", 0, "path"), None, "el fichero de tests 0.path no es un texto"),
        (("tests", 0, "tests"), 0, "el fichero de tests 0.tests no es un número válido: 0"),
        (("tests", 0, "tests"), True, "el fichero de tests 0.tests no es un número válido: True"),
        (("tests", 0, "run_by"), None, "el fichero de tests 0.run_by no es una lista"),
        (
            ("tests", 0, "run_by"),
            ["otro", "más"],
            "el fichero de tests 0.run_by nombra comandos que no se le dieron: otro, más",
        ),
        (("loaded",), None, "loaded no es una lista"),
        (("loaded", 0), 1, "el fichero cargado 0 no es un objeto"),
        (
            ("loaded", 0, "kind"),
            "plugin",
            "el fichero cargado 0 tiene un tipo que no existe: 'plugin'",
        ),
        (("loaded", 0, "by"), [], "el fichero cargado 0 no lo carga ningún comando"),
        (
            ("loaded", 0, "by"),
            ["x"],
            "el fichero cargado 0.by nombra comandos que no se le dieron: x",
        ),
        (("loaded", 0, "path"), "", "el fichero cargado 0.path no es un texto"),
        (("loaded", 0, "imports"), {}, "el fichero cargado 0.imports no es una lista"),
        (("loaded", 0, "imports", 0), 2, "el fichero cargado 0.imports 0 no es un objeto"),
        (
            ("loaded", 0, "imports", 0, "line"),
            0,
            "el fichero cargado 0.imports 0.line no es un número válido: 0",
        ),
        (
            ("loaded", 0, "imports", 0, "module"),
            "",
            "el fichero cargado 0.imports 0.module no es un texto",
        ),
        (
            ("loaded", 0, "imports", 0, "names"),
            [""],
            "el fichero cargado 0.imports 0.names no es un texto",
        ),
        (("code_users",), None, "code_users no es una lista"),
        (("code_users", 0), 1, "el fichero de código 0 no es un objeto"),
        (("code_users", 0, "imports"), [], "el fichero de código 0 no importa el proyecto"),
        (("code_users", 0, "path"), "", "el fichero de código 0.path no es un texto"),
        (
            ("code_users", 0, "read_by"),
            ["x"],
            "el fichero de código 0.read_by nombra comandos que no se le dieron: x",
        ),
        (("code_users", 0, "path"), "tests/conftest.py", "el informe repite tests/conftest.py"),
        (("skipped",), [], "skipped no es un objeto"),
        (("skipped", "projects"), None, "skipped.projects no es una lista"),
        (("skipped", "unreadable"), ["/x"], "skipped.unreadable sale del árbol: '/x'"),
    ],
)
def test_a_survey_report_that_cant_be_one_is_invalid(
    path: tuple[str | int, ...], value: object, reason: str
) -> None:
    report = survey_report(value if path == () else _with(path, value))

    assert report == InvalidReport(reason)


def test_a_program_without_a_path_is_never_in_the_environment() -> None:
    report = survey_report(
        _with(("commands", 0, "program"), {"name": "x", "path": None, "in_env": True})
    )

    assert isinstance(report, SurveyReport)
    assert report.commands[0].program == LaunchedProgram("x", None, False)


TWO_IMPORTS = (ProjectImport(8, "pkg.chain", ("Chain",)), ProjectImport(9, "pkg"))
ACCEPT = "Si es una decisión del proyecto, apunta su ruta en blind_spots.accepted de lictor.json."


def test_each_spot_says_what_it_is_and_what_to_do_word_for_word() -> None:
    report = _report(
        commands=(
            SurveyedCommand("unit", True, tests=True, paths=("tests",)),
            ACCEPTANCE,
            SurveyedCommand("slow", True, tests=True, paths=("x",)),
        ),
        tests=(
            SurveyedTests("test_r1.py", 1),
            SurveyedTests("test_r2.py", 2),
            SurveyedTests("tests/test_f.py", 1, ("unit",)),
            SurveyedTests("x/test_s.py", 1, ("slow",)),
        ),
        loaded=(
            LoadedFile(
                "tests/conftest.py", "conftest", ("unit", "acceptance", "slow"), TWO_IMPORTS
            ),
        ),
        code_users=(CodeUser("examples/a.py", (), TWO_IMPORTS),),
    )
    rules = StartRules(("src/",), ("tests/acceptance/",), ("conftest.py",), check_only=("slow",))

    assert blind_spots(report, rules).lines == (
        "Tests que ningún control ejecuta: la raíz (2), 3 tests. Un cambio de la API puede "
        "romperlos sin que se vea. Para que cuenten, amplía en lictor.json el run del control que "
        f"ejecuta los tests y añade su directorio a paths.writable. {ACCEPT}",
        "Tests que solo ejecuta `lictor check`: x/test_s.py. El run no los ve, y el agente puede "
        "romperlos sin saberlo.",
        "Tests que el run ejecuta y el agente no puede cambiar: tests/test_f.py. Si un escenario "
        "cambia la API que usan, el run parará a la espera de una persona. Añade su directorio a "
        f"paths.writable, o sácalos del run de ese control. {ACCEPT}",
        "Ficheros que cargan los tests del run, importan el código y el agente no puede cambiar: "
        "tests/conftest.py (líneas 8, 9: Chain, pkg). Si un escenario cambia lo que importan, "
        "fallan 'unit', 'acceptance' y el run para a la espera de una persona. Mueve lo que "
        "necesitan junto a los tests que lo usan, en una ruta que el agente pueda cambiar. "
        f"{ACCEPT}",
        "Código que usa el proyecto y que ningún control lee: examples/a.py. Un cambio de la API "
        "puede romperlo sin que se vea: añade su directorio al run de los controles que leen el "
        f"código (los de estilo y tipos). {ACCEPT}",
    )


def test_what_is_missing_is_said_word_for_word() -> None:
    machine = LaunchedProgram("pytest", "/usr/bin/pytest", False)
    report = _report(commands=(SurveyedCommand("acceptance", True, program=machine),))

    (problem,), _ = program_problems(report, ())

    assert problem.message == (
        "El comando de la aceptación lanza 'pytest' con el entorno de verificación, y el entorno "
        "no lo tiene: usaría el de la máquina (/usr/bin/pytest), que no fija el lock, con otro "
        "intérprete y sin las dependencias del proyecto. Añade al proyecto la dependencia de "
        "desarrollo que lo instala y confirma los ficheros de dependencias, que el agente no puede "
        "tocar; o, si es de la máquina a propósito, escribe el comando sin lanzarlo con el entorno."
    )
    assert missing_plugins({"p": "add p"}, ())[0].message == (
        "La aceptación necesita el plugin p, que el entorno de verificación no instala: añádelo al "
        "proyecto (add p) y confirma los ficheros de dependencias que cambie. El agente no puede "
        "tocarlos."
    )
    lint = SurveyedCommand("lint", True, program=LaunchedProgram("linter", None, False))
    (missing,), _ = program_problems(_report(commands=(lint,)), ())
    assert missing.message.startswith(
        "El comando del control 'lint' lanza 'linter' con el entorno de verificación, y el entorno "
        "no lo tiene. Añade"
    )


def test_the_spots_of_several_directories_are_grouped_in_order() -> None:
    report = _report(
        tests=(
            SurveyedTests("tests/a/test_1.py", 1),
            SurveyedTests("tests/a/test_2.py", 1),
            SurveyedTests("tests/b/test_3.py", 1),
        )
    )

    (line,) = blind_spots(report, RULES).lines

    assert line.startswith("Tests que ningún control ejecuta: tests/a/ (2), tests/b/test_3.py, 3 ")


def test_the_record_keeps_every_field_by_name() -> None:
    rules = StartRules(RULES.writable, RULES.driver, FORBIDDEN, accepted=("t/",))
    accepted = blind_spots(_report(tests=(SurveyedTests("t/test_a.py", 1),)), rules)
    skipped = blind_spots(_report(modules=()), RULES)

    assert blind_spots_record(accepted) == {
        "skipped": None,
        "spots": [],
        "lines": ["Aceptados en lictor.json (blind_spots.accepted): 1 ficheros."],
        "accepted": 1,
        "accepted_ci": 0,
    }
    assert blind_spots_record(skipped) == {
        "skipped": "no comprobado: no hay código del proyecto en src/",
        "spots": [],
        "lines": [],
        "accepted": 0,
        "accepted_ci": 0,
    }


def test_lists_are_joined_one_by_one() -> None:
    report = _report(
        loaded=(
            LoadedFile("tests/a.py", "module", ("unit",), IMPORT),
            LoadedFile("tests/b.py", "module", ("unit",), IMPORT),
        ),
        code_users=(
            CodeUser("tests/acceptance/x.py", (), IMPORT),
            CodeUser("tests/acceptance/y.py", ("lint",), IMPORT),
        ),
        unreadable=("u1.py", "u2.py"),
        projects=("p1", "p2"),
    )

    loaded, *notes = blind_spots(report, RULES).lines

    assert "tests/a.py (línea 8: Chain); tests/b.py (línea 8: Chain)." in loaded
    assert notes == [
        "No se pudieron leer, y no se miran: u1.py, u2.py.",
        "Son otros proyectos, y no se miran: p1, p2.",
    ]


def test_an_unknown_command_keeps_its_program_and_a_file_its_single_test() -> None:
    data = copy.deepcopy(GOOD)
    data["commands"][1]["program"] = {"name": "make", "path": None, "in_env": False}
    data["tests"][0]["tests"] = 1
    data["tests"].append({"path": "t/test_b.py", "tests": 1, "run_by": []})
    data["tests"].append({"path": "t/test_b.py", "tests": 1, "run_by": []})

    assert survey_report(data) == InvalidReport("el informe repite t/test_b.py")
    data["tests"].pop()
    report = survey_report(data)

    assert isinstance(report, SurveyReport)
    assert report.commands[1].program == LaunchedProgram("make", None, False)
    assert report.tests[0].tests == 1
    data["code_users"][0]["imports"] = {}
    assert survey_report(data) == InvalidReport("el fichero de código 0.imports no es una lista")


# What the CI checks against what the controls do (F76), and what the configuration of the tests
# does to the commands (F78).

FORMAT = "ruff format"
CHECK = "ruff check"
WORKFLOW = ".github/workflows/test.yml"


def _step(line: int, run: str, tool: str | None, covers: tuple[str, ...] = ()) -> SurveyedCi:
    tests = tool == "pytest"
    return SurveyedCi(WORKFLOW, line, run, "workflow", True, tool=tool, covers=covers, tests=tests)


def _checker(cid: str, tool: str, covers: tuple[str, ...]) -> SurveyedCommand:
    return SurveyedCommand(cid, True, paths=("src",), tool=tool, covers=covers)


def test_pyverifactu_main_checks_its_format_and_no_control_does() -> None:
    report = _report(
        commands=(UNIT, ACCEPTANCE, _checker("lint", CHECK, ("src/a.py",))),
        ci=(
            _step(25, "uv run pytest -m 'not integration'", "pytest"),
            _step(29, "uv run ruff check src/", CHECK, ("src/a.py",)),
            _step(31, "uv run ruff format --check src/", FORMAT, ("src/a.py",)),
            _step(23, "uv sync --all-extras", None),
        ),
    )

    spots = blind_spots(report, RULES)

    assert _kinds(spots) == {"ci_sin_control": (f"{WORKFLOW}:31",)}
    (line,) = spots.lines
    assert line.startswith(
        f"La CI comprueba `uv run ruff format --check src/` ({WORKFLOW}:31) y ningún control lo "
        "hace: el run puede dejar un verde que la CI rechace."
    )
    assert "«fichero: comando» en blind_spots.accepted" in line


def test_a_control_that_runs_the_check_is_recognised_by_its_command_not_its_id() -> None:
    report = _report(
        commands=(UNIT, ACCEPTANCE, _checker("estilo", FORMAT, ("src/a.py",))),
        ci=(_step(31, "uv run ruff format --check src/", FORMAT, ("src/a.py",)),),
    )

    assert blind_spots(report, RULES) == BlindSpots()


def test_a_check_of_the_ci_on_more_files_than_its_control_names_them() -> None:
    report = _report(
        commands=(UNIT, ACCEPTANCE, _checker("lint", CHECK, ("src/a.py",))),
        ci=(
            _step(
                27,
                "uv run ruff check .",
                CHECK,
                ("noxfile.py", "scripts/s.py", "src/a.py", "tests/a.py", "tests/b/c.py"),
            ),
        ),
    )

    spots = blind_spots(report, RULES)

    assert _kinds(spots) == {
        "ci_mas_ficheros": ("noxfile.py", "scripts/s.py", "tests/a.py", "tests/b/c.py")
    }
    assert spots.lines[0].startswith(
        f"La CI comprueba `uv run ruff check .` ({WORKFLOW}:27) en ficheros que no lee ningún "
        "control que haga lo mismo ('lint'): noxfile.py, scripts/ (1), tests/ (2); 4 ficheros."
    )


def test_the_tests_the_ci_runs_are_left_to_the_tests_no_control_runs() -> None:
    report = _report(
        ci=(_step(35, "uv run pytest", "pytest"),),
        tests=(
            SurveyedTests("tests/unit/test_a.py", 3, ("unit",), (0,)),
            SurveyedTests("tests/e2e/test_b.py", 2, (), (0,)),
        ),
    )

    spots = blind_spots(report, RULES)

    assert _kinds(spots) == {"sin_control": ("tests/e2e/test_b.py",)}
    assert f"La CI sí los ejecuta: `uv run pytest` ({WORKFLOW}:35)." in spots.lines[0]


def test_a_step_of_the_ci_is_accepted_by_file_and_command_never_by_file() -> None:
    accepted = (
        f"{WORKFLOW}:  uv run  ruff format --check src/",
        ".pre-commit-config.yaml",
        "tests/",
    )
    rules = StartRules(RULES.writable, RULES.driver, FORBIDDEN, accepted=accepted)
    report = _report(
        ci=(
            _step(31, "uv run ruff format --check src/", FORMAT),
            SurveyedCi(
                ".pre-commit-config.yaml", 8, "ruff format", "pre-commit", True, tool=FORMAT
            ),
        ),
    )

    spots = blind_spots(report, rules)

    # The hook added to a file accepted as a path still warns.
    assert _kinds(spots) == {"ci_sin_control": (".pre-commit-config.yaml:8",)}
    assert spots.accepted_ci == 1
    assert spots.lines[-1] == (
        "Aceptados en lictor.json (blind_spots.accepted): 1 comandos de la CI."
    )


def test_what_of_the_ci_cant_be_read_or_known_is_named_alone() -> None:
    report = _report(
        ci=(
            SurveyedCi(
                WORKFLOW, 18, "uv run ${{ matrix.cmd }}", "workflow", False, "una expresión"
            ),
            SurveyedCi(WORKFLOW, 19, "make lint", "workflow", False, "make puede ejecutar"),
        ),
        ci_unreadable=(".github/workflows/bad.yml: no es YAML válido (línea 2)",),
    )

    spots = blind_spots(report, RULES)

    assert spots.spots == ()
    assert spots.lines == (
        "No se pudo leer la CI en .github/workflows/bad.yml: no es YAML válido (línea 2).",
        f"No se puede saber qué comprueba la CI en `uv run ${{{{ matrix.cmd }}}}` ({WORKFLOW}:18): "
        f"una expresión; `make lint` ({WORKFLOW}:19): make puede ejecutar.",
    )


def test_a_cov_that_takes_the_commands_path_stops_the_run() -> None:
    eaten = SurveyedCommand(
        "unit",
        True,
        tests=True,
        paths=(".",),
        config="pyproject.toml",
        eaten="tests/unit",
    )

    (problem,) = configuration_problems(_report(commands=(eaten, ACCEPTANCE)))

    assert problem.kind == "config"
    assert problem.message == (
        "El comando del control 'unit' nombra tests/unit, pero el `--cov` con que acaba addopts "
        "en pyproject.toml lo toma como su valor, y lo que se ejecuta es .. Escribe "
        "`--cov=<paquete>` en addopts, con el paquete que mide; el agente no puede tocar esa "
        "configuración."
    )


def test_a_minimum_inherited_with_the_cov_of_addopts_stops_the_run_once() -> None:
    gate = CoverageGate("--cov=bookwright", "pyproject.toml", "80", "pyproject.toml")
    commands = tuple(
        SurveyedCommand(cid, True, tests=True, paths=(p,), gate=gate)
        for cid, p in (("unit", "tests/unit"), ("acceptance", "tests/acceptance"))
    )

    (problem,) = configuration_problems(_report(commands=(*commands, LINT)))

    assert problem.kind == "config"
    assert problem.message.startswith(
        "addopts en pyproject.toml mide la cobertura en todo comando de tests (--cov=bookwright) "
        "y pyproject.toml exige un mínimo (80 %): un comando que ejecuta solo una parte de los "
        "tests sale con un fallo aunque pasen todos, y la aceptación nunca podría pasar. Afecta "
        "a 'unit' y la aceptación. Saca --cov=bookwright de addopts"
    )


def test_a_configuration_that_does_nothing_to_the_commands_is_no_problem() -> None:
    assert configuration_problems(_report()) == []


CI_GOOD: dict[str, Any] = {
    **copy.deepcopy(GOOD),
    "ci": [
        {
            "file": WORKFLOW,
            "line": 31,
            "run": "uv run ruff format --check src/",
            "origin": "workflow",
            "status": "understood",
            "tool": FORMAT,
            "covers": ["src/a.py"],
        },
        {
            "file": WORKFLOW,
            "line": 32,
            "run": "make",
            "origin": "workflow",
            "status": "unknown",
            "tool": None,
            "reason": "make",
        },
    ],
}


def test_the_ci_and_the_new_facts_of_a_command_are_read() -> None:
    data = copy.deepcopy(CI_GOOD)
    data["tests"][0]["ci_run_by"] = [1]
    data["commands"][0].update(
        tool="pytest",
        config="pyproject.toml",
        eaten="tests/unit",
        gate={"cov": "--cov", "addopts": "pyproject.toml", "threshold": "80", "source": "a.cfg"},
    )

    report = survey_report(data)

    assert isinstance(report, SurveyReport)
    assert report.ci == (
        SurveyedCi(
            WORKFLOW,
            31,
            "uv run ruff format --check src/",
            "workflow",
            True,
            "",
            FORMAT,
            ("src/a.py",),
        ),
        SurveyedCi(WORKFLOW, 32, "make", "workflow", False, "make"),
    )
    assert report.tests[0].ci_run_by == (1,)
    unit = report.commands[0]
    assert (unit.tool, unit.config, unit.eaten) == ("pytest", "pyproject.toml", "tests/unit")
    assert unit.gate == CoverageGate("--cov", "pyproject.toml", "80", "a.cfg")


@pytest.mark.parametrize(
    ("path", "value", "reason"),
    [
        (("ci",), {}, "ci no es una lista"),
        (("ci", 0, "status"), "x", "el paso de la CI 0 tiene un estado que no existe: 'x'"),
        (("ci", 0, "line"), 0, "el paso de la CI 0.line no es un número válido: 0"),
        (("ci", 0, "file"), "/etc/x", "el paso de la CI 0.file sale del árbol: '/etc/x'"),
        (("ci", 0, "covers"), ["../x"], "el paso de la CI 0.covers sale del árbol: '../x'"),
        (("ci", 1, "reason"), "", "el paso de la CI 1.reason no es un texto"),
        (
            ("tests", 0, "ci_run_by"),
            [2],
            "el fichero de tests 0.ci_run_by nombra un paso de la CI que no se le dio",
        ),
        (("commands", 0, "gate"), {"cov": "x"}, "el comando 0.gate.addopts no es un texto"),
    ],
)
def test_a_ci_that_cant_be_what_the_survey_wrote_is_invalid(
    path: tuple[str | int, ...], value: object, reason: str
) -> None:
    data: Any = copy.deepcopy(CI_GOOD)
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    assert survey_report(data) == InvalidReport(reason)


# F82 (ADR-0019): the project's own metadata, which the verification registers without installing.
METADATA = {
    "name": "agentix",
    "version": "0.0.0",
    "groups": {"x.plugins": 2, "console_scripts": 1},
    "unread": [],
}


def test_the_metadata_the_survey_saw_is_read_as_a_fact() -> None:
    report = survey_report({**GOOD, "metadata": METADATA})

    assert isinstance(report, SurveyReport)
    assert report.metadata == ProjectMetadata(
        "agentix", "0.0.0", (("console_scripts", 1), ("x.plugins", 2))
    )


def test_why_there_is_no_metadata_is_read_as_a_fact() -> None:
    report = survey_report({**GOOD, "metadata": {"problem": "sale de git", "fix": "dale una"}})

    assert isinstance(report, SurveyReport)
    assert report.metadata == ProjectMetadata(problem="sale de git", fix="dale una")
    alone = survey_report({**GOOD, "metadata": {"problem": "sale de git"}})
    assert isinstance(alone, SurveyReport)
    assert alone.metadata == ProjectMetadata(problem="sale de git")


def test_a_project_uv_wouldnt_install_is_read_as_having_none() -> None:
    report = survey_report({**GOOD, "metadata": {"not_installed": "sin [build-system]"}})

    assert isinstance(report, SurveyReport)
    assert report.metadata == ProjectMetadata(not_installed="sin [build-system]")
    assert metadata_facts(report.metadata) == (
        "sin metadata del proyecto: sin [build-system]",
        (),
    )


def test_a_survey_that_says_nothing_of_metadata_has_none() -> None:
    report = survey_report(GOOD)

    assert isinstance(report, SurveyReport)
    assert report.metadata is None


@pytest.mark.parametrize(
    ("metadata", "reason"),
    [
        ([], "metadata no es un objeto"),
        ({"problem": ""}, "metadata.problem no es un texto"),
        ({"problem": "x", "fix": 1}, "metadata.fix no es un texto"),
        ({"not_installed": ""}, "metadata.not_installed no es un texto"),
        ({**METADATA, "name": ""}, "metadata.name no es un texto"),
        ({**METADATA, "version": 1}, "metadata.version no es un texto"),
        ({**METADATA, "groups": []}, "metadata.groups no es un objeto"),
        (
            {**METADATA, "groups": {"g": 0}},
            "metadata.groups.g no es un número válido: 0",
        ),
        ({**METADATA, "unread": "scripts"}, "metadata.unread no es una lista"),
        ({**METADATA, "unread": [""]}, "metadata.unread no es un texto"),
    ],
)
def test_metadata_that_cant_be_what_the_survey_wrote_is_invalid(
    metadata: object, reason: str
) -> None:
    assert survey_report({**GOOD, "metadata": metadata}) == InvalidReport(reason)


def test_registered_metadata_is_a_pass_with_its_name_version_and_groups() -> None:
    found = ProjectMetadata("agentix", "0.0.0", (("console_scripts", 1), ("x.plugins", 2)))

    assert metadata_facts(found) == (
        "metadata del proyecto agentix 0.0.0, con console_scripts (1), x.plugins (2)",
        (),
    )
    assert metadata_facts(ProjectMetadata("x", "1")) == ("metadata del proyecto x 1", ())


def test_metadata_that_isnt_registered_warns_why_and_how_to_fix_it() -> None:
    found = ProjectMetadata(problem="la versión sale de git", fix="dale una estática")

    assert metadata_facts(found) == (
        None,
        (
            "La metadata del proyecto no está en la verificación: la versión sale de git. Si el "
            "código lee la suya (su versión, sus entry points), ahí fallará o no encontrará nada: "
            "dale una estática. Si no la lee, no pasa nada.",
        ),
    )
    assert metadata_facts(ProjectMetadata(problem="x"))[1] == (
        "La metadata del proyecto no está en la verificación: x. Si el código lee la suya (su "
        "versión, sus entry points), ahí fallará o no encontrará nada. Si no la lee, no pasa nada.",
    )


def test_entry_points_left_to_the_backend_warn_that_they_are_missing() -> None:
    found = ProjectMetadata("x", "1", (("console_scripts", 1),), unread=("scripts", "entry-points"))

    assert metadata_facts(found) == (
        None,
        (
            "La metadata del proyecto (x 1) está en la verificación sin scripts, entry-points: el "
            "proyecto los deja a su backend, y Lictor no los lee sin ejecutarlo.",
        ),
    )


def test_without_a_survey_of_metadata_nothing_is_told() -> None:
    assert metadata_facts(None) == (None, ())


def test_a_plugin_that_changes_a_run_of_the_tests_is_named_only_when_it_loads() -> None:
    """S11: every installed pytest plugin acts in every command; some change the order."""
    noted = {"pytest_randomly": "baraja el orden", "random_order.plugin": "baraja"}

    assert noted_plugins(noted, ("pytest_bdd.plugin", "pytest_randomly")) == (
        "El plugin pytest_randomly está instalado y carga en cada orden de pytest de la "
        "verificación: baraja el orden. Si no lo quieres ahí, ponlo en un grupo de dependencias "
        "que no se instale por defecto.",
    )
    assert noted_plugins(noted, ()) == ()
