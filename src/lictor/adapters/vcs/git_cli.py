"""Git CLI adapter implementing the Vcs port."""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from lictor.application.ports import Vcs
from lictor.domain.errors import CandidateError
from lictor.domain.model import Candidate, TreeChange

_MIN_PORCELAIN_LINE_LEN = 4
_PORCELAIN_PREFIX_LEN = 3
_HARDENED = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")
_EXPORT_REF = "refs/lictor/verified"
_NO_USER_CONFIG = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


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
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env is not None:
            merged_env.update(env)
        return subprocess.run(
            [self._git_binary, *args],
            cwd=str(cwd or self.repo_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
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

    def capture(self, workdir: Path, skip: Sequence[str] = ()) -> str:
        """Read a directory into a throwaway index over HEAD, without touching anything else.

        Only what git would track counts: an ignored file stays out, whoever ignored it, so a
        `.gitignore` in `workdir` can only hide files from the result. A tracked file is always
        compared, so it can't be hidden, and a deletion counts like any other change.
        """
        git_dir = self._run_git([*_HARDENED, "rev-parse", "--absolute-git-dir"]).stdout.strip()
        with tempfile.TemporaryDirectory(prefix="lictor-index-") as tmp:
            env = {
                "GIT_INDEX_FILE": str(Path(tmp) / "index"),
                "GIT_DIR": git_dir,
                "GIT_WORK_TREE": str(workdir),
                **_NO_USER_CONFIG,
            }
            self._run_git([*_HARDENED, "read-tree", "HEAD"], env=env)
            added = self._run_git([*_HARDENED, "add", "-A"], check=False, env=env, cwd=workdir)
            if added.returncode != 0:
                raise CandidateError(f"No se pudo leer el resultado: {added.stderr.strip()}")
            if skip:
                self._run_git(
                    [*_HARDENED, "reset", "-q", "HEAD", "--", *skip], env=env, cwd=workdir
                )
            return self._run_git([*_HARDENED, "write-tree"], env=env).stdout.strip()

    def changes(self, tree: str) -> list[TreeChange]:
        """List what `tree` changes over HEAD; renames count as a deletion and an addition."""
        raw = self._run_git(
            [*_HARDENED, "diff-tree", "-r", "-z", "--raw", "--no-renames", "HEAD", tree]
        ).stdout
        fields = raw.split("\0")
        found: list[TreeChange] = []
        for header, path in zip(fields[0::2], fields[1::2], strict=False):
            _, new_mode, *_ = header.split(" ")
            found.append(TreeChange(path=path, mode=new_mode))
        return found

    def added_lines(self, tree: str) -> list[str]:
        """Read the added lines of the diff, binary files included: a NUL must not hide them."""
        patch = self._run_git(
            [
                *_HARDENED,
                "diff-tree",
                "-r",
                "-p",
                "-U0",
                "--text",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "HEAD",
                tree,
            ]
        ).stdout
        added: list[str] = []
        in_hunk = False
        for line in patch.splitlines():
            if line.startswith("diff --git "):
                in_hunk = False
            elif line.startswith("@@"):
                in_hunk = True
            elif in_hunk and line.startswith("+"):
                added.append(line[1:])
        return added

    def commit_tree(
        self,
        tree: str,
        message: str,
        trailers: dict[str, str] | None = None,
    ) -> Candidate:
        """Build a commit with author Lictor; nothing points to it until the branch advances."""
        parent = self.get_head_sha()
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

    def export_copy(self, rev: str, dest: Path) -> None:
        """Export a commit and take its repository away, the agent's only way to history."""
        self.export_tree(rev, dest)
        shutil.rmtree(dest / ".git")

    def advance_head(self, sha: str, expected: str) -> None:
        """Fast-forward with a single command, so that a killed run leaves one state or the other.

        The working tree belongs to the user: git updates what the commit changes, keeps every
        other edit and refuses before touching anything if the commit would overwrite one.
        """
        head = self.get_head_sha()
        if head != expected:
            raise CandidateError(
                f"La rama ya no está en {expected[:12]}, sino en {head[:12]}: "
                f"no se puede avanzar a {sha[:12]}."
            )
        merged = self._run_git(
            [
                *_HARDENED,
                "-c",
                "merge.verifySignatures=false",
                "merge",
                "--ff-only",
                "--quiet",
                "--no-stat",
                sha,
            ],
            check=False,
        )
        if merged.returncode != 0:
            raise CandidateError(
                f"No se puede avanzar la rama a {sha[:12]} sin pisar cambios locales: "
                f"{merged.stderr.strip()}"
            )

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
