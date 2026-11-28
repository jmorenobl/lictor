"""Git CLI adapter implementing the Vcs port."""

import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from lictor.application.ports import TextSearch, Vcs, Worktrees
from lictor.domain.conventions import TreeEntry
from lictor.domain.errors import CandidateError, EnvironmentUnavailableError
from lictor.domain.git_config import (
    ConfigEntries,
    changed_keys,
    config_change_text,
    config_entries,
    watched,
)
from lictor.domain.model import DELETED_MODE, Candidate, RepoPin, TreeChange
from lictor.domain.refactor_scope import LineSpan, hunk_span
from lictor.domain.run_standing import Link

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
# A linked worktree has its own directory, `<common>/worktrees/<name>`, and shares the rest; in a
# normal checkout both are `.git`, and the files are the same as before linked worktrees counted.
_WORKTREE_FILES = ("HEAD", "config.worktree")
_CONFIG = "config"
_COMMON_FILES = (_CONFIG,)
_COMMON_DIRS = ("hooks", "info")
# What says where the other two are: restored first, so that the rest goes back to its place.
_POINTERS = (".git", "commondir")
# Per worktree: two runs in two worktrees of one repository must not move a ref they share.
_EXPORT_REF = "refs/worktree/lictor/verified"


def _hunk_span(header: str) -> LineSpan:
    """The span of a hunk header `@@ -a,b +c,d @@`, where `,d` is left out when it is 1."""
    new = header.split(" ")[2].removeprefix("+")
    start, _, count = new.partition(",")
    return hunk_span(int(start), int(count) if count else 1)


_NO_USER_CONFIG = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
_EXECUTABLE_MODE = "100755"


def _points_to(dot_git: Path, entry: Path) -> bool:
    """Whether a worktree's `.git` file names that entry of the repository."""
    if not dot_git.is_file() or dot_git.is_symlink():
        return False
    pointer = dot_git.read_text(encoding="utf-8").strip()
    if not pointer.startswith("gitdir:"):
        return False
    return (dot_git.parent / pointer.removeprefix("gitdir:").strip()).resolve() == entry.resolve()


class GitCli(Vcs, Worktrees, TextSearch):
    """VCS implementation using the local git command line binary."""

    def __init__(self, repo_dir: Path | None = None, ignored: Sequence[str] = ()) -> None:
        """`ignored` holds gitignore patterns `capture` leaves out whatever the tree ignores.

        They are what the profile's own tools generate (F59): an agent that measures coverage
        leaves `.coverage` in its copy, and a project whose `.gitignore` lacks it must not get it
        in the candidate. They can only hide untracked files, as a `.gitignore` can.
        """
        self.repo_dir = Path(repo_dir) if repo_dir is not None else Path.cwd()
        self._git_binary = shutil.which("git") or "git"
        self._ignored = tuple(ignored)

    def _run_git(
        self,
        args: Sequence[str],
        check: bool = True,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = self._git_env()
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

    @staticmethod
    def _git_env() -> dict[str, str]:
        merged_env = {k: v for k, v in os.environ.items() if k not in _INHERITED_LOCATIONS}
        merged_env["GIT_NO_REPLACE_OBJECTS"] = "1"
        return merged_env

    def _git_bytes(self, args: Sequence[str]) -> bytes:
        """Run git as `_run_git` does and return its stdout untouched: a hash reads bytes."""
        return subprocess.run(
            [self._git_binary, *_HARDENED, *args],
            cwd=str(self.repo_dir),
            capture_output=True,
            check=True,
            env=self._git_env(),
        ).stdout

    def _git_dir(self) -> Path:
        """Locate the repository by reading, not by asking git, which would obey its config."""
        dot_git = self.repo_dir / ".git"
        if dot_git.is_file():
            pointer = dot_git.read_text(encoding="utf-8").removeprefix("gitdir:").strip()
            return (self.repo_dir / pointer).resolve()
        return dot_git.resolve()

    def _common_dir(self) -> Path:
        """Read `commondir`, which a linked worktree has and a normal checkout doesn't."""
        git_dir = self._git_dir()
        commondir = git_dir / "commondir"
        if commondir.is_file():
            return (git_dir / commondir.read_text(encoding="utf-8").strip()).resolve()
        return git_dir

    def _control_paths(self) -> dict[str, Path]:
        """Name each control file as it is named in a normal checkout, wherever it lives."""
        git_dir, common = self._git_dir(), self._common_dir()
        found = {".git": self.repo_dir / ".git", "commondir": git_dir / "commondir"}
        found |= {name: git_dir / name for name in _WORKTREE_FILES}
        found |= {name: common / name for name in _COMMON_FILES}
        for directory in _COMMON_DIRS:
            for path in (common / directory).rglob("*"):
                found[path.relative_to(common).as_posix()] = path
        return found

    def _control_files(self) -> dict[str, bytes]:
        return {
            name: path.read_bytes()
            for name, path in self._control_paths().items()
            if path.is_file() and not path.is_symlink()
        }

    def pin(self) -> RepoPin:
        """Record the repository: the control files in memory, where nothing else reaches."""
        return RepoPin(head=self.get_head_sha(), files=self._control_files())

    def restore(self, pin: RepoPin) -> list[str]:
        """Put the control files back before running git again, then bring HEAD back.

        The pointers go first, one at a time, and every other path is read again after them:
        a redirected `.git` or `commondir` would otherwise send the rest somewhere else.
        """
        changed: list[str] = []
        for pointer in _POINTERS:
            changed += self._restore_files(pin, only={pointer})
        changed += self._restore_files(pin, only=None)

        if self.get_head_sha() != pin.head:
            changed.append("HEAD")
            # `--keep` undoes what the commit changed and keeps what the user is editing.
            kept = self._run_git(["reset", "-q", "--keep", pin.head], check=False)
            if kept.returncode != 0:
                self._run_git(["update-ref", "HEAD", pin.head])
        return changed

    def _restore_files(self, pin: RepoPin, only: set[str] | None) -> list[str]:
        paths = self._control_paths()
        current = self._control_files()
        changed: list[str] = []
        for name in sorted(current.keys() | pin.files.keys()):
            if (only is not None and name not in only) or current.get(name) == pin.files.get(name):
                continue
            if name == _CONFIG:
                keys = self._config_keys(pin, current.get(name))
                if keys == ():
                    # Only inert keys changed: what wrote them (an editor, the person's own
                    # branch) keeps them (S22 of 0.25.0).
                    continue
                changed.append(config_change_text(keys or ()))
            else:
                changed.append(name if name == ".git" else f".git/{name}")
            target = paths.get(name) or self._common_dir() / name
            if name in pin.files:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.unlink(missing_ok=True)
                target.write_bytes(pin.files[name])
            else:
                target.unlink()
        return changed

    def _config_keys(self, pin: RepoPin, now: bytes | None) -> tuple[str, ...] | None:
        """The guarded keys a change of the config touched; () when only inert ones did, and
        None when either version can't be read as a config, which is a change all the same."""
        pinned = pin.files.get(_CONFIG)
        if pinned is None or now is None:
            return None  # created or removed: the file itself changed
        branch = _branch_of(pin.files.get("HEAD", b""))
        before = _read_config(self._git_binary, pinned)
        after = _read_config(self._git_binary, now)
        if before is None or after is None:
            return None
        return changed_keys(before, after, branch)

    def is_linked_worktree(self) -> bool:
        """A linked worktree's `.git` is a file, and its directory has a `commondir`."""
        return (self.repo_dir / ".git").is_file() and (self._git_dir() / "commondir").is_file()

    def current_branch(self) -> str:
        """Read HEAD as a file: `ref: refs/heads/<branch>`, or a sha when it is detached."""
        head = (self._git_dir() / "HEAD").read_text(encoding="utf-8").strip()
        return head.removeprefix("ref: refs/heads/") if head.startswith("ref: refs/heads/") else ""

    def branch_exists(self, branch: str) -> bool:
        """Look the name up under `refs/heads/` only: a tag with the same name isn't a branch."""
        found = self._run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], False)
        return found.returncode == 0

    def add_worktree(self, path: Path, branch: str) -> None:
        """Create the worktree with the same hardening as every call: `post-checkout` won't run."""
        path.parent.mkdir(parents=True, exist_ok=True)
        added = self._run_git(
            ["worktree", "add", "--quiet", "-b", branch, str(path), "HEAD"], check=False
        )
        if added.returncode != 0:
            raise CandidateError(
                f"No se pudo crear el worktree de la rama {branch} en {path}: "
                f"{added.stderr.strip()}"
            )

    def remove_worktree(self, path: Path) -> None:
        """Never `--force`: what git would refuse to lose isn't Lictor's to throw away."""
        removed = self._run_git(["worktree", "remove", str(path)], check=False)
        if removed.returncode != 0:
            raise CandidateError(f"No se pudo retirar el worktree {path}: {removed.stderr.strip()}")

    def link_of(self, path: Path) -> Link:
        """Find git's entry for the worktree among this repository's, reading files only.

        Each entry's `gitdir` names the worktree's `.git`, relative to the entry with
        `worktree.useRelativePaths`; both sides are resolved, so a path through a link (macOS's
        `/var`) still matches. The entry without a `.git` pointing back is what a moved checkout
        or a deleted `.git` leaves, and `git worktree repair` fixes; a stale entry for the same
        path beside the live one doesn't make a sound worktree unlinked.
        """
        wanted = (path / ".git").resolve()
        entries = self._common_dir() / "worktrees"
        named = [
            entry
            for entry in (sorted(entries.iterdir()) if entries.is_dir() else ())
            if (entry / "gitdir").is_file()
            and (entry / (entry / "gitdir").read_text(encoding="utf-8").strip()).resolve() == wanted
        ]
        if any(_points_to(path / ".git", entry) for entry in named):
            return "linked"
        return "unlinked" if named else "orphan"

    def delete_branch(self, branch: str) -> None:
        """`-d`, never `-D`: a branch that isn't integrated stays."""
        deleted = self._run_git(["branch", "-d", branch], check=False)
        if deleted.returncode != 0:
            raise CandidateError(f"No se ha borrado la rama {branch}: {deleted.stderr.strip()}")

    def fingerprint(self) -> str:
        """Hash the control files, read as bytes, and of the config only what Lictor guards.

        No git command runs in the repository: the config is read from a copy, by `git config
        --file` outside it, which follows no include and runs nothing (S22 of 0.25.0).
        """
        files = self._control_files()
        branch = _branch_of(files.get("HEAD", b""))
        digest = hashlib.sha256()
        for name, content in sorted(files.items()):
            hashed = (
                _guarded_config(self._git_binary, content, branch) if name == _CONFIG else content
            )
            digest.update(name.encode() + b"\0" + hashed + b"\0")
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
        `.gitignore` in `workdir` can only hide files from the result. The patterns given as
        `ignored` count as one more such file. A tracked file is always compared, so it can't be
        hidden, and a deletion counts like any other change.
        """
        with tempfile.TemporaryDirectory(prefix="lictor-index-") as tmp:
            excludes = Path(tmp) / "exclude"
            excludes.write_text("".join(f"{p}\n" for p in self._ignored), encoding="utf-8")
            env = {
                "GIT_INDEX_FILE": str(Path(tmp) / "index"),
                "GIT_DIR": str(self._git_dir()),
                "GIT_WORK_TREE": str(workdir),
                **_NO_USER_CONFIG,
            }
            self._run_git(["read-tree", "HEAD"], env=env)
            added = self._run_git(
                ["-c", f"core.excludesFile={excludes}", "add", "-A"],
                check=False,
                env=env,
                cwd=workdir,
            )
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
            old_mode, new_mode, *_ = header.split(" ")
            found.append(TreeChange(path=path, mode=new_mode, old_mode=old_mode.lstrip(":")))
        return found

    def changed_paths(self, base: str, head: str) -> list[str]:
        """List the paths that differ between two commits; renames count as two paths."""
        raw = self._run_git(
            ["diff-tree", "-r", "-z", "--name-only", "--no-renames", base, head]
        ).stdout
        return [path for path in raw.split("\0") if path]

    def changed_lines(self, base: str, head: str) -> dict[str, tuple[LineSpan, ...]]:
        """Read, file by file, the lines `head` adds or changes over `base`, in its numbering.

        One file per diff, so no line has to be told apart as the header of another file, and the
        path goes as a literal pathspec, never parsed back from the patch.
        """
        raw = self._run_git(["diff-tree", "-r", "-z", "--raw", "--no-renames", base, head]).stdout
        fields = raw.split("\0")
        kept = [
            path
            for header, path in zip(fields[0::2], fields[1::2], strict=False)
            if header.split(" ")[1] != DELETED_MODE
        ]
        return {path: self._hunks(base, head, path) for path in kept}

    def _hunks(self, base: str, head: str, path: str) -> tuple[LineSpan, ...]:
        patch = self._run_git(
            [
                "diff-tree",
                "-p",
                "-U0",
                "--text",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                base,
                head,
                "--",
                f":(literal){path}",
            ]
        ).stdout
        # Every line inside a hunk starts with its sign, and no header line with `@@ `.
        return tuple(_hunk_span(line) for line in patch.splitlines() if line.startswith("@@ "))

    def added_lines(self, tree: str) -> list[str]:
        """Read the added lines of the diff, binary files included: a NUL must not hide them."""
        return self._diff_lines(tree, "+")

    def removed_lines(self, tree: str) -> list[str]:
        """Read the removed lines of the diff, as `added_lines` reads the added ones."""
        return self._diff_lines(tree, "-")

    def _diff_lines(self, tree: str, sign: str) -> list[str]:
        """The lines of the diff from HEAD to `tree` that start with `sign`, inside its hunks."""
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
        found: list[str] = []
        in_hunk = False
        for line in patch.splitlines():
            if line.startswith("diff --git "):
                in_hunk = False
            elif line.startswith("@@"):
                in_hunk = True
            elif in_hunk and line.startswith(sign):
                found.append(line[1:])
        return found

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

    def tree_entries(self, rev: str) -> list[TreeEntry]:
        """Every file and link of a commit, at any depth, with its mode; nothing is checked out."""
        raw = self._run_git(["ls-tree", "-r", "-z", f"{rev}^{{tree}}"]).stdout
        entries = []
        for record in raw.split("\0"):
            if not record:
                continue
            meta, path = record.split("\t", 1)
            entries.append(TreeEntry(path=path, mode=meta.split(" ", 1)[0]))
        return entries

    def read_bytes(self, rev: str, path: str) -> bytes:
        """Read a file of a commit, or a link's target, as the bytes git keeps."""
        return self._git_bytes(["cat-file", "blob", f"{rev}:{path}"])

    def cited_in(self, word: str) -> list[str]:
        """The files of HEAD that hold `word` as a whole word, read from the commit, not the disk.

        It is a fixed string, so a name can't be read as a pattern or an option.
        """
        found = self._run_git(
            ["grep", "-l", "-w", "-F", "-I", "-e", word, "HEAD", "--"], check=False
        )
        if found.returncode not in (0, 1):
            return []
        prefix = "HEAD:"
        return sorted(line.removeprefix(prefix) for line in found.stdout.splitlines() if line)

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

    def resolve_commit(self, ref: str) -> str | None:
        """Ask quietly for the commit a reference names; `--end-of-options` keeps it a name."""
        found = self._run_git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
            check=False,
        )
        return found.stdout.strip() if found.returncode == 0 else None

    def merge_base(self, a: str, b: str) -> str | None:
        """git exits 1 with nothing to say when two commits share no ancestor it has."""
        found = self._run_git(["merge-base", "--end-of-options", a, b], check=False)
        return found.stdout.strip() if found.returncode == 0 and found.stdout.strip() else None

    def has_commits(self) -> bool:
        """Ask for HEAD's commit quietly: without one, git fails and says nothing."""
        res = self._run_git(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], check=False)
        return res.returncode == 0

    def get_head_sha(self) -> str:
        """Return the commit sha of HEAD."""
        res = self._run_git(["rev-parse", "HEAD"])
        return res.stdout.strip()

    def get_head_message(self) -> str:
        """Return the subject line of the HEAD commit."""
        res = self._run_git(["log", "-1", "--format=%s"])
        return res.stdout.strip()


def _branch_of(head: bytes) -> str:
    """The branch a HEAD file names, or "" when it is detached or unreadable."""
    text = head.decode("utf-8", errors="replace").strip()
    return text.removeprefix("ref: refs/heads/") if text.startswith("ref: refs/heads/") else ""


def _read_config(git: str, content: bytes) -> ConfigEntries | None:
    """Read a config's entries with git's own parser, from a copy outside every repository.

    `--file` reads that file alone, without includes, and nothing global or of the system: no
    key of it can make git run anything here. None when git doesn't read it as a config.
    """
    with tempfile.TemporaryDirectory(prefix="lictor-config-") as tmp:
        copy = Path(tmp) / "config"
        copy.write_bytes(content)
        env = {
            **GitCli._git_env(),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CEILING_DIRECTORIES": tmp,
        }
        read = subprocess.run(
            [git, *_HARDENED, "config", "--file", str(copy), "--no-includes", "--list", "-z"],
            cwd=tmp,
            capture_output=True,
            check=False,
            env=env,
        )
    if read.returncode != 0:
        return None
    return config_entries(read.stdout.decode("utf-8", errors="surrogateescape"))


def _guarded_config(git: str, content: bytes, branch: str) -> bytes:
    """What an anchor hashes of the config: its guarded entries, or its bytes if unreadable."""
    entries = _read_config(git, content)
    if entries is None:
        return content
    return "\0".join(f"{key}\n{value}" for key, value in watched(entries, branch)).encode(
        "utf-8", errors="surrogateescape"
    )
