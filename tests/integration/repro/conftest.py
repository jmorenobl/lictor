"""Fixtures for the audit reproduction cases."""

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lictor.adapters.config import loader
from lictor.cli import app
from tests.conftest import VERIFICATION_VARIABLES
from tests.integration.repro.harness import (
    copy_project,
    git,
    isolated_env,
    light_config,
    require,
    shell,
    uses_all_controls,
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
        for name in VERIFICATION_VARIABLES:
            patch.delenv(name, raising=False)
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


def _case_profiles(forbidden: tuple[str, ...], dest: Path, *, downloads: bool) -> Path:
    """Copy the profiles, dropping heuristics a case must prove it doesn't depend on.

    Without `downloads`, `prepare` uses the shared cache: downloading a whole environment per case
    would take the network and many gigabytes, and only a case about the cache needs it.
    """
    shutil.copytree(loader.DEFAULT_PROFILES_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"))
    path = dest / "python-uv.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["forbidden_paths"] = [p for p in profile["forbidden_paths"] if p not in forbidden]
    if not downloads:
        verification = profile["verification"]
        verification["prepare"] = verification["prepare"].replace(" --no-cache", "")
        verification["prepare_ask"]["UV_CACHE_DIR"] = "uv cache dir"
    path.write_text(json.dumps(profile), encoding="utf-8")
    return dest


def _read_config(root: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((root / "lictor.json").read_text(encoding="utf-8"))
    return loaded


def _write_config(root: Path, config: dict[str, Any]) -> None:
    (root / "lictor.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


@pytest.fixture
def make_project(
    tmp_path: Path,
    template: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> ProjectFactory:
    """Return a factory of per-case projects, with optional `lictor.json` overrides.

    The project is light (`light_config`) unless the case is marked `smoke` or `all_controls`.
    Light rewrites the template's only commit, so the history has the same shape either way.
    """
    light = not uses_all_controls({mark.name for mark in request.node.iter_markers()})

    def factory(
        *,
        sync: bool = True,
        extra_controls: list[dict[str, Any]] | None = None,
        allowed_paths: tuple[str, ...] = (),
        config_changes: dict[str, Any] | None = None,
        downloads: bool = False,
    ) -> Path:
        root = tmp_path / "demo-iva"
        copy_project(template, root)
        profiles = _case_profiles(allowed_paths, tmp_path / "perfiles", downloads=downloads)
        monkeypatch.setattr(loader, "DEFAULT_PROFILES_DIR", profiles)
        if light:
            _write_config(root, light_config(_read_config(root)))
            git(root, "commit", "-q", "-a", "--amend", "--no-edit")
        if extra_controls or config_changes:
            config = _read_config(root)
            config.setdefault("controls", []).extend(extra_controls or [])
            config.update(config_changes or {})
            _write_config(root, light_config(config) if light else config)
            git(root, "add", "-A")
            git(root, "commit", "-q", "--allow-empty", "-m", "chore: controles del caso")
        if sync:
            uv_sync(root)
        require(not git(root, "status", "--short"), "la plantilla no está limpia")
        return root

    return factory
