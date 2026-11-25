"""The commands of a project's CI, read with a real YAML parser (ADR-0015, F76).

Two places hold what a project checks before it integrates: its workflows and its pre-commit
configuration (two of the four projects measured have only the second). Read line by line they
are right in every real file and silently wrong in valid YAML (a plain scalar on two lines, a
step written as a flow mapping, a `run:` inside another step's script), so they are parsed, and
the line of each command comes from the parser's nodes. Nothing here knows a tool: which commands
check what is the survey's to say. Only the hook ids of the known remote repositories are named,
and they come from the profile.
"""

import shlex
from collections.abc import Mapping
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from lictor.domain.ci import CiStep, CiSteps

WORKFLOWS_DIR = (".github", "workflows")
PRE_COMMIT = ".pre-commit-config.yaml"
MAX_BYTES = 1 << 20
_REDIRECTS = "<>"
_OPERATORS = ";&|"


class _UnreadableError(Exception):
    """Why a file of the CI can't be read; it becomes a line of `unreadable`."""


class YamlCiReader:
    """Read the workflows and the pre-commit configuration of a tree; `hooks` maps the id of a
    known remote hook to the command it runs."""

    def __init__(self, hooks: Mapping[str, str]) -> None:
        self._hooks = dict(hooks)

    def read(self, root: Path) -> CiSteps:
        steps: list[CiStep] = []
        unreadable: list[str] = []
        for rel in _ci_files(root):
            try:
                node = _compose(root / rel)
                if node is None:
                    continue
                found = (
                    _hook_steps(rel, node, self._hooks)
                    if rel == PRE_COMMIT
                    else _workflow_steps(rel, node)
                )
                steps.extend(found)
            except _UnreadableError as why:
                unreadable.append(f"{rel}: {why}")
        return CiSteps(tuple(steps), tuple(unreadable))


def _ci_files(root: Path) -> list[str]:
    files: list[str] = []
    workflows = root.joinpath(*WORKFLOWS_DIR)
    if workflows.is_dir() and not workflows.is_symlink():
        files += sorted(
            "/".join((*WORKFLOWS_DIR, entry.name))
            for entry in workflows.iterdir()
            if entry.suffix in (".yml", ".yaml") and entry.is_file() and not entry.is_symlink()
        )
    pre_commit = root / PRE_COMMIT
    if pre_commit.is_file() and not pre_commit.is_symlink():
        files.append(PRE_COMMIT)
    return files


def _compose(path: Path) -> Node | None:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _UnreadableError(f"no se puede leer ({exc.strerror})") from exc
    if len(raw) > MAX_BYTES:
        raise _UnreadableError(f"ocupa más de {MAX_BYTES // 1024} KiB")
    try:
        return yaml.compose(raw.decode("utf-8"), Loader=yaml.SafeLoader)
    except UnicodeDecodeError as exc:
        raise _UnreadableError("no está en UTF-8") from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (línea {mark.line + 1})" if mark is not None else ""
        raise _UnreadableError(f"no es YAML válido{where}") from exc


def _get(node: Node | None, key: str) -> Node | None:
    """The value of `key` in a mapping node, or None if it isn't one or hasn't it."""
    if not isinstance(node, MappingNode):
        return None
    for name, value in node.value:
        if isinstance(name, ScalarNode) and name.value == key and isinstance(value, Node):
            return value
    return None


def _value(node: Node | None) -> object:
    """A node as the data `safe_load` would give for it."""
    if node is None:
        return None
    return yaml.SafeLoader("").construct_object(node, deep=True)


def _items(node: Node | None) -> list[Node]:
    return list(node.value) if isinstance(node, SequenceNode) else []


def _pairs(node: Node | None) -> list[Node]:
    return [value for _key, value in node.value] if isinstance(node, MappingNode) else []


def _workflow_steps(rel: str, root: Node) -> list[CiStep]:
    """Each command of each step's `run`; a step or job that may fail demands nothing."""
    found: list[CiStep] = []
    for job in _pairs(_get(root, "jobs")):
        if _value(_get(job, "continue-on-error")) is True:
            continue
        for step in _items(_get(job, "steps")):
            run = _get(step, "run")
            if not isinstance(run, ScalarNode) or _value(_get(step, "continue-on-error")) is True:
                continue
            found += [CiStep(rel, line, text, "workflow") for line, text in shell_commands(run)]
    return found


def _texts(node: Node | None) -> tuple[str, ...]:
    raw = _value(node)
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, list):
        return tuple(str(item) for item in raw)
    return ()


def _hook_steps(rel: str, root: Node, hooks: Mapping[str, str]) -> list[CiStep]:
    """Each hook that runs a command: a local one's `entry`, or a known remote hook's command."""
    default_stages = _texts(_get(root, "default_stages"))
    top_files, top_exclude = _texts(_get(root, "files")), _texts(_get(root, "exclude"))
    found: list[CiStep] = []
    for repo in _items(_get(root, "repos")):
        source = _value(_get(repo, "repo"))
        for hook in _items(_get(repo, "hooks")):
            run = _hook_command(source, hook, hooks)
            stages = _texts(_get(hook, "stages")) or default_stages
            if run is None or (stages and set(stages) <= {"manual"}):
                continue
            found.append(
                CiStep(
                    rel,
                    hook.start_mark.line + 1,
                    run,
                    "pre-commit",
                    pass_filenames=_value(_get(hook, "pass_filenames")) is not False,
                    files=top_files + _texts(_get(hook, "files")),
                    exclude=top_exclude + _texts(_get(hook, "exclude")),
                )
            )
    return found


def _hook_command(source: object, hook: Node, hooks: Mapping[str, str]) -> str | None:
    hook_id = _value(_get(hook, "id"))
    if source == "local":
        entry = _value(_get(hook, "entry"))
        command = entry if isinstance(entry, str) and entry.strip() else None
    elif source == "meta" or not isinstance(hook_id, str):
        command = None
    else:
        command = hooks.get(hook_id)
    if command is None:
        return None
    args = _texts(_get(hook, "args"))
    return f"{command} {shlex.join(args)}" if args else command


def shell_commands(node: ScalarNode) -> list[tuple[int, str]]:
    """The commands of a `run` script, each with the line of the file where it starts.

    A literal block keeps its lines; any other scalar is one line (YAML folds it), the one where
    it starts.
    """
    literal = node.style == "|"
    first = node.start_mark.line + (2 if literal else 1)
    return [
        (first + offset if literal else first, text) for offset, text in split_script(node.value)
    ]


def split_script(script: str) -> list[tuple[int, str]]:
    """The commands of a shell script, as written, each with the line (from 0) where it starts.

    They split at a line break and at `;`, `&&`, `||`, `|` and `&` outside quotes, so a quoted
    string may span lines; a backslash at the end of a line continues it, comments go, and so do
    redirections with their targets.
    """
    scan = _Scan(script)
    while scan.index < len(script):
        scan.step()
    scan.cut()
    return scan.found


class _Scan:
    """A pass over a script that cuts it into commands."""

    def __init__(self, script: str) -> None:
        self.script = script
        self.index = 0
        self.line = 0
        self.start = 0
        self.current = ""
        self.found: list[tuple[int, str]] = []

    def cut(self) -> None:
        text = " ".join(self.current.split())
        if text:
            self.found.append((self.start, text))
        self.current = ""

    def add(self, text: str) -> None:
        if not self.current.strip():
            self.start = self.line
        self.current += text
        self.line += text.count("\n")

    def step(self) -> None:
        script, index = self.script, self.index
        char = script[index]
        if char == "\\" and script[index + 1 : index + 2] == "\n":
            self.current += " "
            self.line += 1
            self.index = index + 2
        elif char in "'\"\\":
            end = _literal_end(script, index)
            self.add(script[index:end])
            self.index = end
        elif char == "#" and (not self.current or self.current[-1].isspace()):
            self.index = _past_comment(script, index)
        elif char == "\n" or char in _OPERATORS:
            self.cut()
            self.line += char == "\n"
            self.index = index + 1
        elif char in _REDIRECTS:
            self.current = _without_descriptor(self.current)
            self.index = _after_redirect(script, index)
        else:
            self.add(char)
            self.index = index + 1


def _literal_end(text: str, index: int) -> int:
    """Where what starts at `index` ends: an escaped character, or a quoted string."""
    if text[index] == "\\":
        return min(index + 2, len(text))
    quote = text[index]
    index += 1
    while index < len(text) and text[index] != quote:
        index += 2 if text[index] == "\\" and quote == '"' else 1
    return min(index + 1, len(text))


def _past_comment(text: str, index: int) -> int:
    end = text.find("\n", index)
    return len(text) if end < 0 else end


def _without_descriptor(current: str) -> str:
    """A command without the number of the descriptor a redirection starts with (`2>`)."""
    if current[-1:].isdigit() and current[-2:-1] in ("", " "):
        return current[:-1]
    return current


def _after_redirect(text: str, index: int) -> int:
    """Where the command goes on after a redirection: past its operator and its target."""
    while index < len(text) and text[index] in _REDIRECTS + "&":
        index += 1
    while index < len(text) and text[index] == " ":
        index += 1
    while index < len(text) and not text[index].isspace() and text[index] not in _OPERATORS:
        index += 1
    return index
