"""Fixtures for the audit reproduction cases."""

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lictor.cli import app
from tests.integration.repro.harness import (
    copy_project,
    git,
    isolated_env,
    require,
    shell,
    uv_sync,
)

DEMO_IVA_DIR = Path(__file__).resolve().parents[3] / "examples" / "demo-iva"
DEMO_IVA_FILES = (".python-version", "README.md", "pyproject.toml", "uv.lock")

ProjectFactory = Callable[..., Path]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    for item in items:
        if item.path.is_relative_to(here):
            item.add_marker(pytest.mark.repro)


@pytest.fixture(autouse=True)
def _host_independent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in isolated_env().items():
        monkeypatch.setenv(name, value)
    # c1 and c2 depend on the project writing and reading bytecode as it does in a real run.
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)


@pytest.fixture(scope="session")
def template(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Build a demo-iva repo configured by the real `lictor init`, on the fake backend."""
    root = tmp_path_factory.mktemp("repro-template") / "demo-iva"
    root.mkdir()
    shutil.copytree(DEMO_IVA_DIR / "features", root / "features")
    shutil.copytree(DEMO_IVA_DIR / "src", root / "src")
    for name in DEMO_IVA_FILES:
        shutil.copy(DEMO_IVA_DIR / name, root / name)

    with pytest.MonkeyPatch.context() as patch:
        for name, value in isolated_env().items():
            patch.setenv(name, value)
        git(root, "init", "-q", "-b", "master")
        git(root, "config", "user.name", "Test User")
        git(root, "config", "user.email", "test@example.com")
        result = CliRunner().invoke(app, ["init", "--cwd", str(root)])
        require(result.exit_code == 0, f"lictor init falló: {result.output}")

        # The fake kind makes it impossible for a harness bug to reach the real backend.
        config_path = root / "lictor.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["backend"]["kind"] = "fake"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "chore: initial commit")

        uv_sync(root)
        shell(["uv", "run", "mypy", "src"], root)  # warms the cache each copy inherits
        yield root


@pytest.fixture
def make_project(tmp_path: Path, template: Path) -> ProjectFactory:
    """Return a factory of per-case projects, with optional `lictor.json` overrides."""

    def factory(
        *,
        sync: bool = True,
        extra_controls: list[dict[str, Any]] | None = None,
        allowed_paths: tuple[str, ...] = (),
    ) -> Path:
        root = tmp_path / "demo-iva"
        copy_project(template, root)
        if extra_controls or allowed_paths:
            config_path = root / "lictor.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["controls"].extend(extra_controls or [])
            config["forbidden_paths"] = [
                path for path in config["forbidden_paths"] if path not in allowed_paths
            ]
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            git(root, "add", "-A")
            git(root, "commit", "-q", "--allow-empty", "-m", "chore: controles del caso")
        if sync:
            uv_sync(root)
        require(not git(root, "status", "--short"), "la plantilla no está limpia")
        return root

    return factory
