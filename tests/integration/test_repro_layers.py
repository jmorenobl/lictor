"""The repro cases run on a light project unless their defence needs every control (F40, B).

Light means the real `python-uv` profile and the configuration of `lictor init`, without the
profile's lint, type and unit controls: a case about a forged bytecode or a planted plugin proves
it through the acceptance and the L0 gates, and paid for `mypy` and `pytest` on every
verification. The smoke group keeps the whole configuration, to prove that everything fits; so do
the cases whose defence is about those controls. This file isn't under `repro/`, so every gate
runs it: dropping a marker fails here, not only in the jobs that run the cases.
"""

import importlib
import json
from pathlib import Path

import pytest

from lictor.adapters.config.loader import load_config
from tests.integration.repro.harness import (
    LIGHT_DISABLED,
    keeps_refactor,
    light_config,
    uses_all_controls,
    without_refactor,
)

# Everything fits: a whole cycle to green, from the person's checkout too, controls that block, a
# coverage score read from the one run of unit and its minimum, `check` by level and by id, and
# `doctor` on a fresh project.
SMOKE = {
    "test_baseline": (
        "test_c0_honest_agent_on_real_profile",
        "test_repro_projects_use_the_real_profile",
    ),
    "test_phase3_verification_boundary": ("test_type_control_still_reads_the_sources",),
    "test_phase7_check": ("test_check_passes_and_filters_by_level_and_id",),
    "test_phase7_doctor": ("test_doctor_passes_on_a_project_from_lictor_init",),
    "test_v0_3_0_coverage": (
        "test_check_shows_the_coverage_as_a_score",
        "test_a_minimum_coverage_asked_for_by_id_fails_below_it",
    ),
    "test_v0_3_0_own_worktree": (
        "test_a_run_from_the_checkout_works_in_its_own_worktree_and_branch",
    ),
    "test_v0_4_0_coverage_reads_unit": (
        "test_each_verification_of_a_run_runs_the_unit_tests_once",
    ),
}

# Cases whose defence is about a control the light project drops: without it they would pass
# because the attack, or the output, never happened.
ALL_CONTROLS = {
    "test_phase3_verification_boundary": (
        "test_d22_writable_paths_cannot_neutralize_l1_and_l2",
        "test_project_code_only_runs_where_the_tests_import_it",
    ),
    "test_phase4_environment": ("test_user_configuration_does_not_decide_a_control",),
    "test_phase5_oracle_cycle": ("test_d12_missing_unit_dir_does_not_block_an_honest_agent",),
    "test_phase6_robustness": ("test_e20_non_utf8_output_does_not_crash_the_run",),
    "test_phase7_check": ("test_check_ignores_the_users_configuration",),
    "test_v0_4_0_coverage_reads_unit": (
        "test_unit_runs_once_and_coverage_scores_that_run",
        "test_the_projects_fail_under_doesnt_fail_unit",
        "test_without_pytest_cov_unit_runs_as_it_is_and_coverage_is_skipped",
        "test_coverage_only_in_check_measures_there_and_never_in_the_run",
    ),
}


# Cases about the refactor (ADR-0010), which every other case turns off: without it they would
# pass because no refactor ran.
WITH_REFACTOR = {
    "test_v0_7_0_refactor": (
        "test_each_implemented_green_gets_a_refactor_that_keeps_every_green",
        "test_a_refactor_that_passes_fewer_unit_tests_is_discarded_and_the_green_stays",
    ),
    "test_v0_21_0_agent_commands": (
        "test_with_format_off_nothing_asks_to_format_and_the_refactor_stays_in_scope",
    ),
    "test_v0_21_0_refactor_scope": (
        "test_a_refactor_that_changes_code_its_scenario_didnt_is_discarded_and_the_green_stays",
        "test_a_refactor_of_its_scenarios_file_and_a_new_one_is_applied",
    ),
}


def _case(module: str, name: str) -> object:
    return getattr(importlib.import_module(f"tests.integration.repro.{module}"), name)


def _marks(module: str, name: str) -> set[str]:
    return {mark.name for mark in getattr(_case(module, name), "pytestmark", [])}


def _cases(table: dict[str, tuple[str, ...]]) -> list[tuple[str, str]]:
    return [(module, name) for module, names in table.items() for name in names]


@pytest.mark.parametrize(("module", "name"), _cases(SMOKE))
def test_the_smoke_group_keeps_the_whole_configuration(module: str, name: str) -> None:
    assert "smoke" in _marks(module, name)


def test_the_smoke_group_is_small_and_exists() -> None:
    assert 5 <= len(_cases(SMOKE)) <= 10


@pytest.mark.parametrize(("module", "name"), _cases(ALL_CONTROLS))
def test_a_case_about_the_controls_keeps_them(module: str, name: str) -> None:
    assert _marks(module, name) & {"smoke", "all_controls"}


@pytest.mark.parametrize(
    ("marks", "expected"),
    [(set(), False), ({"smoke"}, True), ({"all_controls"}, True), ({"repro"}, False)],
)
def test_what_a_mark_asks_for(marks: set[str], expected: bool) -> None:
    assert uses_all_controls(marks) is expected


def test_the_light_project_drops_only_the_controls_it_names(tmp_path: Path) -> None:
    written: dict[str, object] = {"profile": "python-uv@0.2"}
    (tmp_path / "lictor.json").write_text(json.dumps(written), encoding="utf-8")
    whole = load_config(tmp_path)

    (tmp_path / "lictor.json").write_text(json.dumps(light_config(written)), encoding="utf-8")
    light = load_config(tmp_path)

    assert {c.id for c in whole.controls} - {c.id for c in light.controls} == set(LIGHT_DISABLED)
    assert light.acceptance == whole.acceptance


@pytest.mark.parametrize("named", ["unit", "coverage"])
def test_a_case_that_names_either_of_the_pair_keeps_both(tmp_path: Path, named: str) -> None:
    """ADR-0008: dropping unit alone leaves coverage reading nothing, and that exits 10."""
    written: dict[str, object] = {
        "profile": "python-uv@0.2",
        "controls": [{"id": named, "mode": "block"}],
    }
    (tmp_path / "lictor.json").write_text(json.dumps(light_config(written)), encoding="utf-8")

    ids = {c.id for c in load_config(tmp_path).controls}

    assert {"unit", "coverage"} <= ids
    assert not {"lint", "format", "types"} & ids


@pytest.mark.parametrize("dropped", ["unit", "coverage"])
def test_a_case_that_drops_either_of_the_pair_drops_both(tmp_path: Path, dropped: str) -> None:
    written: dict[str, object] = {
        "profile": "python-uv@0.2",
        "controls": [{"id": dropped, "enabled": False}],
    }
    (tmp_path / "lictor.json").write_text(json.dumps(light_config(written)), encoding="utf-8")

    ids = {c.id for c in load_config(tmp_path).controls}

    assert not {"unit", "coverage"} & ids


def test_a_change_the_case_writes_wins_over_the_light_project() -> None:
    written = {"controls": [{"id": "coverage", "mode": "block", "min_score": 90}]}

    controls = light_config(written)["controls"]

    assert {"id": "coverage", "mode": "block", "min_score": 90} in controls
    assert {"id": "coverage", "enabled": False} not in controls
    assert {"id": "types", "enabled": False} in controls


@pytest.mark.parametrize(("module", "name"), _cases(WITH_REFACTOR))
def test_a_case_about_the_refactor_keeps_it(module: str, name: str) -> None:
    assert keeps_refactor(_marks(module, name))


@pytest.mark.parametrize(
    ("marks", "expected"),
    [(set(), False), ({"smoke"}, True), ({"with_refactor"}, True), ({"all_controls"}, False)],
)
def test_who_keeps_the_refactor(marks: set[str], expected: bool) -> None:
    assert keeps_refactor(marks) is expected


def test_a_case_turns_the_refactor_off_unless_it_decides_it(tmp_path: Path) -> None:
    written: dict[str, object] = {"profile": "python-uv@0.2"}
    (tmp_path / "lictor.json").write_text(json.dumps(without_refactor(written)), encoding="utf-8")
    assert load_config(tmp_path).refactor.enabled is False

    on = without_refactor({**written, "refactor": {"enabled": True}})
    (tmp_path / "lictor.json").write_text(json.dumps(on), encoding="utf-8")
    assert load_config(tmp_path).refactor.enabled is True
