"""How `doctor` prints each check: a ✓ that has something to tell says it after its name (F82)."""

import pytest

from lictor.application.usecases.preconditions import PreconditionCheck, PreconditionsReport
from lictor.cli import _echo_checks


def test_a_check_that_passed_prints_its_detail(capsys: pytest.CaptureFixture[str]) -> None:
    report = PreconditionsReport(
        checks=(
            PreconditionCheck("Dependencias de la verificación", detail="metadata de x 1"),
            PreconditionCheck("Árbol git limpio"),
        )
    )

    assert _echo_checks(report) == 0
    assert capsys.readouterr().out.splitlines()[:2] == [
        "✓ Dependencias de la verificación: metadata de x 1",
        "✓ Árbol git limpio",
    ]


def test_a_detail_is_told_beside_the_warnings(capsys: pytest.CaptureFixture[str]) -> None:
    check = PreconditionCheck("Dependencias", warnings=("falta x",), detail="metadata de x 1")

    _echo_checks(PreconditionsReport(checks=(check,)))

    assert capsys.readouterr().out.splitlines()[:2] == [
        "✓ Dependencias: metadata de x 1",
        "⚠ Dependencias: falta x",
    ]
