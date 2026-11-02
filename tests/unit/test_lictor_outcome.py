"""The profile's outcome plugin, run in a real pytest process as verification would run it."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lictor.adapters.config.loader import DEFAULT_PROFILES_DIR
from lictor.adapters.config.profiles.python_uv import lictor_outcome
from lictor.adapters.readers.junit import JUnitReader
from lictor.domain.model import TestCaseResult

TESTS = """
import pytest
from iva import general


@pytest.fixture
def roto():
    raise RuntimeError("fixture rota")


@pytest.fixture
def sin_hacer():
    raise NotImplementedError


def test_assertion():
    assert 1 == 2


def test_stub():
    general()


def test_driver_raises_not_implemented():
    raise NotImplementedError


def test_attribute_error():
    import iva
    iva.gnral()


def test_setup(roto):
    pass


def test_setup_not_implemented(sin_hacer):
    pass


def test_did_not_raise():
    with pytest.raises(ValueError):
        pass


def test_fail():
    pytest.fail("no")


def test_passes():
    assert True


def test_library():
    import json
    json.loads("{")
"""


@pytest.fixture(scope="module")
def cases(tmp_path_factory: pytest.TempPathFactory) -> dict[str, TestCaseResult]:
    project = tmp_path_factory.mktemp("proyecto")
    (project / "src" / "iva").mkdir(parents=True)
    (project / "src" / "iva" / "__init__.py").write_text(
        "def general():\n    raise NotImplementedError\n", encoding="utf-8"
    )
    (project / "tests").mkdir()
    (project / "tests" / "test_x.py").write_text(TESTS, encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    plugin_dir = DEFAULT_PROFILES_DIR / "python_uv"
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("PYTEST")},
        "PYTHONPATH": os.pathsep.join([str(plugin_dir), str(project / "src")]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    report = project / "out.xml"
    command = [sys.executable, "-B", "-m", "pytest", "-p", "lictor_outcome"]
    command += ["-p", "no:cacheprovider", "-q", "tests", "--junitxml", str(report)]
    subprocess.run(
        command,
        cwd=project,
        env=env,
        capture_output=True,
        check=False,
        timeout=120,
    )
    result = JUnitReader().read(str(report), exit_code=1, duration_ms=0)
    return {case.name: case for case in result.test_cases}


@pytest.mark.parametrize(
    ("name", "kind", "origin"),
    [
        ("test_assertion", "assertion", "tests/test_x.py"),
        ("test_stub", "not_implemented", "src/iva/__init__.py"),
        ("test_driver_raises_not_implemented", "not_implemented", "tests/test_x.py"),
        ("test_attribute_error", "error", "tests/test_x.py"),
        ("test_setup", "error", "tests/test_x.py"),
        ("test_setup_not_implemented", "error", "tests/test_x.py"),
        ("test_did_not_raise", "assertion", ""),
        ("test_fail", "assertion", ""),
        ("test_library", "error", ""),
    ],
)
def test_each_failure_carries_its_kind_and_origin(
    cases: dict[str, TestCaseResult], name: str, kind: str, origin: str
) -> None:
    case = cases[name]
    assert case.status in ("failure", "error")
    assert (case.failure_kind, case.origin) == (kind, origin)


def test_a_passing_test_carries_nothing(cases: dict[str, TestCaseResult]) -> None:
    assert cases["test_passes"].properties == {}


def test_the_scenario_a_test_executes_is_recorded(tmp_path: Path) -> None:
    node = SimpleNamespace(user_properties=[])
    request = SimpleNamespace(config=SimpleNamespace(rootpath=tmp_path), node=node)
    feature = SimpleNamespace(filename=str(tmp_path / "features" / "iva.feature"))
    outside = SimpleNamespace(filename="/otro/sitio/iva.feature")

    lictor_outcome.pytest_bdd_before_scenario(
        request,
        feature,
        SimpleNamespace(tags={"lento", "IVA-001"}),
    )
    lictor_outcome.pytest_bdd_before_scenario(request, outside, SimpleNamespace(tags=set()))

    assert node.user_properties == [
        ("lictor.scenario", "IVA-001 lento"),
        ("lictor.feature", "features/iva.feature"),
        ("lictor.scenario", ""),
        ("lictor.feature", ""),
    ]
