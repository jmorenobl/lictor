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
    lictor_outcome.pytest_bdd_step_error(
        request=request, step=SimpleNamespace(type=kind), step_func=paso_falla, exception=exc
    )
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
    lictor_outcome.pytest_bdd_step_error(
        request=request, step=SimpleNamespace(type="given"), step_func=paso_falla,
        exception=ValueError(),
    )
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


# F85: the statements a failure was raised in, from the step's own, with what each one feeds.

STEPS = '''
import functools
from xml.etree import ElementTree

import lictor_outcome


def ejecuta(request, func, **kwargs):
    """What the runner of the Gherkin does: call the step, tell the plugin of its failure."""
    try:
        return func(**kwargs)
    except BaseException as exc:
        lictor_outcome.pytest_bdd_step_error(
            request=request, step=type("S", (), {"type": "then"})(), step_func=func, exception=exc
        )
        raise


def _texts(xml, tag):
    root = ElementTree.fromstring(xml)  # PARSEA
    return [e.text for e in root.iter(tag)]  # DEVUELVE


def una_linea(xml, v):
    assert _texts(xml, "a") == [v]  # UNA


def varias_lineas(xml, v):
    assert (  # VARIAS
        _texts(xml, "a")
        == [v]
    )


def dos_lineas(xml, v):
    root = ElementTree.fromstring(xml)  # RAIZ
    otro = 1
    assert root.findtext("a") == v  # COMPARA
    assert otro == 1


def por_un_for(xml, v):
    for el in ElementTree.fromstring(xml):  # FOR
        assert el.text == v  # DENTRO


def reasignado(xml, v):
    root = ElementTree.fromstring(xml)  # CORTA
    root = None
    assert root == v


def dos_en_una(xml, v):
    a = 1; ElementTree.fromstring(xml)  # DOS


def en_el_mensaje(xml, v):
    root = ElementTree.fromstring(xml)  # MENSAJE
    assert v == "S", f"en {root.tag}"


def if_en_una_linea(xml, v):
    if ElementTree.fromstring(xml): assert v == "S"  # IF_LINEA


def _dos_en_una(xml):
    a = 1; ElementTree.fromstring(xml)


def llama_a_dos_en_una(xml, v):
    assert _dos_en_una(xml) == v  # LLAMA


def aserta(xml, v):
    assert v == "otro"  # DIRECTA


def envuelve(func):
    @functools.wraps(func)
    def envoltorio(*args, **kwargs):
        return func(*args, **kwargs)  # ENVOLTORIO
    return envoltorio


def sin_wraps(func):
    def envoltorio(*args, **kwargs):
        return func(*args, **kwargs)  # SIN_WRAPS
    return envoltorio


@envuelve
def decorado(xml, v):
    assert _texts(xml, "a") == [v]  # DECORADO


@sin_wraps
def decorado_sin_wraps(xml, v):
    assert _texts(xml, "a") == [v]  # DECORADO_SIN_WRAPS


BAD = "<r><a>S</a></r>\\0"


def test_una_linea(request):
    ejecuta(request, una_linea, xml=BAD, v="S")


def test_varias_lineas(request):
    ejecuta(request, varias_lineas, xml=BAD, v="S")


def test_dos_lineas(request):
    ejecuta(request, dos_lineas, xml=BAD, v="S")


def test_por_un_for(request):
    ejecuta(request, por_un_for, xml=BAD, v="S")


def test_reasignado(request):
    ejecuta(request, reasignado, xml=BAD, v="S")


def test_dos_en_una(request):
    ejecuta(request, dos_en_una, xml=BAD, v="S")


def test_en_el_mensaje(request):
    ejecuta(request, en_el_mensaje, xml=BAD, v="S")


def test_if_en_una_linea(request):
    ejecuta(request, if_en_una_linea, xml=BAD, v="S")


def test_llama_a_dos_en_una(request):
    ejecuta(request, llama_a_dos_en_una, xml=BAD, v="S")


def test_aserta(request):
    ejecuta(request, aserta, xml="<r/>", v="S")


def test_decorado(request):
    ejecuta(request, decorado, xml=BAD, v="S")


def test_decorado_sin_wraps(request):
    ejecuta(request, decorado_sin_wraps, xml=BAD, v="S")


def test_fuera_de_un_paso():
    _texts(BAD, "a")
'''


@pytest.fixture(scope="module")
def statements(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    project = tmp_path_factory.mktemp("pasos")
    (project / "tests").mkdir()
    (project / "tests" / "test_pasos.py").write_text(STEPS, encoding="utf-8")
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("PYTEST")},
        "PYTHONPATH": str(DEFAULT_PROFILES_DIR / "python_uv"),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    report = project / "out.xml"
    command = [sys.executable, "-B", "-m", "pytest", "-p", "lictor_outcome"]
    command += ["-p", "no:cacheprovider", "-q", "tests", "--junitxml", str(report)]
    subprocess.run(command, cwd=project, env=env, capture_output=True, check=False, timeout=120)
    result = JUnitReader().read(report)
    assert isinstance(result, JUnitReport), result
    return {case.name: case.properties.get("lictor.statement", "") for case in result.cases}


def _at(mark: str) -> int:
    """The line of the steps' module that holds the comment `# mark`, from 1."""
    return next(n for n, text in enumerate(STEPS.splitlines(), 1) if text.endswith(f"# {mark}"))


def _element(mark: str, *fed: str) -> str:
    return f"tests/test_pasos.py:{_at(mark)}:{','.join(str(_at(f)) for f in fed)}"


@pytest.mark.parametrize(
    ("name", "chain"),
    [
        # The assertion calls the helper that couldn't parse: the step's statement, then its own.
        ("test_una_linea", [_element("UNA"), _element("PARSEA", "DEVUELVE")]),
        # Wherever an assertion of several lines raised, it is named by its first.
        ("test_varias_lineas", [_element("VARIAS"), _element("PARSEA", "DEVUELVE")]),
        # What a statement binds is followed to what loads it, and on from there.
        ("test_dos_lineas", [_element("RAIZ", "COMPARA")]),
        ("test_por_un_for", [_element("FOR", "DENTRO")]),
        # A name bound again stops it.
        ("test_reasignado", [_element("CORTA")]),
        ("test_aserta", [_element("DIRECTA")]),
        # A decorator's frame comes before the step's, which is where the chain starts.
        ("test_decorado", [_element("DECORADO"), _element("PARSEA", "DEVUELVE")]),
        # Without `functools.wraps` the step is the wrapper: it starts there.
        (
            "test_decorado_sin_wraps",
            [_element("SIN_WRAPS"), _element("DECORADO_SIN_WRAPS"), _element("PARSEA", "DEVUELVE")],
        ),
        # Two statements on one line can't be told apart by it.
        ("test_dos_en_una", []),
        ("test_if_en_una_linea", []),
        # A frame of the project it can't name leaves no chain: a shorter one would join more.
        ("test_llama_a_dos_en_una", []),
        # An assertion's message is evaluated only once it failed: it reads nothing.
        ("test_en_el_mensaje", [_element("MENSAJE")]),
        ("test_fuera_de_un_paso", []),
    ],
)
def test_a_failure_carries_the_statements_it_was_raised_in(
    statements: dict[str, str], name: str, chain: list[str]
) -> None:
    assert statements[name] == ";".join(chain)


def test_a_relative_path_is_of_no_place_even_if_it_resolves_inside(tmp_path: Path) -> None:
    """A compiled extension names its source relatively, as `src/lxml/parser.pxi`: pytest gives
    such a path as it is, and resolving it against the working directory would put it inside."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "parser.pxi").write_text("", encoding="utf-8")
    cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        assert lictor_outcome._relative("src/parser.pxi", tmp_path) == ""
        assert lictor_outcome._relative(tmp_path / "src" / "parser.pxi", tmp_path) == (
            "src/parser.pxi"
        )
    finally:
        os.chdir(cwd)


def test_a_step_whose_wrapped_chain_is_a_cycle_still_gives_its_own_code() -> None:
    def paso() -> None:
        """A step."""

    paso.__wrapped__ = paso  # type: ignore[attr-defined]

    assert lictor_outcome._codes(paso) == (paso.__code__,)
