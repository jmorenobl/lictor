"""What a run records in its checkpoint so that a resume can tell what happened meanwhile.

Between two processes there is no memory to rely on: this is all a resume can compare against.
With no sandbox the checkpoint is within reach of the same user too, so the anchor raises the cost
of a forgery but doesn't rule one out: whoever rewrites the checkpoint as well gets through.
"""

from pathlib import Path

from lictor.application.ports import Vcs
from lictor.application.seal import hash_file


def observe_anchor(vcs: Vcs, project_root: Path) -> dict[str, str]:
    """Read the anchor as things stand now."""
    seal = project_root / ".lictor" / "seal.json"
    return {
        "head": vcs.get_head_sha(),
        "seal": hash_file(seal) if seal.is_file() else "",
        "repo": vcs.fingerprint(),
    }
