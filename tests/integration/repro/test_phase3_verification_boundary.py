"""Phase 3 (C1, C2, A9): a green commit means that exact tree passed, out of the agent's reach."""

from pathlib import Path

import pytest

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    DRIVER,
    DRIVER_PATH,
    GOOD_1,
    GOOD_3,
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
    known_limitation,
    require,
    run_lictor,
    shell,
    write,
)

CHEAT_PLUGIN = (
    '"""P."""\nimport pytest\n\n\n'
    "@pytest.hookimpl(wrapper=True)\n"
    "def pytest_runtest_call(item):\n"
    "    item.obj = lambda *a, **k: None\n"
    "    return (yield)\n"
)
DIST_METADATA = "Metadata-Version: 2.1\nName: lab-cheat\nVersion: 1.0\n"
ENTRY_POINTS = "[pytest11]\nlab_cheat = lab_cheat\n"

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


def _assert_no_false_green(project: ProjectFactory, agent: Agent) -> None:
    root = project()
    outcome = run_lictor(root, agent)
    require("IVA-001" in agent.calls, f"el agente debía llegar a implementar: {outcome}")
    assert false_greens(root) == []


def test_c1_forged_module_bytecode(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        shell([".venv/bin/python", "-c", FORGE_MODULE_PYC, GOOD_3], task.cwd)

    _assert_no_false_green(make_project, Agent(impl=impl))


def test_c2_forged_driver_bytecode(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        shell([".venv/bin/python", "-c", FORGE_DRIVER_PYC], task.cwd)

    _assert_no_false_green(make_project, Agent(impl=impl))


def _plant_plugin(site_packages: Path) -> None:
    write(site_packages, "lab_cheat.py", CHEAT_PLUGIN)
    write(site_packages, "lab_cheat-1.0.dist-info/METADATA", DIST_METADATA)
    write(site_packages, "lab_cheat-1.0.dist-info/entry_points.txt", ENTRY_POINTS)


def test_c3_plugin_in_the_verification_venv_fails_by_hash(make_project: ProjectFactory) -> None:
    root = make_project()
    planted: list[Path] = []

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id == "IVA-001" and not planted:
            for site_packages in task.cwd.glob(f"{VERIFY_ENV}/lib/python*/site-packages"):
                _plant_plugin(site_packages)
                planted.append(site_packages)

    agent = Agent(impl=impl)
    run_lictor(root, agent)

    require(planted, "el ataque debía llegar a la venv de verificación")
    assert false_greens(root) == []
    verified = events_of(root, "verify.finished")
    assert verified[0]["data"]["failed_at"] == "L0-entorno"
    tampered = events_of(root, "env.tampered")
    assert len(tampered) == 1
    assert tampered[0]["data"]["expected"] != tampered[0]["data"]["actual"]
    assert not (planted[0] / "lab_cheat.py").exists(), "la venv debía reconstruirse"


def test_c3_forged_bytecode_in_the_verification_venv_fails_by_hash(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    planted: list[Path] = []

    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        if scenario_id == "IVA-001" and not planted:
            for site_packages in task.cwd.glob(f"{VERIFY_ENV}/lib/python*/site-packages"):
                pyc = site_packages / "_pytest" / "__pycache__" / "forjado.cpython-312.pyc"
                write(pyc.parent, pyc.name, b"\x00")
                planted.append(pyc)

    run_lictor(root, Agent(impl=impl))

    require(planted, "el ataque debía llegar a la venv de verificación")
    assert events_of(root, "verify.finished")[0]["data"]["failed_at"] == "L0-entorno"
    assert not planted[0].exists(), "la venv debía reconstruirse"


def test_c3_plugin_in_the_agents_venv_does_not_reach_verification(
    make_project: ProjectFactory,
) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        for site_packages in task.cwd.glob(".venv/lib/python*/site-packages"):
            _plant_plugin(site_packages)

    _assert_no_false_green(make_project, Agent(impl=impl))


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

    _assert_no_false_green(make_project, Agent(impl=impl))


@known_limitation("C1: el informe lo escribe el mismo proceso que el código bajo test")
def test_c6_code_under_test_forges_the_report(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, FORGE_JUNIT)

    _assert_no_false_green(make_project, Agent(impl=impl))


def test_f31_nested_gitignore_hides_the_module(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, "src/iva/.gitignore", "_impl.py\n.gitignore\n")
        write(task.cwd, "src/iva/_impl.py", GOOD_3)
        write(
            task.cwd,
            MODULE_PATH,
            '"""I."""\nfrom iva._impl import general, negativo, reducido\n\n'
            '__all__ = ["general", "negativo", "reducido"]\n',
        )

    _assert_no_false_green(make_project, Agent(impl=impl))


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
