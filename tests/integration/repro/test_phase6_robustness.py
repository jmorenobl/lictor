"""Phase 6 (A8, B1): a misbehaving process can't hang or crash a run."""

import sys
from pathlib import Path

from lictor.adapters.runner.subprocess_runner import SubprocessRunner
from lictor.adapters.spec.gherkin_source import GherkinSource
from lictor.domain.model import AgentTask, Scenario, ScenarioId
from tests.conftest import DETACHED_LIFE_S, Detached
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    all_controls,
    good_impl,
    run_lictor,
    write,
)

BINARY_OUTPUT_TEST = (
    "import atexit\nimport os\n\n"
    "atexit.register(lambda: os.write(1, b'\\xff\\xfe'))\n\n\n"
    "def test_b():\n    assert True\n"
)
DOCSTRING_FEATURE = '''# language: es
Característica: X

  @IVA-1
  Escenario: uno
    Dado a
      """
      Escenario: texto
      @OTRO
      """
    Entonces b

  @IVA-10
  Escenario: diez
    Dado secreto
'''


@all_controls
def test_e20_non_utf8_output_does_not_crash_the_run(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        write(task.cwd, "tests/unit/test_bin.py", BINARY_OUTPUT_TEST)

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)


def test_d21_timeout_is_bounded_with_an_orphan_holding_stdout(
    tmp_path: Path, detached: Detached
) -> None:
    marker = tmp_path / "terminó"
    code = (
        detached.code("huérfano")
        + f"import time\ntime.sleep({DETACHED_LIFE_S})\nopen({str(marker)!r}, 'w')\n"
    )

    out = SubprocessRunner().run([sys.executable, "-c", code], cwd=tmp_path, timeout_s=1)

    assert out.timed_out is True
    assert not marker.exists(), "debía matar la orden al vencer el plazo"
    assert detached.alive("huérfano"), "esperó al huérfano que sujetaba stdout"


def test_d27_gherkin_docstring_does_not_truncate_the_scenario(tmp_path: Path) -> None:
    (tmp_path / "a.feature").write_text(DOCSTRING_FEATURE, encoding="utf-8")
    scenario = Scenario(ScenarioId("IVA-1"), "uno", "a.feature", 5)

    extracted = GherkinSource().scenario_text(tmp_path, scenario)

    assert "Entonces b" in extracted
    assert "Dado secreto" not in extracted
