"""What the profile's survey says each command sees, read from files only (ADR-0015).

Each case is a tree and a command, and what pytest would run or load there. The cases of the
review (`.audits/qa/contrato-arranque/revision-fable.md`) are rows here: a doubt is "unknown",
never a guess.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lictor.adapters.config.profiles.python_uv import lictor_survey

PKG = {"src/pkg/__init__.py": "def f():\n    return 1\n"}
UNIT = {"tests/unit/test_a.py": "from pkg import f\n\ndef test_a():\n    assert f() == 1\n"}
TESTPATHS = '[tool.pytest.ini_options]\ntestpaths = ["tests/unit"]\n'
IGNORING = '[tool.pytest.ini_options]\naddopts = "--ignore=tests/integration"\n'
OTHER = {"tests/integration/test_i.py": "import pkg\n\ndef test_i():\n    assert pkg.f()\n"}


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _survey(root: Path, **runs: str) -> dict[str, Any]:
    commands = [{"id": cid, "run": run} for cid, run in runs.items()]
    return lictor_survey.survey(root, "src", commands, ["pyproject.toml"])


def _command(report: dict[str, Any], cid: str) -> dict[str, Any]:
    return next(c for c in report["commands"] if c["id"] == cid)


def _unrun(report: dict[str, Any]) -> list[str]:
    return [t["path"] for t in report["tests"] if not t["run_by"]]


@pytest.mark.parametrize(
    ("run", "paths", "selects"),
    [
        ("uv run pytest tests/unit -q --junitxml {out}", ["tests/unit"], False),
        ("uv run pytest --junitxml {out} tests/unit", ["tests/unit"], False),
        ("uv run pytest tests/unit/test_a.py::test_a -q", ["tests/unit/test_a.py"], False),
        ("uv run --with pytest-xdist pytest tests -n auto", ["tests"], False),
        ("uv run -q -- pytest tests/", ["tests"], False),
        ("uv run python -m pytest tests -k fast", ["tests"], True),
        ('uv run pytest tests -m "not slow" -p no:cacheprovider', ["tests"], True),
        ("uv run pytest tests -pno:cacheprovider -rA", ["tests"], False),
        ("uv run pytest tests/acceptance -q", ["tests/acceptance"], False),
        ("uv run pytest -q", ["."], False),
    ],
)
def test_a_pytest_command_runs_its_paths(
    tmp_path: Path, run: str, paths: list[str], selects: bool
) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT})

    command = _command(_survey(root, unit=run), "unit")

    assert command["status"] == "understood"
    assert command["tests"] is True
    assert command["paths"] == paths
    assert command["selects"] is selects


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        ("make test", "make"),
        ("uv run tox -e py", "tox"),
        ("uv run python -m unittest discover -s tests", "python"),
        ("sh -c 'cd . && uv run pytest tests'", "sh"),
        ("uv run pytest --cov-config pyproject.toml -q", "--cov-config"),
        ("uv run pytest -q --json-report-file out/r.json", "--json-report-file"),
        ("uv run pytest --pyargs pkg", "--pyargs"),
        ("uv run pytest tests --ignore-glob '*_i.py'", "--ignore-glob"),
        ("uv run pytest tests --confcutdir=tests/unit", "--confcutdir"),
        ("uv run pytest -c other.ini", "-c"),
        ("uv run coverage run -m pytest", "coverage run"),
        ("uv run", "uv run sin programa"),
    ],
)
def test_a_command_whose_reach_cant_be_read_is_unknown(
    tmp_path: Path, run: str, reason: str
) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT})

    command = _command(_survey(root, unit=run), "unit")

    assert command["status"] == "unknown"
    assert reason in command["reason"]
    assert "paths" not in command


def test_a_command_that_runs_no_tests_reads_the_paths_it_names(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT})

    report = _survey(
        root,
        lint="uv run ruff check src tests",
        types="uv run mypy src",
        complexity="uv run python -I -B -m lictor_complexity src --out {out}",
        secrets="gitleaks detect --no-banner",
    )

    assert _command(report, "lint")["paths"] == ["src", "tests"]
    assert _command(report, "types")["paths"] == ["src"]
    assert _command(report, "complexity")["paths"] == ["src"]
    # A command that names no path reads nothing it can be credited with.
    assert _command(report, "secrets")["paths"] == []
    assert all(not _command(report, c)["tests"] for c in ("lint", "types", "secrets"))


def test_tests_no_command_runs_are_listed_with_how_many_they_define(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            **PKG,
            **UNIT,
            **OTHER,
            "tests/integration/test_class.py": (
                "import unittest\n\nclass Checks(unittest.TestCase):\n"
                "    def test_x(self):\n        pass\n\n"
                "class TestMore:\n    def test_y(self):\n        pass\n"
                "    def helper(self):\n        pass\n"
            ),
        },
    )

    report = _survey(root, unit="uv run pytest tests/unit -q")

    assert {t["path"]: t["tests"] for t in report["tests"]} == {
        "tests/integration/test_class.py": 2,
        "tests/integration/test_i.py": 1,
        "tests/unit/test_a.py": 1,
    }
    assert _unrun(report) == ["tests/integration/test_class.py", "tests/integration/test_i.py"]


def test_a_file_named_like_a_test_without_tests_is_not_one(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            **PKG,
            "scripts/test_connection.py": "import pkg\nprint(pkg.f())\n",
            "src/pkg/test_utils.py": "def helper():\n    return 1\n",
        },
    )

    report = _survey(root, unit="uv run pytest tests -q")

    assert report["tests"] == []
    assert [u["path"] for u in report["code_users"]] == ["scripts/test_connection.py"]


def test_ignore_takes_tests_and_conftests_out_of_a_command(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            **PKG,
            **UNIT,
            **OTHER,
            "tests/integration/conftest.py": "import pkg\n",
        },
    )

    report = _survey(root, unit="uv run pytest tests --ignore=tests/integration -q")

    assert _unrun(report) == ["tests/integration/test_i.py"]
    assert report["loaded"] == []


@pytest.mark.parametrize(
    ("files", "unrun"),
    [
        # pytest.ini wins even empty: the testpaths of pyproject.toml don't count.
        ({"pytest.ini": "[pytest]\n", "pyproject.toml": TESTPATHS}, []),
        ({"pyproject.toml": TESTPATHS}, True),
        ({"pyproject.toml": '[tool.pytest]\ntestpaths = ["tests/unit"]\n'}, True),
        (
            {"pyproject.toml": IGNORING},
            True,
        ),
        ({"tox.ini": "[pytest]\ntestpaths = tests/unit\n"}, True),
        ({"setup.cfg": "[tool:pytest]\ntestpaths = tests/unit\n"}, True),
        ({"setup.cfg": "[metadata]\nname = x\n"}, []),
    ],
)
def test_pytest_reads_its_configuration_as_pytest_does(
    tmp_path: Path, files: dict[str, str], unrun: bool | list[str]
) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT, **OTHER, **files})

    report = _survey(root, unit="uv run pytest -q")

    expected = ["tests/integration/test_i.py"] if unrun is True else unrun
    assert _unrun(report) == expected


def test_addopts_that_select_mark_the_command(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            **PKG,
            **UNIT,
            "pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"-m 'not slow'\"\n",
        },
    )

    assert _command(_survey(root, unit="uv run pytest tests -q"), "unit")["selects"] is True


def test_a_configuration_that_cant_be_read_is_unknown(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT, "pyproject.toml": "[tool.pytest\nesto no es TOML"})

    command = _command(_survey(root, unit="uv run pytest tests -q"), "unit")

    assert command["status"] == "unknown"
    assert "pyproject.toml" in command["reason"]


def test_a_conftest_above_or_inside_what_runs_is_loaded_with_what_it_imports(
    tmp_path: Path,
) -> None:
    root = _tree(
        tmp_path,
        {
            **PKG,
            **UNIT,
            **OTHER,
            "conftest.py": "",
            "tests/conftest.py": (
                "import pytest\nfrom pkg import f\nfrom pkg.sub import g as h\n"
                "\n@pytest.fixture\ndef value():\n    return f()\n"
            ),
            "tests/unit/conftest.py": "import os\n",
            "tests/integration/conftest.py": "import pkg\n",
        },
    )

    report = _survey(
        root, unit="uv run pytest tests/unit -q", acceptance="uv run pytest tests/acceptance"
    )

    loaded = {f["path"]: f for f in report["loaded"]}
    assert set(loaded) == {"conftest.py", "tests/conftest.py", "tests/unit/conftest.py"}
    assert loaded["tests/conftest.py"]["by"] == ["unit", "acceptance"]
    assert loaded["tests/unit/conftest.py"]["by"] == ["unit"]
    assert loaded["tests/conftest.py"]["imports"] == [
        {"line": 2, "module": "pkg", "names": ["f"]},
        {"line": 3, "module": "pkg.sub", "names": ["g"]},
    ]
    assert loaded["conftest.py"]["imports"] == []


def test_the_modules_the_run_tests_import_are_loaded_too(tmp_path: Path) -> None:
    """`pyverifactu`: `tests/fixtures/sample_invoices.py`, out of reach, under three unit tests."""
    root = _tree(
        tmp_path,
        {
            **PKG,
            "tests/fixtures/__init__.py": "",
            "tests/fixtures/sample.py": "from pkg import f\nfrom tests.fixtures import deeper\n",
            "tests/fixtures/deeper.py": "from pkg import f as g\n",
            "tests/unit/__init__.py": "",
            "tests/unit/test_a.py": (
                "from fixtures.sample import f\nfrom . import helpers\n\n"
                "def test_a():\n    assert f()\n"
            ),
            "tests/unit/helpers.py": "VALUE = 1\n",
            "tests/unused.py": "import pkg\n",
        },
    )

    report = _survey(root, unit="uv run pytest tests/unit -q")

    loaded = {f["path"]: f for f in report["loaded"]}
    assert set(loaded) == {
        "tests/fixtures/__init__.py",
        "tests/fixtures/sample.py",
        "tests/fixtures/deeper.py",
        "tests/unit/__init__.py",
        "tests/unit/helpers.py",
    }
    assert all(f["kind"] == "module" and f["by"] == ["unit"] for f in loaded.values())
    assert loaded["tests/fixtures/deeper.py"]["imports"] == [
        {"line": 1, "module": "pkg", "names": ["f"]}
    ]
    assert [u["path"] for u in report["code_users"]] == ["tests/unused.py"]


def test_code_that_uses_the_project_says_who_reads_it(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            **PKG,
            "examples/demo.py": "from pkg import f\nprint(f())\n",
            "scripts/tool.py": "import pkg.sub\n",
            "docs/conf.py": "project = 'x'\n",
        },
    )

    report = _survey(root, lint="uv run ruff check src scripts", types="uv run mypy src")

    assert [(u["path"], u["read_by"]) for u in report["code_users"]] == [
        ("examples/demo.py", []),
        ("scripts/tool.py", ["lint"]),
    ]


def test_another_project_inside_is_named_and_not_looked_at(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            **PKG,
            "pyproject.toml": "[project]\nname = 'x'\n",
            "examples/other/pyproject.toml": "[project]\nname = 'y'\n",
            "examples/other/tests/unit/test_o.py": "def test_o():\n    pass\n",
        },
    )

    report = _survey(root, unit="uv run pytest tests/unit -q")

    assert report["tests"] == []
    assert report["skipped"]["projects"] == ["examples/other"]


def test_a_file_that_cant_be_parsed_is_named(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**PKG, "tests/unit/test_bad.py": "def test_x(:\n"})

    report = _survey(root, unit="uv run pytest tests/unit -q")

    assert report["skipped"]["unreadable"] == ["tests/unit/test_bad.py"]


def test_without_code_in_its_directory_there_are_no_modules(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"pkg/__init__.py": "", **UNIT})

    assert _survey(root, unit="uv run pytest tests -q")["code"] == {"dir": "src", "modules": []}


def test_the_program_uv_run_launches_says_whether_the_environment_has_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "env"
    machine = tmp_path / "machine"
    for directory, name in ((env / "bin", "pytest"), (machine, "ruff")):
        directory.mkdir(parents=True)
        program = directory / name
        program.write_text("#!/bin/sh\n", encoding="utf-8")
        program.chmod(0o755)
    monkeypatch.setattr(sys, "prefix", str(env))
    monkeypatch.setenv("PATH", f"{env / 'bin'}:{machine}")
    root = _tree(tmp_path / "p", {**PKG, **UNIT})

    report = _survey(
        root,
        unit="uv run pytest tests/unit",
        lint="uv run ruff check src",
        types="uv run mypy src",
        secrets="gitleaks detect",
    )

    assert _command(report, "unit")["program"] == {
        "name": "pytest",
        "path": str(env / "bin" / "pytest"),
        "in_env": True,
    }
    assert _command(report, "lint")["program"] == {
        "name": "ruff",
        "path": str(machine / "ruff"),
        "in_env": False,
    }
    assert _command(report, "types")["program"] == {"name": "mypy", "path": None, "in_env": False}
    assert _command(report, "secrets")["program"] is None


def test_the_command_writes_the_report(tmp_path: Path) -> None:
    root = _tree(tmp_path / "p", {**PKG, **UNIT, **OTHER})
    given = tmp_path / "in.json"
    given.write_text(
        json.dumps({"commands": [{"id": "unit", "run": "uv run pytest tests/unit"}], "detect": []}),
        encoding="utf-8",
    )
    out = tmp_path / "out.json"

    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            lictor_survey.__file__,
            "src",
            "--in",
            str(given),
            "--out",
            str(out),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert _unrun(report) == ["tests/integration/test_i.py"]


@pytest.mark.parametrize("args", [[], ["src"], ["src", "--in", "nada.json", "--out", "r.json"]])
def test_a_command_without_what_it_needs_fails(tmp_path: Path, args: list[str]) -> None:
    assert lictor_survey.main(args) == 2
    assert not (tmp_path / "r.json").exists()
