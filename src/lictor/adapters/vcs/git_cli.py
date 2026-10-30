"""Git CLI adapter implementing the Vcs port."""

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from lictor.application.ports import Vcs

_MIN_PORCELAIN_LINE_LEN = 4
_PORCELAIN_PREFIX_LEN = 3


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
        self._run_git(["checkout", "--", "."], check=False)
        self._run_git(["clean", "-fd"])

    def commit(self, message: str, trailers: dict[str, str] | None = None) -> str:
        """Create a commit with author Lictor, disabled hooks, and trailers."""
        self._run_git(["add", "-A"])

        cmd = [
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--author=Lictor <lictor@localhost>",
            "-m",
            message,
        ]
        if trailers:
            for key, val in trailers.items():
                cmd.extend(["--trailer", f"{key}: {val}"])

        self._run_git(cmd)
        sha_res = self._run_git(["rev-parse", "HEAD"])
        return sha_res.stdout.strip()

    def get_trailers(self, key: str) -> list[str]:
        """Extract trailer values from git log for the given key."""
        format_arg = f"--format=%(trailers:key={key},valueonly)"
        res = self._run_git(["log", format_arg])
        values: list[str] = []
        for line in res.stdout.splitlines():
            stripped = line.strip()
            if stripped:
                values.append(stripped)
        return values

    def revert_paths(self, paths: Sequence[str]) -> None:
        """Selectively revert specific modified or untracked file paths."""
        for path_str in paths:
            target = self.repo_dir / path_str
            # Unstage first if staged
            self._run_git(["restore", "--staged", "--", path_str], check=False)

            # Try checkout from HEAD if tracked
            checkout_res = self._run_git(["checkout", "HEAD", "--", path_str], check=False)
            if checkout_res.returncode != 0 and target.exists():
                # Untracked file or directory
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)

    def diff(self) -> str:
        """Return diff of working tree against HEAD."""
        res = self._run_git(["diff", "HEAD"], check=False)
        return res.stdout
