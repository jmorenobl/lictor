"""Lictor's skill for Claude Code: the stub a project keeps and what the installed version prints.

The project keeps only a stub, whose one instruction is to print the content of the installed
version (`lictor skill --show`). Upgrading Lictor upgrades the skill with it, and the stub itself
hardly ever changes, so `doctor` tells when it isn't this version's. No stub is a decision, and
nothing is said about it.
"""

import importlib.resources
from collections.abc import Sequence
from pathlib import Path

SKILL_FILE = Path(".claude/skills/lictor/SKILL.md")


def _packaged(name: str) -> str:
    return importlib.resources.files("lictor.skill").joinpath(name).read_text(encoding="utf-8")


def skill_stub() -> str:
    """What `lictor init` and `lictor skill` write in the project."""
    return _packaged("stub.md")


def render_skill(version: str, sections: Sequence[tuple[str, str]]) -> str:
    """The whole content: what holds for any project, then what each profile adds."""
    parts = [
        f"# Skill de Lictor (Lictor {version})",
        _packaged("body.md").strip(),
        *(f"## Perfil {name}\n\n{text.strip()}" for name, text in sections),
    ]
    return "\n\n".join(parts) + "\n"


def install_skill_stub(project_root: Path) -> Path:
    """Write the stub, replacing whatever is there: the file is Lictor's, not the project's."""
    path = project_root / SKILL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skill_stub(), encoding="utf-8")
    return path


def skill_stub_warning(project_root: Path) -> str | None:
    """Why the project's stub is worth telling about, if it is there and isn't this version's."""
    path = project_root / SKILL_FILE
    try:
        found = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        found = None
    if found == skill_stub():
        return None
    return (
        f"{SKILL_FILE.as_posix()} no es el esbozo de esta versión de Lictor: "
        "ejecuta `lictor skill`."
    )
