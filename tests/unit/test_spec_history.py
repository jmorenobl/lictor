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


def _history(commits: list[tuple[str, list[str]]]) -> tuple[SpecHistory, MagicMock, MagicMock]:
    vcs = MagicMock(spec=Vcs)
    vcs.trailer_commits.return_value = commits
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
    vcs.trailer_commits.assert_called_once_with("Scenario")


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
