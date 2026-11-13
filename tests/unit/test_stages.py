from dataclasses import dataclass

import pytest

from lictor.domain.run_branches import integration_hint
from lictor.domain.stages import (
    check_controls_warning,
    check_stage_note,
    committed_greens,
    controls_of_stage,
    final_reason,
    integration_with_check_stage,
    spoken_list,
    with_sentence,
)


@dataclass(frozen=True)
class _Control:
    id: str
    stage: str = "attempt"


def test_each_stage_keeps_only_its_controls_in_their_order() -> None:
    controls = [
        _Control("lint"),
        _Control("mutation", "check"),
        _Control("unit"),
        _Control("audit", "check"),
    ]

    assert [c.id for c in controls_of_stage(controls, "attempt")] == ["lint", "unit"]
    assert [c.id for c in controls_of_stage(controls, "check")] == ["mutation", "audit"]


def test_no_controls_of_a_stage_is_an_empty_list() -> None:
    assert controls_of_stage([_Control("lint")], "check") == []


@pytest.mark.parametrize(
    ("names", "spoken"),
    [
        ([], ""),
        (["mutation"], "mutation"),
        (["mutation", "audit"], "mutation y audit"),
        (["a", "b", "c"], "a, b y c"),
    ],
)
def test_names_are_joined_as_spanish_says_them(names: list[str], spoken: str) -> None:
    assert spoken_list(names) == spoken


def test_a_green_from_before_is_not_one_the_run_committed() -> None:
    assert not committed_greens({"A-001": "green", "A-002": "pending"}, ["A-001"])


@pytest.mark.parametrize("status", ["pending", "quarantined"])
def test_a_scenario_that_isnt_green_was_not_committed(status: str) -> None:
    assert not committed_greens({"A-001": status}, [])


def test_a_new_green_is_one_the_run_committed() -> None:
    assert committed_greens({"A-001": "green", "A-002": "green"}, ["A-001"])


def test_the_note_names_the_check_controls_and_says_to_run_check() -> None:
    assert check_stage_note(["mutation", "audit"]) == (
        "Quedan sin comprobar mutation y audit, que solo corren en `lictor check`: "
        "ejecútalo antes de integrar."
    )


def test_the_hint_with_check_controls_names_branch_worktree_and_how_to_integrate() -> None:
    assert integration_with_check_stage("lictor/x", "/w/x", ["mutation", "audit"]) == (
        "El resultado está en la rama lictor/x, en `/w/x`. Quedan sin comprobar mutation y "
        "audit, que solo corren en `lictor check`: ejecútalo en ese worktree, o deja que lo "
        "haga la CI de la pull request, antes de integrarlo con `git merge lictor/x` o con una "
        "pull request."
    )


def test_doctor_warns_naming_the_check_controls() -> None:
    assert check_controls_warning(["mutation", "audit"]) == (
        "Controles que solo corren en `lictor check`: mutation y audit. Un run no los "
        "ejecuta: solo protegen lo que integras si tu CI ejecuta `lictor check`."
    )


@pytest.mark.parametrize(
    ("reason", "joined"),
    [
        ("Hecho", "Hecho. Frase."),
        ("Hecho.", "Hecho. Frase."),
        ("Hecho. ", "Hecho. Frase."),
        ("", "Frase."),
    ],
)
def test_a_sentence_follows_the_reason_after_one_full_stop(reason: str, joined: str) -> None:
    assert with_sentence(reason, "Frase.") == joined


def test_a_reason_that_already_ends_with_the_sentence_stays_as_it_is() -> None:
    assert with_sentence("Hecho. Frase.", "Frase.") == "Hecho. Frase."


def test_finishing_twice_tells_what_is_left_once() -> None:
    once = final_reason("Hecho", check_ids=["mutation"], committed=True)

    assert final_reason(once, check_ids=["mutation"], committed=True) == once


def test_without_check_controls_the_reason_stays_as_it_was() -> None:
    assert final_reason("Hecho", check_ids=[], committed=True) == "Hecho"


def test_without_commits_nothing_is_left_to_check() -> None:
    assert final_reason("Spec inválida", check_ids=["mutation"], committed=False) == (
        "Spec inválida"
    )


def test_check_controls_after_a_commit_add_the_note() -> None:
    assert final_reason("Hecho", check_ids=["mutation"], committed=True) == (
        f"Hecho. {check_stage_note(['mutation'])}"
    )


def test_in_a_linked_worktree_without_check_controls_the_hint_is_todays() -> None:
    reason = final_reason("Hecho", check_ids=[], committed=True, branch="lictor/x", worktree="/w")

    assert reason == f"Hecho. {integration_hint('lictor/x')}"


def test_in_a_linked_worktree_without_commits_the_hint_is_todays() -> None:
    reason = final_reason(
        "Hecho", check_ids=["mutation"], committed=False, branch="lictor/x", worktree="/w"
    )

    assert reason == f"Hecho. {integration_hint('lictor/x')}"


def test_in_a_linked_worktree_the_note_goes_into_the_hint() -> None:
    reason = final_reason(
        "Hecho", check_ids=["mutation"], committed=True, branch="lictor/x", worktree="/w"
    )

    assert reason == f"Hecho. {integration_with_check_stage('lictor/x', '/w', ['mutation'])}"
