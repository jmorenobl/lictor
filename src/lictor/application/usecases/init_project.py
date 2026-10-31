"""InitProject use case for initializing a project with Lictor configuration."""

from dataclasses import dataclass
from pathlib import Path

from lictor.application.ports import ConfigInitializer
from lictor.domain.errors import ConfigError

CONFIG_FILE = "lictor.json"
STATE_DIR = ".lictor"


@dataclass(frozen=True)
class InitProjectRequest:
    """Request payload for InitProject use case."""

    project_root: Path
    profile_name: str | None = None
    model: str | None = None
    force: bool = False


@dataclass(frozen=True)
class InitProjectResult:
    """Result of InitProject use case."""

    config_path: Path
    profile_name: str
    gitignore_updated: bool
    note: str = ""


class InitProject:
    """Use case to initialize lictor.json, .lictor/ directory, and .gitignore entries."""

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

        lictor_dir = req.project_root / STATE_DIR
        lictor_dir.mkdir(parents=True, exist_ok=True)

        gitignore_updated = self._update_gitignore(
            project_root=req.project_root,
            entries=[f"{STATE_DIR}/", *init_data.gitignore_entries],
        )

        return InitProjectResult(
            config_path=config_path,
            profile_name=init_data.profile_name,
            gitignore_updated=gitignore_updated,
            note=init_data.note,
        )

    def _update_gitignore(self, project_root: Path, entries: list[str]) -> bool:
        gitignore_path = project_root / ".gitignore"
        existing_lines: list[str] = []
        if gitignore_path.is_file():
            existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines()

        normalized_existing = {line.strip() for line in existing_lines if line.strip()}
        to_add = [entry.strip() for entry in entries if entry.strip() not in normalized_existing]

        if not to_add:
            return False

        content = "\n".join(existing_lines).rstrip()
        if content:
            content += "\n"
        content += "\n".join(to_add) + "\n"

        gitignore_path.write_text(content, encoding="utf-8")
        return True
