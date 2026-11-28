"""What each green scenario demanded when it went green, read from git and nowhere else.

A green is the most recent commit whose `Scenario:` trailer names it. Its specification is the one
in that commit's own tree: the scenario stays green while the hash of what it demands there matches
the hash of what it demands now. No trailer carries the hash, so nothing has to be migrated and a
forged trailer still needs a tree whose features say the same.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from lictor.application.ports import SpecSource, Vcs
from lictor.domain.rules import RETIRED_TRAILER, ScenarioClass, classify_scenarios

FEATURES_DIR = "features"
_FEATURE_SUFFIX = ".feature"


RUN_TRAILER = "Lictor-Run"
ASSERTED_TRAILER = "Lictor-Asserted-By"


@dataclass(frozen=True)
class GreenRecord:
    """The commit that last made a scenario green, and what it demanded there.

    `by_lictor` says whether Lictor made that commit, as its `Lictor-Run` trailer claims; a
    person who registered it says who in `asserted_by`. Both are only informative: anyone can
    write a trailer.
    """

    commit: str
    spec_hash: str | None
    by_lictor: bool = True
    asserted_by: str | None = None


class SpecHistory:
    """Reads the green commits and hashes their specifications, caching by commit and file."""

    def __init__(self, vcs: Vcs, spec_source: SpecSource) -> None:
        self.vcs = vcs
        self.spec_source = spec_source
        self._hashes: dict[tuple[str, str], dict[str, str]] = {}

    def records(self) -> dict[str, GreenRecord]:
        """Return, for each scenario with a green commit, the most recent one.

        A green that a later driver's commit retired (its `Lictor-Retired`, descending from the
        green) is green no more: a scenario that comes back to `features/` is new, and its test
        has to be written again (Fable's I1 of the PR).
        """
        latest = _latest("Scenario", self.vcs)
        retired = _latest(RETIRED_TRAILER, self.vcs)
        latest = {sid: sha for sid, sha in latest.items() if not self._after(sha, retired.get(sid))}
        by_lictor = {sha for sha, _ in self.vcs.trailer_commits(RUN_TRAILER)}
        asserted = {sha: who[0] for sha, who in self.vcs.trailer_commits(ASSERTED_TRAILER) if who}
        return {
            sid: GreenRecord(
                sha, self._hashes_at(sha).get(sid), sha in by_lictor, asserted.get(sha)
            )
            for sid, sha in latest.items()
        }

    def retired(self, current: Collection[str]) -> tuple[str, ...]:
        """The scenarios with a green commit that the specification no longer has (S23).

        One stops being retired once a driver's commit after its green names it in its
        `Lictor-Retired` trailer: that commit took its tests out, and the run said so; from
        there it has no green (`records`).
        """
        return tuple(sorted(sid for sid in self.records() if sid not in current))

    def _after(self, green: str, retired: str | None) -> bool:
        """Whether a retire commit came after the green, which it descends from."""
        return retired is not None and self.vcs.merge_base(green, retired) == green

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


def _latest(key: str, vcs: Vcs) -> dict[str, str]:
    """For each scenario a trailer names, the most recent commit that names it."""
    latest: dict[str, str] = {}
    for sha, scenario_ids in vcs.trailer_commits(key):
        for sid in scenario_ids:
            latest.setdefault(sid, sha)
    return latest
