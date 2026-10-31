"""Git CLI adapter implementing the Vcs port."""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from lictor.application.ports import Vcs
from lictor.domain.errors import CandidateError
from lictor.domain.model import Candidate

_MIN_PORCELAIN_LINE_LEN = 4
_PORCELAIN_PREFIX_LEN = 3
_HARDENED = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")
_EXPORT_REF = "refs/lictor/verified"


class GitCli(Vcs):
    """VCS implementation using the local git command line binary."""

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = Path(repo_dir) if repo_dir is not None else Path.cwd()
        self._git_binary = shutil.which("git") or "git"

    def _run_git(
        self,
        args: Sequence[str],
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env is not None:
            merged_env.update(env)
        return subprocess.run(
            [self._git_binary, *args],
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            check=check,
            env=merged_env,
        )

    def status(self) -> list[str]:
        """Return modified or untracked file paths via git status --porcelain=v1 -uall."""
        res = self._run_git(["-c", "core.quotePath=false", "status", "--porcelain=v1", "-uall"])
        paths: list[str] = []
        for line in res.stdout.splitlines():
            if len(line) < _MIN_PORCELAIN_LINE_LEN:
                continue
            raw_path = line[_PORCELAIN_PREFIX_LEN:]
            if " -> " in raw_path:
                raw_path = raw_path.split(" -> ")[-1]
            if raw_path.startswith('"') and raw_path.endswith('"'):
                raw_path = raw_path[1:-1]
            if raw_path.strip():
                paths.append(raw_path)
        return paths

    def reset_to_head(self) -> None:
        """Discard uncommitted changes and clean untracked files while preserving ignored files."""
        self._run_git(["restore", "--staged", "--worktree", "."], check=False)
        self._run_git(["checkout", "--", "."], check=False)
        self._run_git(["clean", "-fd"])

    def create_candidate(
        self,
        message: str,
        trailers: dict[str, str] | None = None,
        paths: Sequence[str] | None = None,
    ) -> Candidate:
        """Build a commit with author Lictor in a throwaway index, outside the branch."""
        parent = self.get_head_sha()
        with tempfile.TemporaryDirectory(prefix="lictor-index-") as tmp:
            env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            self._run_git([*_HARDENED, "read-tree", "HEAD"], env=env)
            if paths is None:
                self._run_git([*_HARDENED, "add", "-A"], env=env)
            else:
                existing = [p for p in paths if (self.repo_dir / p).exists()]
                if existing:
                    self._run_git([*_HARDENED, "add", "--", *existing], env=env)
            tree = self._run_git([*_HARDENED, "write-tree"], env=env).stdout.strip()

        lines = [f"{key}: {val}" for key, val in (trailers or {}).items()]
        full_message = "\n\n".join([message, "\n".join(lines)]) if lines else message
        sha = self._run_git(
            [*_HARDENED, "commit-tree", tree, "-p", parent, "-m", full_message],
            env={"GIT_AUTHOR_NAME": "Lictor", "GIT_AUTHOR_EMAIL": "lictor@localhost"},
        ).stdout.strip()
        return Candidate(sha=sha, tree=tree, parent=parent)

    def export_tree(self, rev: str, dest: Path) -> None:
        """Fetch one commit into an empty repository, which inherits no hook or local config."""
        sha = self._run_git([*_HARDENED, "rev-parse", "--verify", f"{rev}^{{commit}}"])
        self._run_git([*_HARDENED, "update-ref", _EXPORT_REF, sha.stdout.strip()])
        dest.mkdir(parents=True)
        clone = GitCli(dest)
        clone._run_git([*_HARDENED, "init", "-q", "--template="])
        clone._run_git(
            [*_HARDENED, "fetch", "-q", "--no-tags", str(self.repo_dir.resolve()), _EXPORT_REF]
        )
        clone._run_git([*_HARDENED, "checkout", "-q", "--detach", "FETCH_HEAD"])

    def advance_head(self, sha: str, expected: str) -> None:
        """Move the branch atomically; the working tree stays as it is, and only the index syncs."""
        res = self._run_git([*_HARDENED, "update-ref", "HEAD", sha, expected], check=False)
        if res.returncode != 0:
            raise CandidateError(
                f"La rama ya no está en {expected[:12]}: no se puede avanzar a {sha[:12]}. "
                f"{res.stderr.strip()}"
            )
        self._run_git([*_HARDENED, "reset", "-q"])

    def get_tree(self, rev: str) -> str:
        """Return the tree hash of a commit."""
        return self._run_git(["rev-parse", f"{rev}^{{tree}}"]).stdout.strip()

    def get_trailers(self, key: str, commit_ref: str | None = None) -> list[str]:
        """Extract trailer values from git log for the given key, optionally for a commit."""
        format_arg = f"--format=%(trailers:key={key},valueonly)"
        cmd = ["log"]
        if commit_ref is not None:
            cmd.extend(["-1", commit_ref])
        cmd.append(format_arg)
        res = self._run_git(cmd)
        values: list[str] = []
        for line in res.stdout.splitlines():
            stripped = line.strip()
            if stripped:
                values.append(stripped)
        return values

    def get_head_sha(self) -> str:
        """Return the commit sha of HEAD."""
        res = self._run_git(["rev-parse", "HEAD"])
        return res.stdout.strip()

    def get_head_message(self) -> str:
        """Return the subject line of the HEAD commit."""
        res = self._run_git(["log", "-1", "--format=%s"])
        return res.stdout.strip()

    def revert_paths(self, paths: Sequence[str]) -> None:
        """Selectively revert specific modified or untracked file paths."""
        for path_str in paths:
            target = self.repo_dir / path_str
            # Unstage first if staged
            self._run_git(["restore", "--staged", "--", path_str], check=False)

            # Try checkout from HEAD if tracked
            checkout_res = self._run_git(["checkout", "HEAD", "--", path_str], check=False)
            if checkout_res.returncode != 0 and (target.is_symlink() or target.exists()):
                # Untracked file, directory, or symlink (including broken symlinks)
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)

    def diff(self) -> str:
        """Return diff of working tree against HEAD."""
        res = self._run_git(["diff", "HEAD"], check=False)
        return res.stdout
