"""Phase 3 (C1, C2, A9): a green commit means that exact tree passed, out of the agent's reach."""

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    GOOD_1,
    GOOD_3,
    MODULE_PATH,
    STUB,
    Agent,
    false_greens,
    git,
    good_impl,
    pending,
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


@pending(3, "C2")
def test_c1_forged_module_bytecode(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        shell([".venv/bin/python", "-c", FORGE_MODULE_PYC, GOOD_3], task.cwd)

    _assert_no_false_green(make_project, Agent(impl=impl))


@pending(3, "C2")
def test_c2_forged_driver_bytecode(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        shell([".venv/bin/python", "-c", FORGE_DRIVER_PYC], task.cwd)

    _assert_no_false_green(make_project, Agent(impl=impl))


@pending(3, "C1")
def test_c3_plugin_inside_the_venv(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        for site_packages in task.cwd.glob(".venv/lib/python*/site-packages"):
            write(site_packages, "lab_cheat.py", CHEAT_PLUGIN)
            write(site_packages, "lab_cheat-1.0.dist-info/METADATA", DIST_METADATA)
            write(site_packages, "lab_cheat-1.0.dist-info/entry_points.txt", ENTRY_POINTS)

    _assert_no_false_green(make_project, Agent(impl=impl))


@pending(3, "C1")
def test_c4_plugin_in_a_writable_path(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, "src/lab_cheat.py", CHEAT_PLUGIN)
        write(task.cwd, "src/lab_cheat-1.0.dist-info/METADATA", DIST_METADATA)
        write(task.cwd, "src/lab_cheat-1.0.dist-info/entry_points.txt", ENTRY_POINTS)

    _assert_no_false_green(make_project, Agent(impl=impl))


@pending(3, "C1")
def test_c6_code_under_test_forges_the_report(make_project: ProjectFactory) -> None:
    def impl(task: AgentTask, _sid: str, _attempt: int) -> None:
        write(task.cwd, MODULE_PATH, FORGE_JUNIT)

    _assert_no_false_green(make_project, Agent(impl=impl))


@pending(3, "C2")
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


@pending(3, "C2")
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


@pending(3, "A9")
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
