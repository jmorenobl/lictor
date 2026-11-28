"""Which change of `.git/config` Lictor guards (S22 of 0.25.0)."""

import pytest

from lictor.domain.git_config import (
    changed_keys,
    config_change_text,
    config_entries,
    inert_key,
    watched,
)


@pytest.mark.parametrize(
    ("key", "inert"),
    [
        # What an editor writes under the branch it just created.
        ("branch.lictor/iva.vscode-merge-base", True),
        # A branch of the person's, whatever git reads of it.
        ("branch.feat.remote", True),
        ("branch.feat.mergeoptions", True),
        # The run's own branch: what git reads of it is guarded.
        ("branch.lictor/iva.remote", False),
        ("branch.lictor/iva.merge", False),
        ("branch.lictor/iva.mergeoptions", False),
        ("branch.lictor/iva.rebase", False),
        ("branch.lictor/iva.pushremote", False),
        ("branch.lictor/iva.description", False),
        # A branch whose name has dots is still a subsection.
        ("branch.v1.2.vscode-merge-base", True),
        ("branch.lictor/v1.2.merge", False),
        # Outside a branch's section, everything.
        ("core.hookspath", False),
        ("remote.origin.url", False),
        ("include.path", False),
        ("filter.x.clean", False),
        ("branch.autosetupmerge", False),
    ],
)
def test_only_a_branch_s_section_can_be_inert(key: str, inert: bool) -> None:
    branch = "lictor/v1.2" if "v1.2" in key and "lictor/" in key else "lictor/iva"

    assert inert_key(key, branch) is inert


def test_with_a_detached_head_every_branch_section_is_another_branch_s() -> None:
    assert inert_key("branch.lictor/iva.merge", "") is True


def test_the_listed_config_reads_keys_values_and_keys_without_a_value() -> None:
    listed = "core.bare\nfalse\0branch.x.flag\0remote.origin.url\nhttps://a\nb\0"

    assert config_entries(listed) == (
        ("core.bare", "false"),
        ("branch.x.flag", ""),
        ("remote.origin.url", "https://a\nb"),
    )
    assert config_entries("") == ()


def test_what_is_watched_and_what_changed_leave_the_inert_out() -> None:
    before = (("core.bare", "false"), ("branch.lictor/iva.remote", "origin"))
    after = (
        ("core.bare", "false"),
        ("core.hookspath", "/srv/h"),
        ("branch.lictor/iva.remote", "otro"),
        ("branch.lictor/iva.vscode-merge-base", "origin/main"),
        ("branch.feat.remote", "origin"),
    )

    assert watched(after, "lictor/iva") == (
        ("core.bare", "false"),
        ("core.hookspath", "/srv/h"),
        ("branch.lictor/iva.remote", "otro"),
    )
    assert changed_keys(before, after, "lictor/iva") == (
        "branch.lictor/iva.remote",
        "core.hookspath",
    )
    assert changed_keys(before, before, "lictor/iva") == ()


def test_a_key_repeated_counts_by_all_its_values_in_order() -> None:
    before = (("remote.origin.fetch", "a"), ("remote.origin.fetch", "b"))
    after = (("remote.origin.fetch", "b"), ("remote.origin.fetch", "a"))

    assert changed_keys(before, after, "x") == ("remote.origin.fetch",)


def test_a_changed_config_names_its_keys() -> None:
    assert config_change_text(["core.hookspath"]) == ".git/config (core.hookspath)"
    assert config_change_text([]) == ".git/config"
    assert config_change_text(["a", "b", "c", "d", "e"]) == ".git/config (a, b, c, d, e)"
    assert config_change_text(["a", "b", "c", "d", "e", "f", "g"]) == (
        ".git/config (a, b, c, d, e y 2 más)"
    )
