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
from lictor.domain.reports import JUnitReport

TESTS = """
from types import SimpleNamespace

import lictor_outcome
import pytest
from iva import general


def paso_falla(request, kind, exc):
    \"\"\"What the runner of the Gherkin does when a step raises: tells the plugin, re-raises.\"\"\"
    lictor_outcome.pytest_bdd_step_error(request, SimpleNamespace(type=kind), exc)
    raise exc


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


def test_then(request):
    paso_falla(request, "then", AssertionError("no"))


def test_given_error(request):
    paso_falla(request, "given", KeyError("x"))


def test_fail_in_a_step(request):
    paso_falla(request, "when", pytest.fail.Exception("no"))


def test_another_failure_than_the_step_s(request):
    lictor_outcome.pytest_bdd_step_error(request, SimpleNamespace(type="given"), ValueError())
    assert 1 == 2


def test_xfail_in_a_step(request):
    paso_falla(request, "when", pytest.xfail.Exception("aún no"))
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
    result = JUnitReader().read(report)
    assert isinstance(result, JUnitReport), result
    return {case.name: case for case in result.cases}


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


def _line_in_the_tests(text: str) -> str:
    """The line of the test module that holds `text`, from 1, as a frame names it."""
    return str(TESTS.splitlines().index(text) + 1)


@pytest.mark.parametrize(
    ("name", "frame", "line"),
    [
        ("test_assertion", "tests/test_x.py", "    assert 1 == 2"),
        ("test_stub", "src/iva/__init__.py", "2"),
        # pytest raises these for the test: the origin is inside pytest, the frame is the test's.
        ("test_did_not_raise", "tests/test_x.py", "    with pytest.raises(ValueError):"),
        ("test_fail", "tests/test_x.py", '    pytest.fail("no")'),
        ("test_library", "tests/test_x.py", '    json.loads("{")'),
    ],
)
def test_each_failure_carries_the_last_frame_inside_the_project_and_its_line(
    cases: dict[str, TestCaseResult], name: str, frame: str, line: str
) -> None:
    expected = line if line.isdigit() else _line_in_the_tests(line)
    assert (cases[name].frame, cases[name].frame_line) == (frame, int(expected))


def test_a_failure_with_no_frame_inside_the_project_carries_no_line(tmp_path: Path) -> None:
    """Every frame of an exception raised here lies outside `tmp_path`, the project."""
    try:
        raise ValueError("fuera")
    except ValueError as raised:
        excinfo = pytest.ExceptionInfo.from_exception(raised)

    assert lictor_outcome._last_frame_inside(excinfo, tmp_path) == ("", "")


def test_a_passing_test_carries_nothing(cases: dict[str, TestCaseResult]) -> None:
    assert cases["test_passes"].properties == {}


@pytest.mark.parametrize(
    ("name", "step", "kind"),
    [
        ("test_then", "then", "assertion"),
        ("test_given_error", "given", "error"),
        ("test_fail_in_a_step", "when", "assertion"),
        # The step raised something else, which the test caught: its failure is outside a step.
        ("test_another_failure_than_the_step_s", None, "assertion"),
        ("test_assertion", None, "assertion"),
        ("test_setup", None, "error"),
    ],
)
def test_each_failure_carries_the_step_that_raised_it(
    cases: dict[str, TestCaseResult], name: str, step: str | None, kind: str
) -> None:
    assert (cases[name].failed_step, cases[name].failure_kind) == (step, kind)
    assert "lictor.step" in cases[name].properties


def test_an_xfail_raised_in_a_step_fails_nothing_and_carries_no_step(
    cases: dict[str, TestCaseResult],
) -> None:
    """The runner tells of an `xfail` as of any failure of a step: it isn't one."""
    assert cases["test_xfail_in_a_step"].status == "skipped"
    assert cases["test_xfail_in_a_step"].properties == {}


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
