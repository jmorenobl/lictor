"""Where a run of the index stands, and what `run` and `retire` may do with it (G8 of 0.23.2)."""

import pytest

from lictor.domain.run_standing import (
    Standing,
    WorktreeFacts,
    broken_reason,
    counts_for_resume,
    foreign_leftovers,
    leftovers_problem,
    run_standing,
    standing_note,
    unlinked_note,
)

LINKED_OPEN = WorktreeFacts(exists=True, link="linked", checkpoint="open")


@pytest.mark.parametrize(
    ("facts", "standing"),
    [
        (WorktreeFacts(exists=False), "gone"),
        (WorktreeFacts(exists=False, retiring=True), "retiring"),
        (WorktreeFacts(exists=True, retiring=True, held=True), "retiring"),
        (WorktreeFacts(exists=True, held=True, link="orphan", checkpoint="unreadable"), "goes_on"),
        (LINKED_OPEN, "goes_on"),
        (WorktreeFacts(exists=True, link="linked", checkpoint="none"), "not_started"),
        (WorktreeFacts(exists=True, link="linked", checkpoint="finished"), "finished"),
        (WorktreeFacts(exists=True, link="linked", checkpoint="unreadable"), "broken"),
        (WorktreeFacts(exists=True, link="orphan", checkpoint="finished"), "broken"),
        (WorktreeFacts(exists=True, link="orphan", checkpoint="open"), "broken"),
        (WorktreeFacts(exists=True, link="unlinked", checkpoint="open"), "unlinked"),
        (WorktreeFacts(exists=True, link="unlinked", checkpoint="unreadable"), "unlinked"),
    ],
)
def test_each_run_stands_by_its_facts(facts: WorktreeFacts, standing: Standing) -> None:
    assert run_standing(facts) == standing


@pytest.mark.parametrize(
    ("facts", "counts"),
    [
        (LINKED_OPEN, True),
        (WorktreeFacts(exists=True, held=True, link="orphan"), True),
        (WorktreeFacts(exists=True, link="linked", checkpoint="none"), True),
        (WorktreeFacts(exists=True, link="linked", checkpoint="finished"), False),
        # Before 0.23.2 a worktree whose checkpoint couldn't be read counted as one that goes on.
        (WorktreeFacts(exists=True, link="linked", checkpoint="unreadable"), False),
        (WorktreeFacts(exists=True, link="orphan", checkpoint="finished"), False),
        (WorktreeFacts(exists=True, link="orphan", checkpoint="open"), False),
        (WorktreeFacts(exists=True, retiring=True, checkpoint="open"), False),
        (WorktreeFacts(exists=False), False),
        # Counted so that choosing it refuses: `lictor run` must not start another one and spend.
        (WorktreeFacts(exists=True, link="unlinked", checkpoint="open"), True),
        (WorktreeFacts(exists=True, link="unlinked", checkpoint="none"), True),
        (WorktreeFacts(exists=True, link="unlinked", checkpoint="finished"), False),
    ],
)
def test_lictor_run_resumes_only_what_can_go_on(facts: WorktreeFacts, counts: bool) -> None:
    assert counts_for_resume(facts) is counts


def test_a_broken_run_says_why() -> None:
    assert broken_reason(WorktreeFacts(exists=True, link="orphan")) == (
        "git ya no conoce su worktree"
    )
    unreadable = WorktreeFacts(exists=True, checkpoint="unreadable", problem="file is not a db")
    assert broken_reason(unreadable) == "su checkpoint no se puede leer (file is not a db)"


@pytest.mark.parametrize("standing", ["gone", "goes_on", "not_started", "finished"])
def test_a_run_that_can_go_on_or_be_retired_has_no_note(standing: Standing) -> None:
    facts = WorktreeFacts(exists=True)
    assert standing_note(standing, facts, name="x", worktree="/w", checkout="/c") is None


def test_each_run_that_cant_go_on_names_its_way_out() -> None:
    orphan = WorktreeFacts(exists=True, link="orphan")

    def note(standing: Standing) -> str | None:
        return standing_note(standing, orphan, name="h9", worktree="/w/h9", checkout="/c")

    assert note("retiring") == (
        "la ejecución de la rama lictor/h9 se estaba retirando y el retiro quedó a medias: "
        "termínalo con `lictor retire h9`."
    )
    assert note("broken") == (
        "la ejecución de la rama lictor/h9 no se puede seguir: git ya no conoce su worktree "
        "(/w/h9). Retírala con `lictor retire h9`."
    )
    assert note("unlinked") == unlinked_note("lictor/h9", "/w/h9", "/c")
    assert "`git -C /c worktree repair /w/h9`" in unlinked_note("lictor/h9", "/w/h9", "/c")
    assert "No se ha tocado nada" in unlinked_note("lictor/h9", "/w/h9", "/c")


def test_what_lictor_and_git_left_is_never_foreign() -> None:
    entries = [
        (".git", "file"),
        (".lictor", "dir"),
        (".lictor/verify-env", "dir"),
        (".lictor/verify-env/gomod/x", "file"),
        (".lictor/checkpoints.sqlite", "file"),
    ]
    assert foreign_leftovers(entries, None) == []
    assert foreign_leftovers(entries, set()) == []


def test_a_git_directory_is_someones_repository() -> None:
    """V5 of the second review: only a `.git` file is the pointer git leaves behind."""
    assert foreign_leftovers([(".git", "dir"), (".git/HEAD", "file")], None) == [".git"]


def test_unmarked_only_lictors_own_may_go() -> None:
    entries = [(".lictor", "dir"), ("b", "file"), ("src", "dir"), ("src/x.go", "file")]
    assert foreign_leftovers(entries, None) == ["b", "src"]


def test_marked_what_the_branch_has_may_go_and_nothing_else() -> None:
    entries = [
        (".git", "file"),
        ("b", "file"),
        ("nif", "dir"),
        ("nif/nif.go", "file"),
        ("nif/sub", "dir"),
        ("nif/sub/a.go", "file"),
        ("nif/__pycache__", "dir"),
        ("nif/__pycache__/x.pyc", "file"),
        ("enlace", "link"),
        ("z", "file"),
    ]
    tracked = {"b", "nif/nif.go", "nif/sub/a.go", "enlace"}
    assert foreign_leftovers(entries, tracked) == ["nif/__pycache__", "z"]


def test_a_tracked_file_doesnt_make_a_directory_of_the_same_name_the_branchs() -> None:
    assert foreign_leftovers([("b", "dir"), ("b/c", "file")], {"b"}) == ["b"]


def test_the_leftovers_message_lists_five_and_says_whose_they_may_be() -> None:
    foreign = [f"f{n}" for n in range(7)]
    marked = leftovers_problem("/w", foreign, marked=True)
    assert "queda f0, f1, f2, f3, f4 y 2 más" in marked
    assert "No son de la rama: si son ignorados que git iba a borrar" in marked
    assert "No se ha tocado nada" in marked
    unmarked = leftovers_problem("/w", ["b"], marked=False)
    assert "queda b. Lictor no dejó este retiro a medias" in unmarked
