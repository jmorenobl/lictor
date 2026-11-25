"""The project's own rules of code, which the person writes in `lictor.json` (F79).

A project keeps its rules in files written for people and for other agents (`AGENTS.md`,
`CLAUDE.md`…), mixed with workflow, environment and description that Lictor's agents must not
follow. So nothing reads those files in a run: the person, with the skill, copies only the rules
of code into `conventions.text`, and `conventions.reviewed` keeps the hash of each file's content
as it was when it was read. `doctor` tells each file whose content no longer matches, which is a
fact exact even in a shallow clone; the person decides whether anything changes.

This module decides which files are rule files, how a link and a copy count, what `doctor` says
and what the prompts get. Nothing verifies that the agents follow the rules: `report.md` says so.
"""

import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lictor.domain.stages import spoken_list

# A prompt spends it every call, and the rules of code of the projects measured fit well below.
MAX_CONVENTIONS_CHARS = 4000
CONVENTIONS_CHECK = "Reglas del proyecto"
UNVERIFIED_NOTE = "Lictor las da a los agentes y no comprueba que se sigan."

PromptRole = Literal["driver", "implement", "refactor"]


_FILE_MODES = ("100644", "100755")
_LINK_MODE = "120000"


@dataclass(frozen=True)
class TreeEntry:
    """A path of a commit's tree with its git mode."""

    path: str
    mode: str

    @property
    def is_file(self) -> bool:
        return self.mode in _FILE_MODES

    @property
    def is_link(self) -> bool:
        return self.mode == _LINK_MODE


def too_long_message(length: int) -> str:
    """Why `conventions.text` is refused at load, and what to do."""
    return (
        f"conventions.text tiene {length} caracteres y el máximo es {MAX_CONVENTIONS_CHARS}: "
        "llega a cada prompt. Deja solo las reglas de código que el agente no deduciría del "
        "propio código; lo de una tarea va en hints."
    )


def is_rule_file(path: str, names: Sequence[str]) -> bool:
    """Whether a path of the tree is one of the profile's rule files, at any depth.

    A name with a directory (`.github/copilot-instructions.md`) matches that ending; one without,
    the file's name. Case is ignored, as on macOS `claude.md` opens as `CLAUDE.md`.
    """
    lowered = path.lower()
    for name in names:
        wanted = name.lower()
        if "/" in wanted:
            if lowered == wanted or lowered.endswith(f"/{wanted}"):
                return True
        elif posixpath.basename(lowered) == wanted:
            return True
    return False


def link_target(link: str, target: str) -> str | None:
    """The path of the repository a symbolic link points to, or None if it leaves it."""
    if not target or target.startswith("/"):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(link), target))
    if resolved in (".", "..") or resolved.startswith("../"):
        return None
    return resolved


@dataclass(frozen=True)
class FoundRuleFile:
    """A rule file of the tree: its path, the file it reads (itself, or a link's target), and
    the hash of that content."""

    path: str
    resolved: str
    digest: str


@dataclass(frozen=True)
class RuleFile:
    """One content of rules, with every path that reads it: `aliases` are the others.

    In pyverifactu `CLAUDE.md` is a link to `AGENTS.md`: one file to review, not two.
    """

    path: str
    digest: str
    aliases: tuple[str, ...] = ()

    @property
    def paths(self) -> tuple[str, ...]:
        return (self.path, *self.aliases)


def by_content(found: Sequence[FoundRuleFile]) -> tuple[RuleFile, ...]:
    """Count each content once; it is named by a path that isn't a link, if one reads it."""
    groups: dict[str, list[FoundRuleFile]] = {}
    for item in found:
        groups.setdefault(item.digest, []).append(item)
    files = []
    for digest, members in groups.items():
        paths = sorted({m.path for m in members})
        own = sorted({m.path for m in members if m.path == m.resolved})
        main = (own or paths)[0]
        files.append(RuleFile(main, digest, tuple(p for p in paths if p != main)))
    return tuple(sorted(files, key=lambda f: f.path))


def is_reviewed(file: RuleFile, reviewed: Mapping[str, str]) -> bool:
    """Whether the person reviewed this content under any of the paths that read it."""
    return any(reviewed.get(path) == file.digest for path in file.paths)


def unreviewed(files: Sequence[RuleFile], reviewed: Mapping[str, str]) -> tuple[RuleFile, ...]:
    """The rule files whose content isn't the one the person reviewed, or was never reviewed."""
    return tuple(file for file in files if not is_reviewed(file, reviewed))


def reviewed_record(files: Sequence[RuleFile]) -> dict[str, str]:
    """What `lictor conventions --reviewed` writes: each content's hash, under its path."""
    return {file.path: file.digest for file in files}


def rule_file_name(file: RuleFile) -> str:
    """`AGENTS.md`, or `AGENTS.md (y CLAUDE.md, con el mismo contenido)`."""
    if not file.aliases:
        return f"`{file.path}`"
    aliases = spoken_list([f"`{alias}`" for alias in file.aliases])
    return f"`{file.path}` (y {aliases}, con el mismo contenido)"


def rule_file_state(file: RuleFile, reviewed: Mapping[str, str]) -> str:
    """«revisado», «cambió desde que se revisó» or «sin revisar»."""
    if is_reviewed(file, reviewed):
        return "revisado"
    if any(path in reviewed for path in file.paths):
        return "cambió desde que se revisó"
    return "sin revisar"


def conventions_warning(files: Sequence[RuleFile], reviewed: Mapping[str, str]) -> str | None:
    """What `doctor` warns of the rule files not reviewed as they are now, or None."""
    stale = unreviewed(files, reviewed)
    if not stale:
        return None
    states = "; ".join(f"{rule_file_name(f)}: {rule_file_state(f, reviewed)}" for f in stale)
    return (
        f"{states}. Léelos con la skill de Lictor, pasa a conventions.text de lictor.json solo "
        "las reglas de código y ejecuta `lictor conventions --reviewed`; si no hay nada que "
        "cambiar, basta esa orden."
    )


def _precedence(role: PromptRole) -> str:
    first = "las reglas y rutas de Lictor de este mensaje"
    if role == "driver":
        first += " y las instrucciones del perfil"
    order = [
        first,
        "los valores literales del escenario",
        "el contexto del proyecto, que es la indicación de esta tarea",
        "estas reglas del proyecto",
    ]
    if role == "refactor":
        order.append("los objetivos de calidad")
    return (
        f"Si dos indicaciones chocan, manda {', después '.join(order)}. Si una indicación de esta "
        "tarea contradice una regla del proyecto, sigue la indicación."
    )


def conventions_block(text: str, role: PromptRole) -> str:
    """The project's rules for a prompt, with the order that settles a clash, or "" without them.

    It ends with a blank line, so a prompt writes it right before what follows it.
    """
    rules = text.strip()
    if not rules:
        return ""
    names = (
        "\nLos nombres del código que llaman los pasos (módulos, funciones, parámetros) siguen "
        "estas reglas, no el idioma del Gherkin, salvo un valor literal que fije el escenario."
        if role == "driver"
        else ""
    )
    return f"Reglas del proyecto:\n{rules}\n\n{_precedence(role)}{names}\n\n"


def conventions_report_lines(recorded: object) -> list[str]:
    """The section of `report.md` with the rules the run's agents got, from `run.started`.

    Said once, and said to be unverified; a run without rules, or a journal from before the
    field, has none.
    """
    text = recorded.get("text") if isinstance(recorded, dict) else None
    if not isinstance(text, str) or not text.strip():
        return []
    quoted = [f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()]
    return ["", "## Reglas del proyecto", f"*{UNVERIFIED_NOTE}*", "", *quoted]


def describe_conventions(text: str, files: Sequence[RuleFile], reviewed: Mapping[str, str]) -> str:
    """What `lictor conventions` prints: the rules, and the state of each rule file of HEAD."""
    rules = text.strip() or "(ninguna: conventions.text está vacío)"
    lines = ["Reglas del proyecto (conventions.text):", rules, ""]
    if not files:
        lines.append("Ningún fichero de reglas en HEAD.")
        return "\n".join(lines) + "\n"
    lines.append("Ficheros de reglas en HEAD:")
    lines += [f"- {rule_file_name(f)}: {rule_file_state(f, reviewed)}" for f in files]
    if unreviewed(files, reviewed):
        lines += [
            "",
            "Revisa los que no lo están con la skill de Lictor y, cuando conventions.text recoja "
            "sus reglas de código, ejecuta `lictor conventions --reviewed`.",
        ]
    return "\n".join(lines) + "\n"


def describe_recorded(files: Sequence[RuleFile]) -> str:
    """What `lictor conventions --reviewed` says it wrote."""
    if not files:
        return "Ningún fichero de reglas en HEAD: conventions.reviewed queda vacío.\n"
    names = spoken_list([rule_file_name(f) for f in files])
    return f"Revisados en lictor.json, como están en HEAD: {names}.\n"
