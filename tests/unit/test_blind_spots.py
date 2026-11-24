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
    missing_plugins,
    program_problems,
)
from lictor.domain.reports import InvalidReport
from lictor.domain.survey import (
    CodeUser,
    LaunchedProgram,
    LoadedFile,
    ProjectImport,
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
    }
    assert blind_spots_record(skipped) == {
        "skipped": "no comprobado: no hay código del proyecto en src/",
        "spots": [],
        "lines": [],
        "accepted": 0,
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
