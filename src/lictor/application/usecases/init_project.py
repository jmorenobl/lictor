"""InitProject use case for initializing a project with Lictor configuration."""

from dataclasses import dataclass
from pathlib import Path

from lictor.application.ports import ConfigInitializer
from lictor.application.run_files import own_dir
from lictor.application.skill import install_skill_stub
from lictor.domain.errors import ConfigError
from lictor.domain.gitignore import missing_entries

CONFIG_FILE = "lictor.json"
STATE_DIR = ".lictor"


@dataclass(frozen=True)
class InitProjectRequest:
    """Request payload for InitProject use case."""

    project_root: Path
    profile_name: str | None = None
    model: str | None = None
    force: bool = False
    skill: bool = True


@dataclass(frozen=True)
class InitProjectResult:
    """Result of InitProject use case."""

    config_path: Path
    profile_name: str
    gitignore_updated: bool
    skill_path: Path | None = None
    model: str = ""
    hints: str = ""
    gitignore_added: tuple[str, ...] = ()
    gitignore_checked: bool = False  # the profile named entries for it, added or already there
    to_write: tuple[str, ...] = ()
    status: str = "stable"


class InitProject:
    """Use case to initialize lictor.json, .lictor/, .gitignore entries and the skill's stub."""

    def __init__(self, initializer: ConfigInitializer) -> None:
        self.initializer = initializer

    def execute(self, req: InitProjectRequest) -> InitProjectResult:
        """Execute project initialization."""
        config_path = req.project_root / CONFIG_FILE
        if config_path.exists() and not req.force:
            raise ConfigError(
                f"El archivo '{CONFIG_FILE}' ya existe (usa --force para sobrescribir)"
            )

        init_data = self.initializer.generate_config(
            project_root=req.project_root,
            profile_name=req.profile_name,
            model=req.model,
        )

        config_path.write_text(init_data.config_json.rstrip() + "\n", encoding="utf-8")

        # Its own `.gitignore` makes git ignore all of `.lictor/` (F53): the project's is left
        # alone for it, and only gets what the profile's tools leave in a working tree (G6).
        own_dir(req.project_root)

        added = self._update_gitignore(
            project_root=req.project_root,
            entries=list(init_data.gitignore_entries),
        )

        return InitProjectResult(
            config_path=config_path,
            profile_name=init_data.profile_name,
            gitignore_updated=bool(added),
            skill_path=install_skill_stub(req.project_root) if req.skill else None,
            model=init_data.model,
            hints=init_data.hints,
            gitignore_added=added,
            gitignore_checked=bool(init_data.gitignore_entries),
            to_write=tuple(init_data.to_write),
            status=init_data.status,
        )

    def _update_gitignore(self, project_root: Path, entries: list[str]) -> tuple[str, ...]:
        """Append the entries the file lacks, and return them."""
        gitignore_path = project_root / ".gitignore"
        existing_lines: list[str] = []
        if gitignore_path.is_file():
            existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines()

        to_add = missing_entries(existing_lines, entries)

        if not to_add:
            return ()

        content = "\n".join(existing_lines).rstrip()
        if content:
            content += "\n"
        content += "\n".join(to_add) + "\n"

        gitignore_path.write_text(content, encoding="utf-8")
        return tuple(to_add)
