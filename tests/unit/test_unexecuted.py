"""What a run changed that no control executed (H2 of 0.20.0): the rule, case by case."""

import pytest

from lictor.domain.blind_spots import BlindSpot, BlindSpots, StartRules
from lictor.domain.survey import (
    CodeUser,
    LoadedFile,
    SurveyedCommand,
    SurveyedTests,
    SurveyReport,
)
from lictor.domain.unexecuted import (
    CODE_NOTE,
    FinalTree,
    Unexecuted,
    UnexecutedChanges,
    dotted_test_module,
    files_with_cases,
    unexecuted_changes,
    unexecuted_from_record,
    unexecuted_lines,
    unexecuted_record,
)

_REPORT = SurveyReport(
    code_dir="src",
    modules=("pkg",),
    commands=(
        SurveyedCommand("unit", True, tests=True, paths=("tests",), selects=True),
        SurveyedCommand("lint", True, covers=("src/pkg/a.py", "scripts/visto.py")),
        SurveyedCommand("raro", True, tests=True, paths=("tests/otros",)),
    ),
    tests=(
        SurveyedTests("tests/unit/test_a.py", 3, run_by=("unit",)),
        SurveyedTests("tests/integration/test_marcado.py", 2, run_by=("unit",)),
        SurveyedTests("tests/otros/test_c.py", 1, run_by=("raro",)),
        SurveyedTests("tests/fuera/test_d.py", 1),
    ),
    loaded=(LoadedFile("tests/conftest.py", "conftest", ("unit",)),),
    code_users=(CodeUser("examples/uso.py", (), ()),),
)
_SPOTS = BlindSpots(
    spots=(
        BlindSpot("sin_control", ("tests/fuera/test_d.py",), "m"),
        BlindSpot("cargado", ("tests/conftest.py",), "m"),
        BlindSpot("sin_lector", ("examples/uso.py",), "m"),
        BlindSpot("sin_cambiar", ("tests/unit/test_a.py",), "m"),
    )
)
_RULES = StartRules(
    writable=("src/", "tests/"), driver=("tests/acceptance/",), accepted=("docs/aceptado/",)
)
_PRESENT = frozenset(
    {
        "src/pkg/a.py",
        "tests/unit/test_a.py",
        "tests/integration/test_marcado.py",
        "tests/otros/test_c.py",
        "tests/fuera/test_d.py",
        "tests/conftest.py",
        "examples/uso.py",
        "scripts/visto.py",
        "scripts/suelto.py",
        "README.md",
        "docs/aceptado/a.md",
    }
)
# `unit` ran cases of test_a only; `raro` leaves no JUnit to read.
_RAN = {"unit": frozenset({"tests/unit/test_a.py"}), "raro": None}


def _kinds(changed: list[str], report: SurveyReport = _REPORT) -> dict[str, str]:
    found = unexecuted_changes(changed, FinalTree(report, _SPOTS, _PRESENT), _RULES, _RAN)
    return {f.path: f.kind for f in found.files}


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        # R1: named by `unit`, which runs `-m "not integration"`, and no case of it ran.
        ("tests/integration/test_marcado.py", "sin_ejecutar"),
        ("tests/unit/test_a.py", None),  # ran; out of reach, but executed
        ("tests/otros/test_c.py", None),  # no JUnit to read: the survey is trusted
        ("tests/fuera/test_d.py", "sin_control"),
        ("tests/conftest.py", "cargado"),
        ("examples/uso.py", "sin_lector"),
        ("scripts/visto.py", None),  # a static check reads it
        ("scripts/suelto.py", "sin_leer"),
        ("README.md", "no_es_codigo"),
        ("src/pkg/a.py", None),
    ],
)
def test_each_changed_file_is_judged_by_the_start_contracts_rule(
    path: str, kind: str | None
) -> None:
    assert _kinds([path]).get(path) == kind


def test_deleted_and_accepted_go_apart_and_code_is_said_once() -> None:
    found = unexecuted_changes(
        ["src/pkg/a.py", "src/pkg/borrado.py", "docs/aceptado/a.md"],
        FinalTree(_REPORT, _SPOTS, _PRESENT),
        _RULES,
        _RAN,
    )

    assert found == UnexecutedChanges(
        files=(), deleted=("src/pkg/borrado.py",), accepted=1, notes=(CODE_NOTE,)
    )


def test_with_a_command_nobody_understands_no_test_is_judged_unexecuted() -> None:
    report = SurveyReport(
        code_dir="src",
        modules=("pkg",),
        commands=(*_REPORT.commands, SurveyedCommand("make", False, reason="make")),
        tests=_REPORT.tests,
    )

    found = unexecuted_changes(
        ["tests/integration/test_marcado.py"], FinalTree(report, _SPOTS, _PRESENT), _RULES, _RAN
    )

    assert found.files == ()
    assert "no se sabe qué ejecutan" in found.notes[-1]


@pytest.mark.parametrize(
    ("classnames", "expected"),
    [
        (["tests.unit.test_a"], {"tests/unit/test_a.py"}),
        (["tests.unit.test_a.TestClase"], {"tests/unit/test_a.py"}),
        (["tests.unit.test_ab"], set()),  # another module that starts alike
        ([], set()),
    ],
)
def test_a_junit_case_names_its_file_by_class(classnames: list[str], expected: set[str]) -> None:
    assert files_with_cases(classnames, ["tests/unit/test_a.py"]) == expected


def test_module_is_the_dotted_path() -> None:
    assert dotted_test_module("tests/unit/test_a.py") == "tests.unit.test_a"


def test_the_record_reads_back_and_the_lines_group_by_kind() -> None:
    found = UnexecutedChanges(
        files=(
            Unexecuted("README.md", "no_es_codigo"),
            Unexecuted("tests/integration/test_m.py", "sin_ejecutar"),
        ),
        deleted=("src/x.py",),
        accepted=2,
        notes=(CODE_NOTE,),
    )
    record = unexecuted_record(found, "a" * 40, "b" * 40)

    assert unexecuted_from_record(record) == found
    assert unexecuted_lines(found) == [
        "- Tests que un control nombra y no ejecutó: ningún caso suyo en su informe: "
        "`tests/integration/test_m.py`",
        "- No es Python: ningún control lo lee: `README.md`",
        "- Borrados: `src/x.py`",
        "- Aceptados en blind_spots.accepted, solo contados: 2",
        f"- {CODE_NOTE}",
    ]


def test_a_problem_is_kept_as_such() -> None:
    record = unexecuted_record("el survey falló", "a" * 40, "b" * 40)

    assert unexecuted_from_record(record) == "el survey falló"
    assert unexecuted_from_record({"files": "x"}) is None
    assert unexecuted_from_record(None) is None


def test_nothing_unexecuted_says_so() -> None:
    assert unexecuted_lines(UnexecutedChanges()) == [
        "- Nada: cada fichero que cambió lo ejecuta o lo lee algún control."
    ]
