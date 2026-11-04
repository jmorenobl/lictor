"""Unit tests for SpecHistory: greens come from git, with the spec of their own commit."""

from unittest.mock import MagicMock

from lictor.application.ports import SpecSource, Vcs
from lictor.application.spec_history import GreenRecord, SpecHistory

FILES = {
    ("c2", "features/iva.feature"): "v2",
    ("c1", "features/iva.feature"): "v1",
    ("c1", "features/otro.feature"): "o1",
}
HASHES = {"v1": {"A-1": "h1", "A-2": "h2"}, "v2": {"A-1": "h1b"}, "o1": {"B-1": "hb", "A-1": "x"}}


def _history(
    commits: list[tuple[str, list[str]]],
    others: dict[str, list[tuple[str, list[str]]]] | None = None,
) -> tuple[SpecHistory, MagicMock, MagicMock]:
    vcs = MagicMock(spec=Vcs)
    # Lictor writes Lictor-Run on every green it commits; a case says otherwise in `others`.
    trailers = {"Scenario": commits, "Lictor-Run": [(sha, ["r"]) for sha, _ in commits]}
    trailers.update(others or {})
    vcs.trailer_commits.side_effect = lambda key: trailers.get(key, [])
    vcs.list_files.side_effect = lambda sha, directory: (
        [path for (commit, path) in FILES if commit == sha] + [f"{directory}/README.md"]
    )
    vcs.read_file.side_effect = lambda sha, path: FILES[(sha, path)]
    spec_source = MagicMock(spec=SpecSource)
    spec_source.spec_hashes.side_effect = lambda text: HASHES[text]
    return SpecHistory(vcs, spec_source), vcs, spec_source


def test_each_scenario_takes_its_most_recent_green_commit() -> None:
    history, vcs, _ = _history([("c2", ["A-1"]), ("c1", ["A-1", "A-2", "B-1"])])

    records = history.records()

    assert records == {
        "A-1": GreenRecord("c2", "h1b"),
        "A-2": GreenRecord("c1", "h2"),
        "B-1": GreenRecord("c1", "hb"),
    }
    vcs.trailer_commits.assert_any_call("Scenario")
    assert {call.args[1] for call in vcs.list_files.call_args_list} == {"features"}


def test_a_scenario_its_green_commit_does_not_describe_has_no_hash() -> None:
    history, _, _ = _history([("c2", ["A-2"])])
    assert history.records() == {"A-2": GreenRecord("c2", None)}


def test_the_first_feature_file_to_name_an_id_wins() -> None:
    history, _, _ = _history([("c1", ["A-1"])])
    assert history.records()["A-1"].spec_hash == "h1"


def test_classify_compares_with_the_current_hashes() -> None:
    history, _, _ = _history([("c2", ["A-1"]), ("c1", ["A-2"])])

    classes = history.classify({"A-1": "h1b", "A-2": "otro", "A-3": "h3"})

    assert classes == {"A-1": "green", "A-2": "reopened", "A-3": "new"}


def test_files_are_hashed_once_per_commit() -> None:
    history, vcs, spec_source = _history([("c1", ["A-1", "A-2"])])

    history.records()
    history.records()

    assert spec_source.spec_hashes.call_count == 2
    assert vcs.read_file.call_count == 2
    assert all(not call.args[1].endswith("README.md") for call in vcs.read_file.call_args_list)


def test_a_green_lictor_didnt_commit_is_marked_with_who_asserted_it() -> None:
    history, _, _ = _history(
        [("c2", ["A-1"]), ("c1", ["A-2", "B-1"])],
        {
            "Lictor-Run": [("c1", ["r1"])],
            "Lictor-Asserted-By": [("c2", ["Ana <ana@example.com>"])],
        },
    )

    records = history.records()

    assert records["A-1"] == GreenRecord("c2", "h1b", False, "Ana <ana@example.com>")
    assert records["A-2"] == GreenRecord("c1", "h2", True, None)


def test_a_green_without_either_trailer_was_registered_by_hand_by_someone() -> None:
    history, _, _ = _history(
        [("c1", ["A-2"])], {"Lictor-Run": [], "Lictor-Asserted-By": [("c1", [])]}
    )

    assert history.records()["A-2"] == GreenRecord("c1", "h2", False, None)
