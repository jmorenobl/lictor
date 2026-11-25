"""ADR-0011, amended (B): only a Then proves that a test checks what the interface returns.

The code meets IVA-001 and IVA-002 before the first run, as in `test_v0_8_0_interface_impostor`.
The impostor alters only what the When and the Then get, and only a Then's assertion, or its own
error from the When on, proves the test. What can't be altered from the When on stops the run for
a person at once, telling the agent nothing of the impostor; a check in a When is another try.
"""

from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    GOOD_2,
    TOTAL_CHECK,
    Agent,
    Outcome,
    events_of,
    false_greens,
    green_ids,
    run_lictor,
)
from tests.integration.repro.test_v0_8_0_interface_impostor import (
    _code_already_meets,
    _driver_over_the_code,
)

# The interface returns an object the impostor can't alter: the test is right, and can't be told
# from one that checks nothing.
_AN_OBJECT = (
    GOOD_2.replace(
        '"""Interfaz."""\n',
        '"""Interfaz."""\n\n\nclass Total:\n    """Total."""\n\n'
        "    def __init__(self, valor: float) -> None:\n"
        "        self.valor = valor\n",
    )
    .replace("def general(importe: float) -> float:", "def general(importe: float) -> Total:")
    .replace("def reducido(importe: float) -> float:", "def reducido(importe: float) -> Total:")
    .replace("return round(importe * 1.21, 2)", "return Total(round(importe * 1.21, 2))")
    .replace("return round(importe * 1.10, 2)", "return Total(round(importe * 1.10, 2))")
)

# Each When computes the total and checks it itself; the Then only compares what the When left.
_CHECKED_IN_THE_WHEN = DRIVER.replace(
    "def general(importe):\n    return lambda: iva.general(importe)\n",
    "def general(importe):\n    total = iva.general(importe)\n"
    "    assert total == round(importe * 1.21, 2)\n    return lambda: total\n",
).replace(
    "def reducido(importe):\n    return lambda: iva.reducido(importe)\n",
    "def reducido(importe):\n    total = iva.reducido(importe)\n"
    "    assert total == round(importe * 1.10, 2)\n    return lambda: total\n",
)


def test_what_can_t_be_altered_from_the_when_on_stops_for_a_person_at_once(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    _code_already_meets(root, _AN_OBJECT)
    driver = DRIVER.replace(TOTAL_CHECK, "assert calculo().valor == esperado")
    agent = Agent(driver=_driver_over_the_code(driver))

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(10), agent.calls
    assert agent.calls == ["driver"]
    [rejected] = events_of(root, "driver.rejected")
    assert (rejected["data"]["reason"], rejected["data"]["final"]) == ("sin_prueba", True)
    error = str(rejected["data"]["error"])
    assert error.count("Este test no lo cambies") == 2
    # Its Then reads the scenario's value: the person reads where (ADR-0016).
    line = driver.splitlines().index("    assert calculo().valor == esperado") + 1
    place = f"tests/acceptance/test_iva.py:{line}"
    assert f"Lictor no pudo comprobar IVA-001, en {place}; IVA-002, en {place}" in error
    assert "Lee antes esos tests" in error
    assert "lictor register" not in error
    [measured] = events_of(root, "driver.measured")
    outcomes = {
        sid: (m["outcome"], m["interface"]) for sid, m in measured["data"]["interface"].items()
    }
    assert outcomes == {
        "IVA-001": ("solo_el_valor", "sin_alterar"),
        "IVA-002": ("solo_el_valor", "sin_alterar"),
    }
    assert green_ids(root) == []
    assert false_greens(root) == []


def test_a_check_in_a_when_is_another_try_that_says_where_the_check_goes(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    _code_already_meets(root)
    agent = Agent(driver=_driver_over_the_code(_CHECKED_IN_THE_WHEN))

    outcome = run_lictor(root, agent)

    assert outcome == Outcome(10), agent.calls
    assert agent.calls == ["driver", "driver"]
    rejected = events_of(root, "driver.rejected")
    assert [event["data"]["final"] for event in rejected] == [False, True]
    error = str(rejected[0]["data"]["error"])
    assert "falla, pero por una aserción de un Cuando (modo mas: " in error
    assert "la comprobación del resultado va en un paso Entonces" in error
    [first, _] = events_of(root, "driver.measured")
    assert first["data"]["interface"]["IVA-001"]["failed_step"] == {"mas": "when", "menos": "when"}
    assert green_ids(root) == []
    assert false_greens(root) == []
