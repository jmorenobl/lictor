"""Unit tests for the branch of each run and the choice of the run to resume."""

import pytest

from lictor.domain.run_branches import (
    branch_name_problem,
    integration_hint,
    resume_choice,
    run_branch,
    uncommitted_left_out,
)


@pytest.mark.parametrize("name", ["iva", "IVA-001", "a", "20261110-104131-ea1f", "v1.2_x"])
def test_a_plain_name_can_name_a_branch(name: str) -> None:
    assert branch_name_problem(name) is None


@pytest.mark.parametrize(
    "name", ["", "a/b", "../x", "a..b", "-x", ".x", "x.", "x.lock", "con espacio", "ñ"]
)
def test_a_name_that_git_or_a_directory_would_refuse_is_refused(name: str) -> None:
    problem = branch_name_problem(name)

    assert problem is not None
    assert f"«{name}»" in problem


def test_the_branch_is_the_name_given_or_the_run_id() -> None:
    assert run_branch("iva", "R1") == "lictor/iva"
    assert run_branch(None, "R1") == "lictor/R1"
    assert run_branch("", "R1") == "lictor/R1"


def test_a_single_pending_run_is_resumed() -> None:
    assert resume_choice(["lictor/a"], None) == ("lictor/a", None)


def test_without_pending_runs_nothing_is_resumed() -> None:
    assert resume_choice([], None) == (None, None)


def test_several_pending_runs_have_to_be_told_apart() -> None:
    branch, problem = resume_choice(["lictor/b", "lictor/a"], None)

    assert branch is None
    assert problem is not None
    assert "lictor/a, lictor/b" in problem
    assert "--branch" in problem
    assert "--new" in problem


def test_a_branch_given_resumes_only_that_one() -> None:
    assert resume_choice(["lictor/a", "lictor/b"], "lictor/b") == ("lictor/b", None)
    assert resume_choice(["lictor/a"], "lictor/c") == (None, None)
    assert resume_choice([], "lictor/c") == (None, None)


def test_the_hint_names_the_branch_and_how_to_integrate_it() -> None:
    hint = integration_hint("lictor/iva")

    assert "rama lictor/iva" in hint
    assert "`git merge lictor/iva`" in hint
    assert "pull request" in hint


def test_the_warning_lists_a_few_paths_and_counts_the_rest() -> None:
    paths = [f"f{i}" for i in range(7)]

    warning = uncommitted_left_out(paths)

    assert "f0, f1, f2, f3, f4 y 2 más." in warning
    assert "f5" not in warning
    assert "último commit" in warning


def test_the_warning_with_few_paths_lists_them_all() -> None:
    assert uncommitted_left_out(["a", "b"]).endswith(": a, b.")
    assert uncommitted_left_out([f"f{i}" for i in range(5)]).endswith("f4.")
