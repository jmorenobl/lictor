"""Phase 3 (C1, C2, A9): a green commit means that exact tree passed, out of the agent's reach."""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from lictor.adapters.config.loader import load_config
from lictor.domain.errors import ConfigError
from lictor.domain.model import AgentStatus, AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    GOOD_1,
    GOOD_3,
    GOOD_BY_SCENARIO,
    MODULE_PATH,
    STUB,
    VERIFY_ENV,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_driver,
    good_impl,
    green_ids,
    known_limitation,
    no_op_change,
    require,
    run_lictor,
    shell,
    write,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CHEAT_PLUGIN = (
    '"""P."""\nimport pytest\n\n\n'
    "@pytest.hookimpl(wrapper=True)\n"
    "def pytest_runtest_call(item):\n"
    "    item.obj = lambda *a, **k: None\n"
    "    return (yield)\n"
)
SITECUSTOMIZE = '"""S."""\nimport os\n\nos.environ["PYTEST_ADDOPTS"] = "-p lab_cheat"\n'
CUSTOMIZE_FILES = ("sitecustomize.py", "usercustomize.py")
BDD_DRIVER = """import iva
import pytest
from pytest_bdd import given, scenario, then, when


@scenario("../../features/iva.feature", "IVA general")
def test_iva_001():
    pass


@scenario("../../features/iva.feature", "IVA reducido")
def test_iva_002():
    pass


@scenario("../../features/iva.feature", "Importe negativo")
def test_iva_003():
    pass


@pytest.fixture
def calculo():
    return {}


@given("una factura con importe 100", target_fixture="importe")
def importe_100():
    return 100


@given("una factura con importe -5", target_fixture="importe")
def importe_negativo():
    return -5


@when("calculo el IVA general")
def general(importe, calculo):
    calculo["total"] = iva.general(importe)


@when("calculo el IVA reducido")
def reducido(importe, calculo):
    calculo["total"] = iva.reducido(importe)


@then("el total es 121")
def total_121(calculo):
    assert calculo["total"] == 121


@then("el total es 110")
def total_110(calculo):
    assert calculo["total"] == 110


@then("se rechaza por importe no válido")
def rechazado(importe):
    assert iva.negativo(importe) is True
"""
FAKE_PYTEST_BDD = (
    '"""Sustituto."""\n\n\n'
    "def _identity(*_args, **_kwargs):\n"
    "    return lambda function: function\n\n\n"
    "scenario = given = when = then = _identity\n"
)
ARCHITECTURE_CONTRACT = """[importlinter]
root_package = paquete

[importlinter:contract:capas]
name = Capas
type = forbidden
source_modules = paquete.dominio
forbidden_modules = paquete.adaptador
"""
DIST_METADATA = "Metadata-Version: 2.1\nName: lab-cheat\nVersion: 1.0\n"
ENTRY_POINTS = "[pytest11]\nlab_cheat = lab_cheat\n"
_PLANT_FROM_A_CONTROL = (
    "import site\n"
    "base = pathlib.Path(site.getsitepackages()[0])\n"
    f"(base / 'lab_cheat.py').write_text({CHEAT_PLUGIN!r})\n"
    "info = base / 'lab_cheat-1.0.dist-info'\n"
    "info.mkdir()\n"
    f"(info / 'METADATA').write_text({DIST_METADATA!r})\n"
    f"(info / 'entry_points.txt').write_text({ENTRY_POINTS!r})\n"
)

FORGE_MODULE_PYC = """
import pathlib, py_compile, sys
source = pathlib.Path("src/iva/__init__.py")
tmp = pathlib.Path(".forge_tmp.py")
tmp.write_text(sys.argv[1])
cfile = source.parent / "__pycache__" / f"__init__.{sys.implementation.cache_tag}.pyc"
cfile.parent.mkdir(exist_ok=True)
py_compile.compile(
    str(tmp), cfile=str(cfile), dfile=str(source), doraise=True,
    invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
)
tmp.unlink()
"""
FORGE_DRIVER_PYC = """
import importlib.util, marshal, pathlib, struct, sys
import pytest
source = pathlib.Path("tests/acceptance/test_iva.py")
stat = source.stat()
fake = "".join(f"def test_iva_00{n}():\\n    pass\\n\\n\\n" for n in (1, 2, 3))
code = compile(fake, str(source), "exec")
header = importlib.util.MAGIC_NUMBER + b"\\0\\0\\0\\0"
header += struct.pack("<LL", int(stat.st_mtime) & 0xFFFFFFFF, stat.st_size & 0xFFFFFFFF)
name = f"test_iva.{sys.implementation.cache_tag}-pytest-{pytest.__version__}.pyc"
out = source.parent / "__pycache__" / name
out.parent.mkdir(exist_ok=True)
out.write_bytes(header + marshal.dumps(code))
"""
FORGE_JUNIT = (
    '"""Interfaz."""\nimport os\nimport sys\n\n'
    'if any("acceptance" in a for a in sys.argv):\n'
    '    out = sys.argv[sys.argv.index("--junitxml") + 1]\n'
    '    with open(out, "w", encoding="utf-8") as f:\n'
    "        f.write('<testsuite>' + ''.join("
    'f\'<testcase classname="t" name="test_iva_00{i}"/>\' for i in (1, 2, 3))'
    " + '</testsuite>')\n"
    "    os._exit(0)\n"
) + STUB.split('"""Interfaz."""')[1]
SABOTAGE_AFTER_CHECKS = (
    "\nimport atexit as _a\nimport subprocess as _s\nimport sys as _y\n"
    "from pathlib import Path as _P\n\n\ndef _fin() -> None:\n"
    '    p = _P("src/iva/__init__.py")\n'
    '    p.write_text(p.read_text().replace("round(importe * 1.21, 2)", "0.0"))\n'
    '    q = _P("pyproject.toml")\n'
    '    q.write_text(q.read_text() + "\\n# tocado tras L0\\n")\n'
    '    _s.run(["git", "add", "pyproject.toml"], check=False)\n\n\n'
    'if any("acceptance" in x for x in _y.argv):\n    _a.register(_fin)\n'
)


def _exit_code(root: Path, agent: Agent) -> int | None:
    outcome = run_lictor(root, agent)
    if isinstance(outcome.exception, ConfigError):
        return outcome.exception.exit_code
    return outcome.exit_code


ARCHITECTURE_COMMAND = "uv run python scripts/check_architecture.py"


def _assert_rejected_by(project: Callable[[], Path], agent: Agent, *reasons: str) -> None:
    """An attack that fails lint would look like a defence: it must fall to the expected gate."""
    root = project()
    outcome = run_lictor(root, agent)
    require("IVA-001" in agent.calls, f"el agente debía llegar a implementar: {outcome}")
    assert false_greens(root) == []
    assert green_ids(root) == []
    verified = events_of(root, "verify.finished")
    assert {event["data"]["failed_at"] for event in verified} == set(reasons)


def test_c1_forged_module_bytecode(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        no_op_change(task)
        forged = shell(["uv", "run", "python", "-c", FORGE_MODULE_PYC, GOOD_3], task.cwd)
        require(forged.returncode == 0, f"el ataque debía ejecutarse: {forged.stderr}")

    _assert_rejected_by(make_project, Agent(impl=impl), "acceptance")


def test_c2_forged_driver_bytecode(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        no_op_change(task)
        forged = shell(["uv", "run", "python", "-c", FORGE_DRIVER_PYC], task.cwd)
        require(forged.returncode == 0, f"el ataque debía ejecutarse: {forged.stderr}")

    _assert_rejected_by(make_project, Agent(impl=impl), "acceptance")


def _plant_plugin(site_packages: Path) -> None:
    write(site_packages, "lab_cheat.py", CHEAT_PLUGIN)
    write(site_packages, "lab_cheat-1.0.dist-info/METADATA", DIST_METADATA)
    write(site_packages, "lab_cheat-1.0.dist-info/entry_points.txt", ENTRY_POINTS)


def test_c3_plugin_in_the_verification_venv_fails_by_hash(make_project: ProjectFactory) -> None:
    root = make_project()
    planted: list[Path] = []

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        no_op_change(task)
        if scenario_id == "IVA-001" and not planted:
            for site_packages in root.glob(f"{VERIFY_ENV}/lib/python*/site-packages"):
                _plant_plugin(site_packages)
                planted.append(site_packages)

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require(planted, "el ataque debía llegar a la venv de verificación")
    assert false_greens(root) == []
    verified = events_of(root, "verify.finished")
    reasons = [event["data"]["failed_at"] for event in verified]
    assert reasons[0] == "L0-entorno"
    assert set(reasons[1:]) == {"acceptance"}, "tras reconstruir, el stub debía seguir fallando"
    tampered = events_of(root, "env.tampered")
    assert len(tampered) == 1
    assert tampered[0]["data"]["expected"] != tampered[0]["data"]["actual"]
    assert not (planted[0] / "lab_cheat.py").exists(), "la venv debía reconstruirse"
    built = events_of(root, "env.built")
    assert [event["data"]["plugins"] for event in built] == [["pytest_bdd.plugin"]] * 2


def test_c3_plugin_planted_during_a_command_is_not_loaded_nor_accepted(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """Nothing is discovered at run time: acceptance comes right after the planting control."""
    planted = tmp_path / "plantado"
    script = tmp_path / "plantar.py"
    script.write_text(
        f"import pathlib\n{_PLANT_FROM_A_CONTROL}pathlib.Path({str(planted)!r}).touch()\n",
        encoding="utf-8",
    )
    control = {
        "id": "planta",
        "level": "L2",
        "run": f"sh -c 'test -e {planted} || uv run python {script}'",
        "report": "exit-code",
    }
    root = make_project(extra_controls=[control])

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        """Leave the stub: only the planted plugin could turn it green."""
        no_op_change(task)

    run_lictor(root, Agent(impl=impl))

    require(planted.exists(), "el control debía plantar el plugin")
    assert false_greens(root) == []
    assert green_ids(root) == []
    reasons = [event["data"]["failed_at"] for event in events_of(root, "verify.finished")]
    assert reasons[:2] == ["acceptance", "L0-entorno"]


def test_a_plugin_cannot_come_in_through_the_lockfile(make_project: ProjectFactory) -> None:
    """Whatever `uv.lock` installs now loads: that makes its protection part of this defence."""
    root = make_project()

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        lock = task.cwd / "uv.lock"
        lock.write_text(lock.read_text(encoding="utf-8") + "\n# nuevo plugin\n", encoding="utf-8")

    _assert_rejected_by(lambda: root, Agent(impl=impl), "L0-rutas")
    assert [event["data"]["reason"] for event in events_of(root, "env.built")] == [
        "primera verificación"
    ]


def test_c3_forged_bytecode_in_the_verification_venv_fails_by_hash(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    planted: list[Path] = []

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        no_op_change(task)
        if scenario_id == "IVA-001" and not planted:
            for site_packages in root.glob(f"{VERIFY_ENV}/lib/python*/site-packages"):
                pyc = site_packages / "_pytest" / "__pycache__" / "forjado.cpython-312.pyc"
                write(pyc.parent, pyc.name, b"\x00")
                planted.append(pyc)

    run_lictor(root, Agent(impl=impl))

    require(planted, "el ataque debía llegar a la venv de verificación")
    assert false_greens(root) == []
    assert events_of(root, "verify.finished")[0]["data"]["failed_at"] == "L0-entorno"
    assert not planted[0].exists(), "la venv debía reconstruirse"


def test_c3_plugin_in_the_agents_venv_does_not_reach_verification(
    make_project: ProjectFactory,
) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        no_op_change(task)
        synced = shell(["uv", "sync", "-q", "--frozen", "--no-install-project"], task.cwd)
        require(synced.returncode == 0, f"el agente debía tener su venv: {synced.stderr}")
        sites = list(task.cwd.glob(".venv/lib/python*/site-packages"))
        require(sites, "el ataque debía llegar a la venv del agente")
        for site_packages in sites:
            _plant_plugin(site_packages)

    _assert_rejected_by(make_project, Agent(impl=impl), "acceptance")


def test_venv_tampered_during_verification_rejects_the_attempt(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """A control runs project code too: the hash must also hold after the commands."""
    venv = tmp_path / "demo-iva" / VERIFY_ENV / "pyvenv.cfg"
    once = tmp_path / "ya-tocado"
    tamper = {
        "id": "toca-venv",
        "level": "L2",
        "run": f"sh -c 'test -e {once} || {{ touch {once}; echo \\# >> {venv}; }}'",
        "report": "exit-code",
    }
    root = make_project(extra_controls=[tamper])

    run_lictor(root, Agent())

    require(once.exists(), "el control debía tocar la venv")
    assert events_of(root, "verify.finished")[0]["data"]["failed_at"] == "L0-entorno"
    assert false_greens(root) == []


def test_verification_runs_in_a_clean_clone_with_the_profile_environment(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    probe = tmp_path / "sonda"
    control = {
        "id": "sonda",
        "level": "L2",
        "run": f"sh -c 'pwd > {probe}; ls -a >> {probe}; env >> {probe}'",
        "report": "exit-code",
    }
    root = make_project(extra_controls=[control])

    run_lictor(root, Agent())

    seen = probe.read_text(encoding="utf-8")
    cwd, listing = seen.split("\n", 1)
    assert cwd != str(root.resolve())
    assert not Path(cwd).exists(), "el clon debía borrarse"
    assert ".venv" not in listing.split("\n"), "uv creó un entorno dentro del clon"
    assert ".lictor" not in listing.split("\n")
    assert "PYTHONDONTWRITEBYTECODE=1" in seen
    assert f"UV_PROJECT_ENVIRONMENT={root.resolve() / VERIFY_ENV}" in seen


def _probe_control(probe: Path) -> dict[str, str]:
    return {"id": "sonda", "level": "L2", "run": f"sh -c 'env > {probe}'", "report": "exit-code"}


def test_undeclared_variable_does_not_reach_the_commands(
    make_project: ProjectFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name is invented, so no list of known dangers could have foreseen it."""
    probe = tmp_path / "sonda"
    root = make_project(extra_controls=[_probe_control(probe)])
    monkeypatch.setenv("LICTOR_NADIE_LA_ENUMERO_7Q2", "x")

    assert run_lictor(root, Agent()) == Outcome(0)

    assert "LICTOR_NADIE_LA_ENUMERO_7Q2" not in probe.read_text(encoding="utf-8")


def test_verification_inherits_nothing_that_loads_code(
    make_project: ProjectFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = tmp_path / "sonda"
    root = make_project(extra_controls=[_probe_control(probe)])
    monkeypatch.setenv("PYTHONPATH", "src")
    monkeypatch.setenv("PYTHONSTARTUP", "src/inicio.py")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p lab_cheat")

    assert run_lictor(root, Agent()) == Outcome(0)

    seen = probe.read_text(encoding="utf-8").splitlines()
    assert not [line for line in seen if line.startswith(("PYTHONPATH=", "PYTHONSTARTUP="))]
    assert "PYTEST_ADDOPTS=-o pythonpath=" in seen


def test_verification_keeps_what_the_tools_need(
    make_project: ProjectFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The network settings and credentials of `prepare` don't reach project code."""
    probe = tmp_path / "sonda"
    root = make_project(extra_controls=[_probe_control(probe)])
    monkeypatch.setenv("SSL_CERT_DIR", "/ruta/de/prueba")
    monkeypatch.setenv("NETRC", "/ruta/de/credenciales")
    monkeypatch.setenv("HTTPS_PROXY", "http://usuario:clave@proxy.invalid:9")
    monkeypatch.setenv("NO_PROXY", "*")

    assert run_lictor(root, Agent()) == Outcome(0)

    seen = probe.read_text(encoding="utf-8").splitlines()
    assert f"PATH={os.environ['PATH']}" in seen
    names = {line.partition("=")[0] for line in seen}
    assert not names & {"SSL_CERT_DIR", "NETRC", "HTTPS_PROXY", "NO_PROXY"}
    assert f"HOME={os.environ['HOME']}" not in seen


def test_project_needs_reach_the_commands(
    make_project: ProjectFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = tmp_path / "sonda"
    verification = {"env": {"MI_FIJA": "1"}, "inherit": ["MI_HEREDADA"]}
    root = make_project(
        extra_controls=[_probe_control(probe)], config_changes={"verification": verification}
    )
    monkeypatch.setenv("MI_HEREDADA", "2")

    assert run_lictor(root, Agent()) == Outcome(0)

    seen = probe.read_text(encoding="utf-8").splitlines()
    assert {"MI_FIJA=1", "MI_HEREDADA=2"} <= set(seen)
    assert "PYTEST_PLUGINS=lictor_project_path,pytest_bdd.plugin" in seen


@pytest.mark.parametrize(
    "changes",
    [
        {"verification": {"env": {"PYTEST_ADDOPTS": "-p lab_cheat"}}},
        {"verification": {"env": {"PYTHONPATH": "src"}}},
        {"verification": {"inherit": ["PYTHONPATH"]}},
        {"verification": {"prepare": "true"}},
        {"forbidden_paths": []},
        {"banned_patterns": []},
    ],
    ids=["addopts", "pythonpath", "heredar", "prepare", "rutas", "patrones"],
)
def test_lictor_json_cannot_weaken_the_profile(
    make_project: ProjectFactory, changes: dict[str, Any]
) -> None:
    """`lictor.json` doesn't get to choose: the run refuses to start and no agent is called."""
    agent = Agent()
    root = make_project(config_changes=changes)

    assert _exit_code(root, agent) == 10
    assert agent.calls == []


def test_verification_venv_shares_nothing_and_has_no_bytecode(
    make_project: ProjectFactory,
) -> None:
    root = make_project()

    run_lictor(root, Agent())

    files = [p for p in (root / VERIFY_ENV).rglob("*") if p.is_file() and not p.is_symlink()]
    require(files, "la venv de verificación debía existir")
    assert [p for p in files if p.stat().st_nlink > 1] == []
    assert [p for p in files if p.suffix == ".pyc"] == []
    assert events_of(root, "env.tampered") == []


def test_green_commit_is_exactly_the_verified_tree(make_project: ProjectFactory) -> None:
    root = make_project()

    run_lictor(root, Agent())

    verified = [e for e in events_of(root, "verify.finished") if e["data"]["ok"]]
    greens = events_of(root, "scenario.green")
    require(len(greens) == 3, f"se esperaban 3 verdes: {greens}")
    for check, green in zip(verified, greens, strict=True):
        assert check["data"]["candidate"] == green["data"]["commit"]
        tree = git(root, "rev-parse", f"{green['data']['commit']}^{{tree}}").strip()
        assert check["data"]["tree"] == tree
    assert git(root, "status", "--short") == ""


def test_driver_relying_on_an_ignored_file_is_not_sealed(make_project: ProjectFactory) -> None:
    root = make_project()

    def driver(task: AgentTask, _attempt: int) -> None:
        good_driver(task)
        write(task.cwd, ".pytest_cache/helper.py", "VALOR = 1\n")
        write(
            task.cwd,
            DRIVER_PATH,
            "import importlib.util\n"
            "spec = importlib.util.spec_from_file_location('h', '.pytest_cache/helper.py')\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n" + DRIVER,
        )

    outcome = run_lictor(root, Agent(driver=driver))

    assert outcome == Outcome(10)
    assert events_of(root, "driver.sealed") == []
    assert events_of(root, "driver.rejected")


def test_installed_plugin_needs_no_declaration(make_project: ProjectFactory) -> None:
    """The agent's commands autoload `pytest-bdd`; verification must see the same driver pass."""

    def driver(task: AgentTask, _attempt: int) -> None:
        good_driver(task)
        write(task.cwd, DRIVER_PATH, BDD_DRIVER)

    agent = Agent(driver=driver)
    root = make_project(config_changes={"verification": {}})

    assert run_lictor(root, agent) == Outcome(0)

    assert agent.calls.count("driver") == 1
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []


def test_installed_plugin_loads_with_autoload_off(make_project: ProjectFactory) -> None:
    root = make_project()
    needs_plugin = DRIVER.replace(
        "def test_iva_001():\n    pass",
        "def test_iva_001(pytestconfig):\n"
        '    assert pytestconfig.pluginmanager.get_plugin("pytest_bdd.plugin")',
    )
    require(needs_plugin != DRIVER, "el driver debía cambiar")

    def driver(task: AgentTask, _attempt: int) -> None:
        good_driver(task)
        write(task.cwd, DRIVER_PATH, needs_plugin)

    assert run_lictor(root, Agent(driver=driver)) == Outcome(0)


def test_verification_venv_is_built_once_per_run(make_project: ProjectFactory) -> None:
    root = make_project()

    run_lictor(root, Agent())

    built = events_of(root, "env.built")
    assert len(built) == 1
    assert built[0]["data"]["hash"].startswith("sha256:")


@pytest.mark.parametrize(
    ("rel_path", "content", "scenario"),
    [
        ("src/lab_cheat-1.0.dist-info/METADATA", "x", "L0-rutas"),
        ("src/evil.pth", "import x", "L0-rutas"),
        ("src/iva/.gitignore", "_oculto.py\n", "L0-rutas"),
        ("src/ruff.toml", "x", "L0-rutas"),
        ("src/sitecustomize.py", "x = 1\n", "L0-rutas"),
        ("src/usercustomize.py", "x = 1\n", "L0-rutas"),
        ("src/uv.lock", "x", "L0-rutas"),
        ("tests/unit/conftest.py", "x", "L0-rutas"),
        ("tests/unit/pytest.ini", "[pytest]", "L0-rutas"),
        ("src/iva/a.py", "import os  # NOQA\n", "L0-supresiones"),
        ("src/iva/a.py", 'x: int = "a"  #type:ignore\n', "L0-supresiones"),
        ("src/iva/a.py", "# ruff: noqa\n", "L0-supresiones"),
        ("src/iva/a.py", "# mypy: ignore-errors\n", "L0-supresiones"),
        ("tests/unit/test_a.py", "import pytest\npytest.skip('x')\n", "L0-supresiones"),
        ("tests/unit/test_a.py", "@pytest.mark.skip\ndef test_a(): ...\n", "L0-supresiones"),
    ],
)
def test_d22_each_evasion_is_rejected_on_its_own(
    make_project: ProjectFactory, rel_path: str, content: str, scenario: str
) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        write(task.cwd, rel_path, content)

    run_lictor(root, Agent(impl=impl))

    assert events_of(root, "verify.finished")[0]["data"]["failed_at"] == scenario
    assert rel_path not in git(root, "ls-tree", "-r", "--name-only", "HEAD").splitlines()


def test_project_cannot_open_the_lock_to_the_agent(make_project: ProjectFactory) -> None:
    """What the lock installs loads as a plugin: its defence can't depend on `paths`."""
    root = make_project()
    config_path = root / "lictor.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["paths"]["protected"].remove("uv.lock")
    config["paths"]["writable"].append("uv.lock")
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    git(root, "commit", "-qam", "chore: abrir el bloqueo")

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        lock = task.cwd / "uv.lock"
        lock.write_text(lock.read_text(encoding="utf-8") + "\n# editado\n", encoding="utf-8")

    _assert_rejected_by(lambda: root, Agent(impl=impl), "L0-rutas")


def test_symlink_out_of_the_tree_is_rejected(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        link = task.cwd / "src/iva/atajo.py"
        if not link.is_symlink():
            link.symlink_to(task.cwd / ".venv/pyvenv.cfg")

    run_lictor(root, Agent(impl=impl))

    assert events_of(root, "verify.finished")[0]["data"]["failed_at"] == "L0-rutas"
    assert false_greens(root) == []


def test_c4_plugin_in_a_writable_path(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, "src/lab_cheat.py", CHEAT_PLUGIN)
        write(task.cwd, "src/lab_cheat-1.0.dist-info/METADATA", DIST_METADATA)
        write(task.cwd, "src/lab_cheat-1.0.dist-info/entry_points.txt", ENTRY_POINTS)

    _assert_rejected_by(make_project, Agent(impl=impl), "L0-rutas")


def test_c4_plugin_in_a_writable_path_without_the_path_heuristic(
    make_project: ProjectFactory,
) -> None:
    """`*.dist-info` is only a heuristic: with autoload off the plugin must not load anyway."""

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, "src/lab_cheat.py", CHEAT_PLUGIN)
        write(task.cwd, "src/lab_cheat-1.0.dist-info/METADATA", DIST_METADATA)
        write(task.cwd, "src/lab_cheat-1.0.dist-info/entry_points.txt", ENTRY_POINTS)

    _assert_rejected_by(
        lambda: make_project(allowed_paths=("*.dist-info",)), Agent(impl=impl), "acceptance"
    )


def test_c4b_sitecustomize_does_not_reach_verification(make_project: ProjectFactory) -> None:
    """The paths stay allowed on purpose: what closes this is that `src` isn't on the path."""

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, "src/sitecustomize.py", SITECUSTOMIZE)
        write(task.cwd, "src/lab_cheat.py", CHEAT_PLUGIN)

    agent = Agent(impl=impl)
    _assert_rejected_by(lambda: make_project(allowed_paths=CUSTOMIZE_FILES), agent, "acceptance")


def _bdd_driver(task: AgentTask, _attempt: int) -> None:
    good_driver(task)
    write(task.cwd, DRIVER_PATH, BDD_DRIVER)


def test_c4b_package_in_src_does_not_replace_a_dependency(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, "src/pytest_bdd/__init__.py", FAKE_PYTEST_BDD)
        write(task.cwd, "src/pytest_bdd/plugin.py", '"""Sustituto."""\n')

    _assert_rejected_by(make_project, Agent(driver=_bdd_driver, impl=impl), "acceptance")


def test_c4b_module_in_src_does_not_replace_a_transitive_dependency(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """`pytest_bdd.plugin` imports `parse`: if `src` came first, this would run on startup."""
    marker = tmp_path / "parse-falso"

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        write(task.cwd, "src/parse.py", f'"""P."""\nopen({str(marker)!r}, "w").close()\n')

    agent = Agent(driver=_bdd_driver, impl=impl)
    assert run_lictor(make_project(), agent) == Outcome(0)
    assert not marker.exists()


def test_project_code_only_runs_where_the_tests_import_it(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    """Lint and types read the sources: nothing of the project may execute during them."""
    log = tmp_path / "ejecuciones"
    trace = (
        '"""Interfaz."""\nimport sys\n\n'
        f'with open({str(log)!r}, "a", encoding="utf-8") as trace:\n'
        '    trace.write(sys.argv[0] + "\\n")\n'
    )
    root = make_project(allowed_paths=CUSTOMIZE_FILES)

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        write(task.cwd, "src/sitecustomize.py", trace)
        write(task.cwd, "src/usercustomize.py", trace)
        write(task.cwd, MODULE_PATH, trace + GOOD_BY_SCENARIO[scenario_id].split('"""', 2)[2])

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    tools = {Path(line).name for line in log.read_text(encoding="utf-8").splitlines()}
    assert tools == {"pytest"}


@pytest.mark.parametrize(
    ("domain", "expected"), [("", 0), ("import paquete.adaptador\n", 1)], ids=["limpio", "violado"]
)
def test_architecture_control_reads_the_sources_without_running_them(
    tmp_path: Path, domain: str, expected: int
) -> None:
    """Import Linter puts its working directory first, ahead of what it imports lazily."""
    marker = tmp_path / "ejecutado"
    root = tmp_path / "proyecto"
    write(root, "pyproject.toml", '[project]\nname = "demo"\nversion = "0"\n')
    write(root, ".importlinter", ARCHITECTURE_CONTRACT)
    write(root, "src/paquete/__init__.py", "")
    write(root, "src/paquete/adaptador.py", "")
    write(root, "src/paquete/dominio.py", domain)
    write(
        root,
        "src/typing_extensions.py",
        f"open({str(marker)!r}, 'w').close()\nraise SystemExit(0)\n",
    )
    script = "scripts/check_architecture.py"
    write(root, script, (REPO_ROOT / script).read_text(encoding="utf-8"))
    env = os.environ | {
        "UV_PROJECT_ENVIRONMENT": sys.prefix,
        "UV_NO_SYNC": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    result = subprocess.run(
        shlex.split(ARCHITECTURE_COMMAND),
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert not marker.exists()
    assert result.returncode == expected, result.stdout + result.stderr


def test_architecture_control_is_out_of_the_agents_reach() -> None:
    config = load_config(REPO_ROOT)
    command = next(control.run for control in config.controls if control.id == "architecture")

    assert command == ARCHITECTURE_COMMAND
    assert not any(path.startswith("scripts") for path in config.paths.writable)


def _load_verify_self(root: Path) -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify_self.py"
    spec = importlib.util.spec_from_file_location("verify_self", path)
    require(spec and spec.loader, "el script debía cargarse")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPO_ROOT = root
    return module


def _exit_control(control_id: str, command: str, mode: str = "block") -> dict[str, str]:
    return {"id": control_id, "level": "L2", "run": command, "report": "exit-code", "mode": mode}


def test_verify_self_runs_the_controls_in_the_real_environment(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    probe = tmp_path / "sonda"
    fails = _exit_control("falla", "false")
    measured = _exit_control("mide", "false", "measure")
    seen = _exit_control("sonda", f"sh -c 'pwd > {probe}; env >> {probe}'")
    root = make_project(extra_controls=[fails, measured, seen])
    verify_self = _load_verify_self(root)

    assert verify_self.main(["sonda", "mide"]) == 0
    assert verify_self.main(["falla"]) == 1

    cwd, *variables = probe.read_text(encoding="utf-8").splitlines()
    assert cwd != str(root.resolve())
    assert f"UV_PROJECT_ENVIRONMENT={root.resolve() / VERIFY_ENV}" in variables


def test_verify_self_fails_if_a_control_tampers_with_the_environment(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    venv = tmp_path / "demo-iva" / VERIFY_ENV / "pyvenv.cfg"
    root = make_project(extra_controls=[_exit_control("toca", f"sh -c 'echo \\# >> {venv}'")])

    assert _load_verify_self(root).main(["toca"]) == 1


def test_project_without_pythonpath_of_its_own_reaches_the_sources(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    pyproject = root / "pyproject.toml"
    declared = '[tool.pytest.ini_options]\npythonpath = ["src"]\n'
    require(declared in pyproject.read_text(encoding="utf-8"), "demo-iva debía declararlo")
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace(declared, ""))
    git(root, "commit", "-qam", "chore: sin pythonpath")

    outcome = run_lictor(root, Agent(driver=_bdd_driver))

    assert outcome == Outcome(0)
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []


def test_type_control_still_reads_the_sources(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        write(task.cwd, "src/iva/malo.py", '"""M."""\nvalor: int = "a"\n')

    root = make_project()
    run_lictor(root, Agent(impl=impl))

    assert events_of(root, "verify.finished")[0]["data"]["failed_at"] == "types"


@known_limitation("C1: el informe lo escribe el mismo proceso que el código bajo test")
def test_c6_code_under_test_forges_the_report(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, FORGE_JUNIT)

    root = make_project()
    agent = Agent(impl=impl)
    outcome = run_lictor(root, agent)
    require("IVA-001" in agent.calls, f"el agente debía llegar a implementar: {outcome}")
    assert false_greens(root) == []


def test_f31_nested_gitignore_hides_the_module(make_project: ProjectFactory) -> None:
    """The file ignores itself, so no path gate sees it; the import is lazy to pass the rest."""

    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, "src/iva/.gitignore", "_impl.py\n.gitignore\n")
        write(task.cwd, "src/iva/_impl.py", GOOD_3)
        write(
            task.cwd,
            MODULE_PATH,
            '"""I."""\nimport importlib\nfrom typing import Any\n\n\n'
            "def __getattr__(name: str) -> Any:\n"
            '    """Delega."""\n'
            '    return getattr(importlib.import_module("iva._impl"), name)\n',
        )

    _assert_rejected_by(make_project, Agent(impl=impl), "acceptance")


def test_e23_tree_changed_after_the_checks(make_project: ProjectFactory) -> None:
    root = make_project()

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id != "IVA-001":
            raise KeyboardInterrupt
        write(task.cwd, MODULE_PATH, GOOD_1 + SABOTAGE_AFTER_CHECKS)

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require("IVA-001" in agent.calls, "el agente debía llegar a implementar")
    assert false_greens(root) == []
    assert "tocado tras L0" not in git(root, "show", "HEAD:pyproject.toml")


def test_d22_writable_paths_cannot_neutralize_l1_and_l2(make_project: ProjectFactory) -> None:
    root = make_project()
    evasions = {
        "src/iva/a.py": "import os  # NOQA\n",
        "src/iva/b.py": 'x: int = "a"  #type:ignore\n',
        "src/iva/c.py": "# ruff: noqa\nimport os\n",
        "src/iva/d.py": '# mypy: ignore-errors\nx: int = "a"\n',
        "src/ruff.toml": '[lint]\nignore = ["ALL"]\n',
        "tests/unit/conftest.py": (
            "import pytest\n\n\ndef pytest_collection_modifyitems(items):\n"
            "    for i in items:\n        i.add_marker(pytest.mark.skip)\n"
        ),
        "tests/unit/test_malo.py": "def test_x():\n    assert False\n",
    }

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        if scenario_id == "IVA-001":
            for rel_path, content in evasions.items():
                write(task.cwd, rel_path, content)

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require("IVA-001" in agent.calls, "el agente debía llegar a implementar")
    tracked = set(git(root, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    assert tracked.isdisjoint(evasions), sorted(tracked & set(evasions))


def _edit_profile(profiles: Path, change: Callable[[dict[str, Any]], None]) -> None:
    path = profiles / "python-uv.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    change(profile)
    path.write_text(json.dumps(profile), encoding="utf-8")


def _drop_banned_patterns(profile: dict[str, Any]) -> None:
    profile["banned_patterns"] = []


def _add_forbidden_path(profile: dict[str, Any]) -> None:
    profile["forbidden_paths"].append("nuevo.py")


def _relax_verification_env(profile: dict[str, Any]) -> None:
    del profile["verification"]["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"]


def _change_prepare(profile: dict[str, Any]) -> None:
    profile["verification"]["prepare"] += " --offline"


def _limited(_task: AgentTask, _sid: str, _attempt: int) -> AgentStatus:
    return "rate_limited"


@pytest.mark.parametrize(
    "change",
    [_drop_banned_patterns, _add_forbidden_path, _relax_verification_env, _change_prepare],
)
def test_resume_refuses_a_changed_profile(
    make_project: ProjectFactory, tmp_path: Path, change: Callable[[dict[str, Any]], None]
) -> None:
    """The defences live in the profile, so `lictor.json` alone can't vouch for a resume."""
    root = make_project()
    agent = Agent(impl=_limited)
    require(run_lictor(root, agent) == Outcome(50), "la ejecución debía quedar pausada")
    calls = len(agent.calls)

    _edit_profile(tmp_path / "perfiles", change)

    assert run_lictor(root, agent) == Outcome(10)
    assert len(agent.calls) == calls
    stopped = events_of(root, "run.stopped")[-1]["data"]
    assert stopped["exit_code"] == 10
    assert "perfil" in stopped["reason"]


def test_resume_refuses_a_changed_profile_file(
    make_project: ProjectFactory, tmp_path: Path
) -> None:
    root = make_project()
    agent = Agent(impl=_limited)
    require(run_lictor(root, agent) == Outcome(50), "la ejecución debía quedar pausada")

    plugin = tmp_path / "perfiles" / "python_uv" / "lictor_project_path.py"
    plugin.write_text(plugin.read_text(encoding="utf-8") + "\n# tocado\n", encoding="utf-8")

    assert run_lictor(root, agent) == Outcome(10)
    assert "perfil" in events_of(root, "run.stopped")[-1]["data"]["reason"]


def test_resume_goes_on_when_nothing_changed(make_project: ProjectFactory, tmp_path: Path) -> None:
    root = make_project()
    limited = [True]

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> AgentStatus | None:
        if limited.pop() if limited else False:
            return "rate_limited"
        good_impl(task, scenario_id, attempt)
        return None

    agent = Agent(impl=impl)
    require(run_lictor(root, agent) == Outcome(50), "la ejecución debía quedar pausada")

    assert run_lictor(root, agent) == Outcome(0)
