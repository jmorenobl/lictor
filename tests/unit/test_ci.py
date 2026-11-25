"""A step of the project's CI, and how the person accepts one by file and command (F76)."""

import pytest

from lictor.domain.ci import CiStep, ci_entry_matches, is_ci_entry

FILE = ".github/workflows/tests.yml"
RUN = "uv run --group docs mkdocs build --strict"


def test_a_step_says_where_it_is_written() -> None:
    assert CiStep(FILE, 38, RUN, "workflow").place() == f"{FILE}:38"


@pytest.mark.parametrize(
    ("entry", "matches"),
    [
        (f"{FILE}: {RUN}", True),
        (f"  {FILE} :   uv run  --group docs mkdocs build   --strict ", True),
        (f"{FILE}: uv run --group docs mkdocs build", False),
        (f"{FILE}: {RUN} --quiet", False),
        (f".github/workflows/docs.yml: {RUN}", False),
        # A path alone accepts no step: one added later to that file still warns.
        (FILE, False),
        (f"{FILE}:{RUN}", False),
    ],
)
def test_a_step_is_accepted_by_its_file_and_the_words_of_its_command(
    entry: str, matches: bool
) -> None:
    assert ci_entry_matches(entry, FILE, RUN) is matches


@pytest.mark.parametrize(
    ("entry", "is_step"),
    [(f"{FILE}: {RUN}", True), ("tests/integration/", False), ("a:b", False)],
)
def test_an_entry_names_a_step_only_with_a_colon_and_a_space(entry: str, is_step: bool) -> None:
    assert is_ci_entry(entry) is is_step
