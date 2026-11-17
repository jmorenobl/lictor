"""Lictor's skill for Claude Code: the stub a project keeps and what the installed version prints.

The project keeps only a stub, whose one instruction is to print the content of the installed
version (`lictor skill --show`). Upgrading Lictor upgrades the skill with it, and the stub itself
hardly ever changes, so `doctor` tells when it isn't this version's. No stub is a decision, and
nothing is said about it.
"""

import importlib.resources
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lictor.domain.errors import ConfigError

SKILL_FILE = Path(".claude/skills/lictor/SKILL.md")
# Where the body takes the essentials of the profile, and where a profile's text splits in two.
# Claude Code keeps only the start of a skill when it compacts a conversation (about 5000
# tokens), so what must survive goes first, and the rest after the body.
PROFILE_SLOT = "<!-- lictor: perfil -->"
DETAIL_MARK = "<!-- lictor: detalle -->"
END_LINE = "Fin de la skill de Lictor."


def _packaged(name: str) -> str:
    return importlib.resources.files("lictor.skill").joinpath(name).read_text(encoding="utf-8")


def skill_stub() -> str:
    """What `lictor init` and `lictor skill` write in the project."""
    return _packaged("stub.md")


@dataclass(frozen=True)
class ProfileSkill:
    """What a profile adds to the skill: the essentials, which go up, and the detail."""

    name: str
    essentials: str
    detail: str

    @property
    def summary(self) -> str:
        """The first line of the essentials, which says what projects the profile is for."""
        return self.essentials.splitlines()[0]


def parse_profile_skill(name: str, text: str) -> ProfileSkill:
    """Split a profile's `<name>.skill.md` at its one detail mark."""
    essentials, mark, detail = text.partition(DETAIL_MARK)
    if not mark or DETAIL_MARK in detail or not essentials.strip() or not detail.strip():
        msg = (
            f"La skill del perfil '{name}' necesita lo esencial, una línea {DETAIL_MARK} "
            "y el detalle"
        )
        raise ConfigError(msg)
    return ProfileSkill(name=name, essentials=essentials.strip(), detail=detail.strip())


@dataclass(frozen=True)
class ProjectProfile:
    """What the project says of its profile: what `lictor.json` names, or `lictor init` finds."""

    declared: str | None = None
    # Why `lictor.json` is there and doesn't say its profile.
    unreadable: str | None = None
    detected: str | None = None


@dataclass(frozen=True)
class ProfilePart:
    """What goes where the body takes the profile, and the detail at the end, if any."""

    essentials: str
    detail: str | None = None


def _by_name(profiles: Sequence[ProfileSkill], name: str | None) -> ProfileSkill | None:
    return next((profile for profile in profiles if profile.name == name), None)


def _chosen(profile: ProfileSkill, note: str | None) -> ProfilePart:
    lead = f"{note}\n\n" if note else ""
    return ProfilePart(
        essentials=f"## Perfil {profile.name}: lo esencial\n\n{lead}{profile.essentials}",
        detail=f"## Perfil {profile.name}: el detalle\n\n{profile.detail}",
    )


def profile_part(
    project: ProjectProfile,
    profiles: Sequence[ProfileSkill],
    requested: str | None = None,
) -> ProfilePart:
    """The profile the skill speaks of: the one asked for, the project's, or a list to ask from.

    Only one profile goes in, so it stays within what a compacted conversation keeps. The one
    `lictor.json` names wins even if the file doesn't load, because that is when the skill helps
    most; without it, the one `lictor init` would detect; and with neither, nothing is guessed:
    the person chooses from the list.
    """
    names = ", ".join(profile.name for profile in profiles)
    if requested is not None:
        asked = _by_name(profiles, requested)
        if asked is None:
            msg = f"Perfil desconocido: '{requested}'. Los de esta versión: {names}"
            raise ConfigError(msg)
        return _chosen(asked, None)
    declared = _by_name(profiles, project.declared)
    if declared is not None:
        return _chosen(declared, None)
    if project.declared is not None:
        why = f"`lictor.json` pide el perfil «{project.declared}», que esta versión no tiene"
    elif project.unreadable is not None:
        why = f"`lictor.json` no dice su perfil ({project.unreadable}; `lictor doctor` lo detalla)"
    else:
        why = "Aún no hay `lictor.json`"
    detected = _by_name(profiles, project.detected)
    if detected is not None:
        return _chosen(detected, f"{why}: este es el perfil que `lictor init` detecta aquí.")
    listed = "\n".join(f"- `{profile.name}`: {profile.summary}" for profile in profiles)
    return ProfilePart(
        essentials=(
            "## Perfil del proyecto\n\n"
            f"{why}, y ningún perfil encaja con lo que hay en el directorio. Los de esta "
            f"versión:\n\n{listed}\n\n"
            "Pregunta a la persona con cuál va a trabajar y lee su parte con "
            "`lictor skill --show --profile <nombre>` antes de preparar el proyecto."
        )
    )


def render_skill(version: str, profile: ProfilePart) -> str:
    """The whole content: the body with the profile's essentials early, its detail at the end.

    It ends with `END_LINE`, which the header names: a skill cut by a compacted conversation
    lacks it, and the model knows to print it again.
    """
    body = _packaged("body.md").strip()
    if body.count(PROFILE_SLOT) != 1:
        msg = f"El cuerpo de la skill necesita una línea {PROFILE_SLOT}"
        raise ConfigError(msg)
    parts = [
        f"# Skill de Lictor (Lictor {version})",
        (
            f"Lo que imprime Lictor termina con la línea «{END_LINE}». Si no la ves, está "
            "recortado (pasa al compactar la conversación): vuelve a ejecutar "
            "`lictor skill --show`, con el mismo `--profile` si lo llevaba, y sigue lo que imprima."
        ),
        body.replace(PROFILE_SLOT, profile.essentials),
        *([profile.detail] if profile.detail else []),
        END_LINE,
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
