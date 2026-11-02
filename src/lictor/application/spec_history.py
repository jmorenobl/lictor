"""What each green scenario demanded when it went green, read from git and nowhere else.

A green is the most recent commit whose `Scenario:` trailer names it. Its specification is the one
in that commit's own tree: the scenario stays green while the hash of what it demands there matches
the hash of what it demands now. No trailer carries the hash, so nothing has to be migrated and a
forged trailer still needs a tree whose features say the same.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from lictor.application.ports import SpecSource, Vcs
from lictor.domain.rules import ScenarioClass, classify_scenarios

FEATURES_DIR = "features"
_FEATURE_SUFFIX = ".feature"


@dataclass(frozen=True)
class GreenRecord:
    """The commit that last made a scenario green, and what it demanded there."""

    commit: str
    spec_hash: str | None


class SpecHistory:
    """Reads the green commits and hashes their specifications, caching by commit and file."""

    def __init__(self, vcs: Vcs, spec_source: SpecSource) -> None:
        self.vcs = vcs
        self.spec_source = spec_source
        self._hashes: dict[tuple[str, str], dict[str, str]] = {}

    def records(self) -> dict[str, GreenRecord]:
        """Return, for each scenario with a green commit, the most recent one."""
        latest: dict[str, str] = {}
        for sha, scenario_ids in self.vcs.trailer_commits("Scenario"):
            for sid in scenario_ids:
                latest.setdefault(sid, sha)
        return {sid: GreenRecord(sha, self._hashes_at(sha).get(sid)) for sid, sha in latest.items()}

    def classify(self, current: Mapping[str, str]) -> dict[str, ScenarioClass]:
        """Say which scenarios are green, which were green for another spec, and which never."""
        recorded = {sid: record.spec_hash for sid, record in self.records().items()}
        return classify_scenarios(current, recorded)

    def _hashes_at(self, sha: str) -> dict[str, str]:
        """Hash every scenario in the features of a commit; the first file to name an ID wins."""
        found: dict[str, str] = {}
        for path in sorted(self.vcs.list_files(sha, FEATURES_DIR)):
            if not path.endswith(_FEATURE_SUFFIX):
                continue
            key = (sha, path)
            if key not in self._hashes:
                self._hashes[key] = self.spec_source.spec_hashes(self.vcs.read_file(sha, path))
            for sid, spec_hash in self._hashes[key].items():
                found.setdefault(sid, spec_hash)
        return found
