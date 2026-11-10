"""Unit tests for which `.gitignore` entries a file still lacks (F33)."""

import pytest

from lictor.domain.gitignore import missing_entries


@pytest.mark.parametrize(
    ("existing", "entry"),
    [
        (".venv", ".venv/"),
        (".venv/", ".venv/"),
        ("  .venv/  ", ".venv/"),
        ("*.py[oc]", "*.pyc"),
        ("*.py[cod]", "*.pyc"),
        ("__pycache__", "__pycache__/"),
    ],
)
def test_an_equivalent_line_covers_the_entry(existing: str, entry: str) -> None:
    assert missing_entries([existing], [entry]) == []


@pytest.mark.parametrize(
    ("existing", "entry"),
    [
        (".venv/", ".venv"),
        ("/.venv", ".venv/"),
        ("?.pyc", "*.pyc"),
        ("*.py[a-z]", "*.pyc"),
        ("*.py[!o]", "*.pyc"),
        ("# .venv/", ".venv/"),
        ("!.venv/", ".venv/"),
        ("venv/", ".venv/"),
    ],
)
def test_a_line_that_ignores_less_doesnt_cover_the_entry(existing: str, entry: str) -> None:
    assert missing_entries([existing], [entry]) == [entry]


def test_what_uv_init_leaves_is_not_repeated() -> None:
    uv_init = ["# Python-generated files", "__pycache__/", "*.py[oc]", "build/", ".venv"]
    profile = ["__pycache__/", "*.pyc", ".venv/", ".pytest_cache/"]

    assert missing_entries(uv_init, [".lictor/", *profile]) == [".lictor/", ".pytest_cache/"]


def test_an_entry_listed_twice_is_added_once() -> None:
    assert missing_entries([], [".venv/", " .venv/ ", ".venv"]) == [".venv/", ".venv"]
