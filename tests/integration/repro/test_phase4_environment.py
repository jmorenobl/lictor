"""Phase 4: verification gets a home of its own, and what `prepare` can reach stays with it."""

from pathlib import Path

import pytest

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    GOOD_BY_SCENARIO,
    MODULE_PATH,
    VERIFY_ENV,
    Agent,
    events_of,
    false_greens,
    green_ids,
    require,
    run_lictor,
    shell,
    write,
)

PERMISSIVE_RUFF = 'lint.ignore = ["ALL"]\n'
PERMISSIVE_MYPY = "[mypy]\nignore_errors = True\n"
POISON_PACKAGE = "iniconfig"
POISON_MODULE = "evil_plugin.py"
POISON_PLUGIN = (
    '"""Anula todos los tests."""\n\n\n'
    "def pytest_collection_modifyitems(items):\n"
    "    for item in items:\n"
    "        item.obj = lambda *a, **k: None\n"
)


def _user_home_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> Path:
    """Move HOME to a directory holding `files`, keeping the tools where they really are."""
    for variable, question in (
        ("UV_CACHE_DIR", ["uv", "cache", "dir"]),
        ("UV_PYTHON_INSTALL_DIR", ["uv", "python", "dir"]),
    ):
        answer = shell(question, tmp_path)
        require(answer.returncode == 0, f"uv no respondió: {answer.stderr}")
        monkeypatch.setenv(variable, answer.stdout.strip())
    home = tmp_path / "casa-del-usuario"
    for rel_path, content in files.items():
        write(home, rel_path, content)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.mark.parametrize(
    ("control", "code"),
    [("lint", "import os\n"), ("types", 'VALOR: int = "texto"\n')],
)
def test_user_configuration_does_not_decide_a_control(
    make_project: ProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: str,
    code: str,
) -> None:
    root = make_project()
    _user_home_with(
        tmp_path,
        monkeypatch,
        {".config/ruff/ruff.toml": PERMISSIVE_RUFF, ".config/mypy/config": PERMISSIVE_MYPY},
    )

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, GOOD_BY_SCENARIO[scenario_id])
        write(task.cwd, "src/iva/defecto.py", f'"""Defecto."""\n\n{code}')

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require("IVA-001" in agent.calls, "el agente debía llegar a implementar")
    assert green_ids(root) == []
    failures = {event["data"]["failed_at"] for event in events_of(root, "verify.finished")}
    assert failures == {control}


def _poison_cache(cache: Path) -> None:
    """Rewrite an entry of the cache the way any code running as the user could."""
    infos = list(cache.glob(f"archive-v0/*/{POISON_PACKAGE}-*.dist-info"))
    require(len(infos) == 1, f"la caché debía tener {POISON_PACKAGE}: {infos}")
    write(infos[0].parent, f"{POISON_PACKAGE}/{POISON_MODULE}", POISON_PLUGIN)
    write(infos[0], "entry_points.txt", f"[pytest11]\nmal = {POISON_PACKAGE}.evil_plugin\n")


@pytest.mark.parametrize(
    ("downloads", "poisoned"),
    [(True, False), (False, True)],
    ids=["el-perfil-real-no-la-usa", "control-con-cache-el-veneno-entra"],
)
def test_a_poisoned_cache_doesnt_reach_the_environment(
    make_project: ProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    downloads: bool,
    poisoned: bool,
) -> None:
    root = make_project(sync=False, downloads=downloads)
    cache = tmp_path / "cache-compartida"
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    filled = shell(["uv", "sync", "-q", "--frozen", "--no-install-project"], root)
    require(filled.returncode == 0, f"no se pudo llenar la caché: {filled.stderr}")
    _poison_cache(cache)

    agent = Agent()
    run_lictor(root, agent)

    require(events_of(root, "env.built"), "debía construirse el entorno")
    planted = list((root / VERIFY_ENV).glob(f"lib/python*/site-packages/*/{POISON_MODULE}"))
    assert bool(planted) is poisoned
    loaded = events_of(root, "env.built")[-1]["data"]["plugins"]
    assert (f"{POISON_PACKAGE}.evil_plugin" in loaded) is poisoned
    if not poisoned:
        assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
        assert false_greens(root) == []
