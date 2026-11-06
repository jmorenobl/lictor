"""0.2.0, F07: `doctor` validates the features with the rule `load_spec` uses.

In the greenfield pass `romanos`, the only way to know whether a feature would load before the
run was to call the Gherkin parser by hand. A problem was found in `load_spec`, one at a time.
"""

from pathlib import Path

from typer.testing import CliRunner

from lictor.cli import app
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    git,
    green_ids,
    run_lictor,
    write,
)

BROKEN = """# language: es
Característica: IVA

  @IVA-001
  Escenario: IVA general
    Dado una factura con importe 100
    Cuando calculo el IVA general
    Entonces el total es 121

  @IVA-001
  Escenario: IVA repetido
    Dado una factura con importe 100
    Cuando calculo el IVA general
    Entonces el total es 121

  Escenario: Sin ID
    Dado una factura con importe 100
    Cuando calculo el IVA general
    Entonces el total es 121

  @IVA-004
  Esquema del escenario: Varios importes
    Dado una factura con importe <importe>
    Cuando calculo el IVA general
    Entonces el total es <total>

    Ejemplos:
      | importe | total |
      | 100     | 121   |
"""

EXPECTED = [
    "✗ Escenarios: features/iva.feature:11: el escenario «IVA repetido» usa el ID @IVA-001, "
    "que ya tiene features/iva.feature:5",
    "✗ Escenarios: features/iva.feature:16: el escenario «Sin ID» no tiene ID",
    "✗ Escenarios: features/iva.feature:22: el escenario «Varios importes» es un Esquema del "
    "escenario (Scenario Outline), que Lictor no admite",
]
EMPTY = "⚠ Escenarios: features/vacio.feature: está vacío"


def _break_the_features(root: Path) -> None:
    write(root, "features/iva.feature", BROKEN)
    write(root, "features/vacio.feature", "# language: es\n# pendiente\n")
    git(root, "add", "features")
    git(root, "commit", "-qm", "test: features rotos")


def test_f07_doctor_lists_every_problem_of_the_features(make_project: ProjectFactory) -> None:
    root = make_project()
    _break_the_features(root)

    result = CliRunner().invoke(app, ["doctor", "--cwd", str(root)])

    assert result.exit_code == 10, result.output
    for line in [*EXPECTED, EMPTY]:
        assert line in result.output
    assert result.output.count("✗ Escenarios:") == len(EXPECTED)


def test_f07_doctor_passes_valid_features(make_project: ProjectFactory) -> None:
    root = make_project()

    result = CliRunner().invoke(app, ["doctor", "--cwd", str(root)])

    assert "✓ Escenarios" in result.output


def test_f07_run_refuses_the_same_problems_before_any_agent_call(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    _break_the_features(root)
    agent = Agent()

    assert run_lictor(root, agent) == Outcome(10)

    assert agent.calls == []
    reason = events_of(root, "run.finished")[-1]["data"]["reason"]
    for line in EXPECTED:
        assert line.removeprefix("✗ Escenarios: ") in reason
    assert "vacio.feature" not in reason


def test_f07_an_empty_feature_is_a_warning_and_the_run_goes_on(
    make_project: ProjectFactory,
) -> None:
    """A draft left in `features/` demands nothing: `doctor` tells, and nothing stops."""
    root = make_project()
    write(root, "features/borrador.feature", "# language: es\n# pendiente\n")
    write(root, "features/sin_escenarios.feature", "# language: es\nCaracterística: Pendiente\n")
    git(root, "add", "features")
    git(root, "commit", "-qm", "docs: borradores")

    result = CliRunner().invoke(app, ["doctor", "--cwd", str(root)])

    assert result.exit_code == 0, result.output
    assert "⚠ Escenarios: features/borrador.feature: está vacío" in result.output
    assert (
        "⚠ Escenarios: features/sin_escenarios.feature:2: la característica «Pendiente» "
        "no tiene escenarios" in result.output
    )
    assert "✓ Escenarios" not in result.output
    assert "con avisos (⚠)" in result.output
    assert run_lictor(root, Agent()) == Outcome(0)
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]


def test_f07_only_empty_features_stop_the_run(make_project: ProjectFactory) -> None:
    root = make_project()
    git(root, "rm", "-q", "features/iva.feature")
    write(root, "features/borrador.feature", "# language: es\n# pendiente\n")
    git(root, "add", "features")
    git(root, "commit", "-qm", "docs: solo un borrador")

    result = CliRunner().invoke(app, ["doctor", "--cwd", str(root)])

    assert result.exit_code == 10, result.output
    assert "✗ Escenarios: features/: no hay ningún escenario" in result.output
    assert "⚠ Escenarios: features/borrador.feature: está vacío" in result.output
