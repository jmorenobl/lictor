"""Git CLI adapter implementing the Vcs port."""

import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from lictor.application.ports import Vcs
from lictor.domain.errors import CandidateError, EnvironmentUnavailableError
from lictor.domain.model import Candidate, RepoPin, TreeChange

_MIN_PORCELAIN_LINE_LEN = 4
_PORCELAIN_PREFIX_LEN = 3
# On every call: a repository may hold config that runs code, and Lictor has to read it anyway.
_HARDENED = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")
# Set by whoever launched Lictor, for instance a git hook: they would redirect every call.
_INHERITED_LOCATIONS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)
# Files that decide what git executes or sees. `refs/` isn't here: HEAD is compared by value.
_CONTROL_FILES = ("HEAD", "config")
_CONTROL_DIRS = ("hooks", "info")
_EXPORT_REF = "refs/lictor/verified"
_NO_USER_CONFIG = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
_EXECUTABLE_MODE = "100755"


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
        merged_env = {k: v for k, v in os.environ.items() if k not in _INHERITED_LOCATIONS}
        merged_env["GIT_NO_REPLACE_OBJECTS"] = "1"
        if env is not None:
            merged_env.update(env)
        return subprocess.run(
            [self._git_binary, *_HARDENED, *args],
            cwd=str(cwd or self.repo_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=check,
            env=merged_env,
        )

    def _git_dir(self) -> Path:
        """Locate the repository by reading, not by asking git, which would obey its config."""
        dot_git = self.repo_dir / ".git"
        if dot_git.is_file():
            pointer = dot_git.read_text(encoding="utf-8").removeprefix("gitdir:").strip()
            return (self.repo_dir / pointer).resolve()
        return dot_git.resolve()

    def _control_files(self) -> dict[str, bytes]:
        git_dir = self._git_dir()
        found = {name: git_dir / name for name in _CONTROL_FILES}
        for directory in _CONTROL_DIRS:
            for path in (git_dir / directory).rglob("*"):
                found[path.relative_to(git_dir).as_posix()] = path
        return {
            name: path.read_bytes()
            for name, path in found.items()
            if path.is_file() and not path.is_symlink()
        }

    def pin(self) -> RepoPin:
        """Record the repository: the control files in memory, where nothing else reaches."""
        return RepoPin(head=self.get_head_sha(), files=self._control_files())

    def restore(self, pin: RepoPin) -> list[str]:
        """Put the control files back before running git again, then bring HEAD back."""
        git_dir = self._git_dir()
        current = self._control_files()
        changed: list[str] = []
        for name in sorted(current.keys() | pin.files.keys()):
            if current.get(name) == pin.files.get(name):
                continue
            changed.append(f".git/{name}")
            target = git_dir / name
            if name in pin.files:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.unlink(missing_ok=True)
                target.write_bytes(pin.files[name])
            else:
                target.unlink()

        if self.get_head_sha() != pin.head:
            changed.append("HEAD")
            # `--keep` undoes what the commit changed and keeps what the user is editing.
            kept = self._run_git(["reset", "-q", "--keep", pin.head], check=False)
            if kept.returncode != 0:
                self._run_git(["update-ref", "HEAD", pin.head])
        return changed

    def fingerprint(self) -> str:
        """Hash the control files, read as bytes: no git command runs here."""
        digest = hashlib.sha256()
        for name, content in sorted(self._control_files().items()):
            digest.update(name.encode() + b"\0" + content + b"\0")
        return f"sha256:{digest.hexdigest()}"

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
        with tempfile.TemporaryDirectory(prefix="lictor-index-") as tmp:
            env = {
                "GIT_INDEX_FILE": str(Path(tmp) / "index"),
                "GIT_DIR": str(self._git_dir()),
                "GIT_WORK_TREE": str(workdir),
                **_NO_USER_CONFIG,
            }
            self._run_git(["read-tree", "HEAD"], env=env)
            added = self._run_git(["add", "-A"], check=False, env=env, cwd=workdir)
            if added.returncode != 0:
                raise CandidateError(f"No se pudo leer el resultado: {added.stderr.strip()}")
            if skip:
                self._run_git(["reset", "-q", "HEAD", "--", *skip], env=env, cwd=workdir)
            return self._run_git(["write-tree"], env=env).stdout.strip()

    def changes(self, tree: str) -> list[TreeChange]:
        """List what `tree` changes over HEAD; renames count as a deletion and an addition."""
        raw = self._run_git(["diff-tree", "-r", "-z", "--raw", "--no-renames", "HEAD", tree]).stdout
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
        trailers: Mapping[str, str | Sequence[str]] | None = None,
        *,
        parent: str | None = None,
        empty: bool = False,
    ) -> Candidate:
        """Build a commit with author Lictor; nothing points to it until the branch advances."""
        parent = parent or self.get_head_sha()
        if not empty and self.get_tree(parent) == tree:
            raise CandidateError(f"El commit sobre {parent[:12]} no cambia nada")
        lines = [
            f"{key}: {value}"
            for key, values in (trailers or {}).items()
            for value in ([values] if isinstance(values, str) else values)
        ]
        full_message = "\n\n".join([message, "\n".join(lines)]) if lines else message
        sha = self._run_git(
            ["commit-tree", tree, "-p", parent, "-m", full_message],
            env={"GIT_AUTHOR_NAME": "Lictor", "GIT_AUTHOR_EMAIL": "lictor@localhost"},
        ).stdout.strip()
        return Candidate(sha=sha, tree=tree, parent=parent)

    def export_tree(self, rev: str, dest: Path) -> None:
        """Fetch one commit into an empty repository: no hook, template, or user or local config.

        Without that isolation, a global `core.autocrlf` or a filter would change the tree under
        test on one machine and not on another.
        """
        sha = self._run_git(["rev-parse", "--verify", f"{rev}^{{commit}}"])
        self._run_git(["update-ref", _EXPORT_REF, sha.stdout.strip()])
        dest.mkdir(parents=True)
        clone = GitCli(dest)
        clone._run_git(["init", "-q", "--template="], env=_NO_USER_CONFIG)
        clone._run_git(
            ["fetch", "-q", "--no-tags", str(self.repo_dir.resolve()), _EXPORT_REF],
            env=_NO_USER_CONFIG,
        )
        clone._run_git(["checkout", "-q", "--detach", "FETCH_HEAD"], env=_NO_USER_CONFIG)

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

    def trailer_commits(self, key: str) -> list[tuple[str, list[str]]]:
        """Read every commit with the trailer in one call: NUL ends a record, LF a value.

        A repository without commits has none, rather than an error.
        """
        if self._run_git(["rev-parse", "--verify", "-q", "HEAD"], check=False).returncode != 0:
            return []
        log = self._run_git(["log", f"--format=%H%n%(trailers:key={key},valueonly)%x00"]).stdout
        found: list[tuple[str, list[str]]] = []
        for record in log.split("\0"):
            sha, _, values = record.strip().partition("\n")
            names = [value.strip() for value in values.splitlines() if value.strip()]
            if sha and names:
                found.append((sha, names))
        return found

    def list_files(self, rev: str, directory: str) -> list[str]:
        """List the files of a commit under a directory, without checking anything out."""
        raw = self._run_git(
            ["ls-tree", "-r", "-z", "--name-only", f"{rev}^{{tree}}", "--", directory]
        ).stdout
        return [path for path in raw.split("\0") if path]

    def is_executable(self, rev: str, path: str) -> bool:
        """Look the file up in the commit, not on disk: a clone only has what was committed."""
        entry = self._run_git(["ls-tree", "-z", f"{rev}^{{tree}}", "--", path]).stdout
        return entry.split(" ", 1)[0] == _EXECUTABLE_MODE

    def committer_identity(self) -> str:
        """Ask git with the same call `commit_tree` makes, so both apply the same rules."""
        ident = self._run_git(["var", "GIT_COMMITTER_IDENT"], check=False)
        if ident.returncode != 0:
            raise EnvironmentUnavailableError(
                "git no tiene identidad para los commits que hace Lictor: "
                f"{ident.stderr.strip().rstrip('.')}. Configura user.name y user.email "
                "(git config) y relanza."
            )
        # The last two fields are the date and the time zone of the moment it was asked.
        return ident.stdout.strip().rsplit(" ", 2)[0]

    def read_file(self, rev: str, path: str) -> str:
        """Read a file of a commit with no filter, its line endings read as `read_text` would."""
        return self._run_git(["cat-file", "blob", f"{rev}:{path}"]).stdout

    def parent_of(self, rev: str) -> str:
        """Return the first parent of a commit, or "" for a root commit."""
        found = self._run_git(["rev-parse", "--verify", "--quiet", f"{rev}^"], check=False)
        return found.stdout.strip() if found.returncode == 0 else ""

    def get_head_sha(self) -> str:
        """Return the commit sha of HEAD."""
        res = self._run_git(["rev-parse", "HEAD"])
        return res.stdout.strip()

    def get_head_message(self) -> str:
        """Return the subject line of the HEAD commit."""
        res = self._run_git(["log", "-1", "--format=%s"])
        return res.stdout.strip()
