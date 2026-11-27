"""F82, ADR-0019: the code reads its own package's metadata, and the project isn't installed.

The verification environment never installs the project, on purpose. Before 0.22.0, a package
whose `__init__.py` did `version("<name>")`, as agentix's does, failed to collect every unit test
in the verification, and a group of entry points of its own was empty without anything saying so,
as pyverifactu's. The path plugin now registers the project's distribution in memory, read from
the clone's `pyproject.toml`: `unit` runs with the version and the group, and nothing is installed.
"""

from pathlib import Path

from defusedxml import ElementTree
from typer.testing import CliRunner

from lictor.cli import app
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import all_controls, git, require, write

PACKAGE = """\
from importlib.metadata import entry_points, version

__version__ = version("iva")


def rules() -> list[str]:
    return sorted(entry.load() for entry in entry_points(group="iva.reglas"))


def hello() -> str:
    return "Hello from iva!"
"""

RULES = 'GENERAL = "general"\nREDUCIDO = "reducido"\n'

UNIT_TEST = """\
import iva


def test_the_package_knows_its_version() -> None:
    assert iva.__version__ == "0.1.0"


def test_its_own_group_of_entry_points_is_there() -> None:
    assert iva.rules() == ["general", "reducido"]
"""

ENTRY_POINTS = (
    '\n[project.entry-points."iva.reglas"]\n'
    'general = "iva.reglas:GENERAL"\nreducido = "iva.reglas:REDUCIDO"\n'
)


def _reads_its_own_metadata(root: Path) -> None:
    write(root, "src/iva/__init__.py", PACKAGE)
    write(root, "src/iva/reglas.py", RULES)
    write(root, "tests/unit/test_metadata.py", UNIT_TEST)
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    write(root, "pyproject.toml", pyproject + ENTRY_POINTS)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: el paquete lee su propia metadata")


@all_controls
def test_unit_sees_the_projects_version_and_entry_points_without_installing_it(
    make_project: ProjectFactory,
) -> None:
    root = make_project()
    _reads_its_own_metadata(root)

    result = CliRunner().invoke(app, ["check", "--cwd", str(root), "--level", "L2"])

    assert result.exit_code == 0, result.output
    assert "✓ unit" in result.output
    xml = (root / ".lictor" / "check" / "unit.xml").read_text(encoding="utf-8")
    report = ElementTree.fromstring(xml)
    cases = [case.get("name", "") for case in report.iter("testcase")]
    assert sorted(cases) == [
        "test_its_own_group_of_entry_points_is_there",
        "test_the_package_knows_its_version",
    ]
    assert not [case for case in report.iter("testcase") if len(case)]
    require(not git(root, "status", "--short"), "check ensució el árbol")
