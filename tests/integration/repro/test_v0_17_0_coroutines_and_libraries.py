"""F69 and F74 (ADR-0011, amendment of coroutines and libraries), with the real profile.

F69: an `async` interface the code already met was never wrapped, so its scenarios were rejected
as never calling it. Now a coroutine is wrapped, its result altered once awaited, and the
scenario goes green with no person. F74: a test that only runs a command, which a library
invokes, was told it never calls the interface; now it is told the truth, and the run stops for a
person at the first attempt instead of the second.
"""

from collections.abc import Callable
from pathlib import Path

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    GOOD_2,
    GOOD_3,
    MODULE_PATH,
    ORACLE,
    UNIT_TEST,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    run_lictor,
    write,
)


def _async(code: str) -> str:
    """The same interface, `async`: what an API such as pyverifactu's looks like."""
    return code.replace("def general", "async def general").replace(
        "def reducido", "async def reducido"
    )


ASYNC_ORACLE = {
    **ORACLE,
    "IVA-001": '__import__("asyncio").run(iva.general(100)) == 121',
    "IVA-002": '__import__("asyncio").run(iva.reducido(100)) == 110',
}

ASYNC_DRIVER = "import asyncio\n" + DRIVER.replace(
    "return lambda: iva.general(importe)", "return lambda: asyncio.run(iva.general(importe))"
).replace(
    "return lambda: iva.reducido(importe)", "return lambda: asyncio.run(iva.reducido(importe))"
)

# A command of the project, `python -m iva.cli <importe>`, which the driver runs through `runpy`.
CLI_PATH = "src/iva/cli.py"
CLI = "import sys\n\nimport iva\n\nprint(iva.general(float(sys.argv[1])))\n"
CLI_DRIVER = (
    "import contextlib\nimport io\nimport runpy\nimport sys\n"
    + DRIVER.replace(
        "return lambda: iva.general(importe)",
        "return lambda: _orden(importe)",
    )
    + "\n\ndef _orden(importe):\n"
    "    salida = io.StringIO()\n"
    "    sys.argv = ['iva', str(importe)]\n"
    "    with contextlib.redirect_stdout(salida):\n"
    "        runpy.run_module('iva.cli', run_name='__main__')\n"
    "    return float(salida.getvalue())\n"
)


def _code_already_meets(root: Path, code: str, *extra: tuple[str, str]) -> None:
    write(root, MODULE_PATH, code)
    for path, text in extra:
        write(root, path, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: IVA general y reducido, antes de Lictor")


def _driver(text: str) -> Callable[[AgentTask, int], None]:
    def write_driver(task: AgentTask, _attempt: int) -> None:
        write(task.cwd, DRIVER_PATH, text)
        write(task.cwd, "tests/unit/test_smoke.py", UNIT_TEST)

    return write_driver


def test_an_async_interface_the_code_already_meets_goes_green_without_a_person(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    _code_already_meets(root, _async(GOOD_2))

    def implement(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, _async(GOOD_3))

    agent = Agent(driver=_driver(ASYNC_DRIVER), impl=implement)

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(0), agent.calls
    assert agent.calls == ["driver", "IVA-003"]
    greens = {str(e["scenarioid"]): e["data"] for e in events_of(root, "scenario.green")}
    for sid in ("IVA-001", "IVA-002"):
        assert greens[sid]["proof"] == "interface"
    [measured] = events_of(root, "driver.measured")
    found = measured["data"]["interface"]["IVA-001"]
    assert found["outcome"] == "probado"
    assert found["calls"] == {"given": 0, "when": 0, "then": 1, "outside": 0}
    assert false_greens(root, ASYNC_ORACLE) == []


def test_a_command_a_library_runs_stops_for_a_person_at_the_first_attempt(
    make_project: ProjectFactory,
) -> None:
    """IVA-001 only runs the command; IVA-002 calls the interface and is proved."""
    root = make_project()
    _code_already_meets(root, GOOD_2, (CLI_PATH, CLI))
    agent = Agent(driver=_driver(CLI_DRIVER))

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(10), agent.calls
    assert agent.calls == ["driver"]  # no attempt of the driver would change it
    [rejected] = events_of(root, "driver.rejected")
    assert rejected["data"]["reason"] == "sin_prueba"
    assert rejected["data"]["final"] is True
    error = str(rejected["data"]["error"])
    assert "Lictor no pudo comprobar IVA-001, a través de runpy:" in error
    assert "no llama a la interfaz" not in error
    assert "lictor register IVA-001" in error
    [measured] = events_of(root, "driver.measured")
    found = measured["data"]["interface"]["IVA-001"]
    assert found["outcome"] == "a_traves"
    assert found["libraries"] == ["runpy"]
    assert found["via"]["when"] == 0
    assert found["via"]["then"] >= 1
    assert measured["data"]["interface"]["IVA-002"]["outcome"] == "probado"
