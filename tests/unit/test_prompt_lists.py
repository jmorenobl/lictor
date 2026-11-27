"""The rules of the prompts that name a list, said with and without it (0.23.1)."""

import pytest

from lictor.domain.prompt_lists import (
    Role,
    forbidden_rule,
    greens_rule,
    protected_rule,
    suppressions_rule,
)

ROLES: list[Role] = ["driver", "implement", "refactor"]


def test_suppressions_with_a_list_name_it_and_what_each_role_hears() -> None:
    assert suppressions_rule(["# noqa", "//nolint"], "driver") == (
        "- No añadas supresiones: # noqa, //nolint. El driver queda sellado: si las lleva, se "
        "rechaza.\n"
    )
    assert suppressions_rule(["# noqa"], "implement") == (
        "- No añadas supresiones: # noqa. Mover o juntar las que ya hay no cuenta, aunque sea a "
        "otro fichero; cambiar su texto, sí.\n"
    )
    assert suppressions_rule(["# noqa"], "refactor") == suppressions_rule(["# noqa"], "implement")


@pytest.mark.parametrize("role", ROLES)
def test_without_suppressions_the_rule_says_what_one_is(role: Role) -> None:
    assert suppressions_rule([], role) == (
        "- No añadas supresiones: ningún comentario ni llamada que calle un aviso o salte un "
        "test.\n"
    )


def test_the_forbidden_paths_of_each_role() -> None:
    assert forbidden_rule(["a", "b"], "driver") == (
        "- No crees ni modifiques, en ninguna carpeta: a, b. Se descartan: lo que hay ahí\n"
        "  lo cambia solo una persona.\n"
    )
    assert forbidden_rule(["a"], "driver", ["conftest.py"], ["tests/acceptance/", "x/"]) == (
        "- No crees ni modifiques, en ninguna carpeta: a, salvo conftest.py dentro de "
        "tests/acceptance/, x/. Se descartan: lo que hay ahí\n  lo cambia solo una persona.\n"
    )
    assert forbidden_rule(["a", "b"], "implement") == (
        "- Tampoco crees ni modifiques, en ninguna carpeta: a, b.\n"
        "  Lo que hay ahí lo cambia solo una persona.\n"
    )
    assert forbidden_rule(["a"], "refactor") == (
        "- Tampoco crees ni modifiques, en ninguna carpeta: a.\n"
    )


@pytest.mark.parametrize("role", ROLES)
def test_without_forbidden_paths_there_is_no_rule(role: Role) -> None:
    assert forbidden_rule([], role, ["conftest.py"], ["tests/"]) == ""


def test_the_greens_of_each_role() -> None:
    assert greens_rule(["A-1", "A-2"], "driver") == (
        "- Los escenarios A-1, A-2 ya funcionan y sus tests deben pasar.\n"
    )
    assert greens_rule(["A-1"], "implement") == (
        "- Estos escenarios ya funcionan y deben seguir funcionando: A-1.\n"
    )
    assert greens_rule(["A-1", "A-2"], "refactor") == (
        "- No cambies el comportamiento. Todos estos escenarios funcionan y deben seguir "
        "funcionando: A-1, A-2.\n"
    )


def test_without_greens_only_the_refactor_keeps_a_rule() -> None:
    assert greens_rule([], "driver") == ""
    assert greens_rule([], "implement") == ""
    assert greens_rule([], "refactor") == "- No cambies el comportamiento.\n"


def test_the_driver_touches_nothing_protected_besides_its_paths() -> None:
    assert protected_rule(["features/", "lictor.json"]) == (
        "- No implementes la funcionalidad. No toques features/, lictor.json.\n"
    )
    assert protected_rule([]) == "- No implementes la funcionalidad.\n"
