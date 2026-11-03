"""Unit tests for what a run anchors in its checkpoint."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lictor.application import anchor
from lictor.application.anchor import observe_anchor
from lictor.application.ports import Vcs
from lictor.application.seal import hash_file


def _vcs() -> MagicMock:
    vcs = MagicMock(spec=Vcs)
    vcs.get_head_sha.return_value = "abc123"
    vcs.fingerprint.return_value = "sha256:repo"
    return vcs


def test_anchor_records_head_seal_and_repository(tmp_path: Path) -> None:
    seal = tmp_path / ".lictor" / "seal.json"
    seal.parent.mkdir()
    seal.write_text('{"run_id": "R"}', encoding="utf-8")

    anchor = observe_anchor(_vcs(), tmp_path)

    assert anchor == {"head": "abc123", "seal": hash_file(seal), "repo": "sha256:repo"}
    assert anchor["seal"]


def test_anchor_without_a_seal_records_it_empty(tmp_path: Path) -> None:
    assert observe_anchor(_vcs(), tmp_path) == {"head": "abc123", "seal": "", "repo": "sha256:repo"}


def test_anchor_hashes_the_project_s_own_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seal = tmp_path / ".lictor" / "seal.json"
    seal.parent.mkdir()
    seal.write_text("{}", encoding="utf-8")
    hashed: list[Path] = []
    monkeypatch.setattr(anchor, "hash_file", lambda path: hashed.append(path) or "h")

    anchor.observe_anchor(_vcs(), tmp_path)

    assert [str(p) for p in hashed] == [str(tmp_path / ".lictor" / "seal.json")]
