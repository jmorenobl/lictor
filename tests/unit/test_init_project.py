from collections.abc import Sequence
from pathlib import Path

import pytest

from lictor.application.ports import ConfigInitializer, ProfileInitConfig
from lictor.application.skill import SKILL_FILE, skill_stub
from lictor.application.usecases.init_project import (
    InitProject,
    InitProjectRequest,
)
from lictor.domain.errors import ConfigError


class FakeConfigInitializer(ConfigInitializer):
    def __init__(
        self,
        profile_name: str = "python-uv@0.1",
        gitignore_entries: Sequence[str] = ("__pycache__/", ".venv/"),
        config_json: str = '{"profile": "python-uv@0.1"}',
    ) -> None:
        self.model = "claude-sonnet-5-5"
        self.hints = "Python >=3.14, gestor uv."
        self.profile_name = profile_name
        self.gitignore_entries = gitignore_entries
        self.config_json = config_json

    def generate_config(
        self,
        project_root: Path,
        profile_name: str | None = None,
        model: str | None = None,
    ) -> ProfileInitConfig:
        return ProfileInitConfig(
            profile_name=self.profile_name,
            gitignore_entries=self.gitignore_entries,
            config_json=self.config_json,
            model=self.model,
            hints=self.hints,
        )


def test_init_project_creates_files_and_gitignore(tmp_path: Path) -> None:
    initializer = FakeConfigInitializer()
    use_case = InitProject(initializer)

    res = use_case.execute(InitProjectRequest(project_root=tmp_path))

    assert res.config_path == tmp_path / "lictor.json"
    assert (tmp_path / "lictor.json").read_text(
        encoding="utf-8"
    ).strip() == '{"profile": "python-uv@0.1"}'
    assert (tmp_path / ".lictor").is_dir()

    gitignore_content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".lictor/" in gitignore_content
    assert "__pycache__/" in gitignore_content
    assert ".venv/" in gitignore_content


def test_init_project_existing_config_without_force_fails(tmp_path: Path) -> None:
    (tmp_path / "lictor.json").write_text("existing", encoding="utf-8")
    initializer = FakeConfigInitializer()
    use_case = InitProject(initializer)

    with pytest.raises(ConfigError) as exc_info:
        use_case.execute(InitProjectRequest(project_root=tmp_path, force=False))
    assert exc_info.value.exit_code == 10
    assert "ya existe" in str(exc_info.value)


def test_init_project_existing_config_with_force_overwrites(tmp_path: Path) -> None:
    (tmp_path / "lictor.json").write_text("existing", encoding="utf-8")
    initializer = FakeConfigInitializer(config_json='{"overwritten": true}')
    use_case = InitProject(initializer)

    res = use_case.execute(InitProjectRequest(project_root=tmp_path, force=True))
    assert res.config_path.is_file()
    assert (tmp_path / "lictor.json").read_text(encoding="utf-8").strip() == '{"overwritten": true}'


def test_init_project_existing_gitignore_preserves_content_and_appends_missing(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("# My rules\n.venv/\n", encoding="utf-8")
    initializer = FakeConfigInitializer(gitignore_entries=["__pycache__/", ".venv/"])
    use_case = InitProject(initializer)

    use_case.execute(InitProjectRequest(project_root=tmp_path))

    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert content.startswith("# My rules\n.venv/\n")
    assert ".lictor/" in content
    assert "__pycache__/" in content
    # .venv/ should not be duplicated
    assert content.count(".venv/") == 1


def test_init_project_installs_the_skill_stub_unless_told_not_to(tmp_path: Path) -> None:
    with_skill = InitProject(FakeConfigInitializer()).execute(InitProjectRequest(tmp_path))
    assert with_skill.skill_path == tmp_path / SKILL_FILE
    assert with_skill.skill_path.read_text(encoding="utf-8") == skill_stub()

    other = tmp_path / "otro"
    other.mkdir()
    without = InitProject(FakeConfigInitializer()).execute(InitProjectRequest(other, skill=False))
    assert without.skill_path is None
    assert not (other / ".claude").exists()


def test_init_project_says_what_it_decided(tmp_path: Path) -> None:
    """The person has to know what `init` chose for them, and what is left (F08)."""
    (tmp_path / ".gitignore").write_text(".venv/\n", encoding="utf-8")

    res = InitProject(FakeConfigInitializer()).execute(InitProjectRequest(tmp_path))

    assert res.profile_name == "python-uv@0.1"
    assert res.model == "claude-sonnet-5-5"
    assert res.hints == "Python >=3.14, gestor uv."
    assert res.gitignore_added == (".lictor/", "__pycache__/")


def test_init_project_with_a_complete_gitignore_adds_nothing(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".lictor/\n__pycache__/\n.venv/\n", encoding="utf-8")

    res = InitProject(FakeConfigInitializer()).execute(InitProjectRequest(tmp_path))

    assert res.gitignore_added == ()
    assert not res.gitignore_updated
