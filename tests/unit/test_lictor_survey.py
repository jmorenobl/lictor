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
        ("uv run pytest --cov-context test -q", "--cov-context"),
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


def _addopts(addopts: str, extra: str = "") -> dict[str, str]:
    return {"pyproject.toml": f'[tool.pytest.ini_options]\naddopts = "{addopts}"\n{extra}'}


@pytest.mark.parametrize(
    ("addopts", "run", "paths", "eaten"),
    [
        # `--cov=X` changes nothing of what runs.
        ("-ra --cov=pkg", "uv run pytest tests/unit -q", ["tests/unit"], None),
        ("--cov -ra", "uv run pytest tests/unit -q", ["tests/unit"], None),
        # A `--cov` that ends `addopts` takes the command's first path: pytest runs everything.
        ("-ra --cov", "uv run pytest tests/unit -q", ["."], "tests/unit"),
        ("-ra --cov", "uv run pytest tests/unit tests/other", ["tests/other"], "tests/unit"),
        ("-ra --cov", "uv run pytest -q tests/unit", ["tests/unit"], None),
        # In the command a word after `--cov` is its value, always, and nobody is surprised.
        ("-ra", "uv run pytest --cov src tests/unit", ["tests/unit"], None),
        ("-ra", "uv run pytest --cov tests/unit", ["."], None),
    ],
)
def test_cov_takes_a_value_only_when_a_word_follows_it(
    tmp_path: Path, addopts: str, run: str, paths: list[str], eaten: str | None
) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT, **OTHER, **_addopts(addopts)})

    command = _command(_survey(root, unit=run), "unit")

    assert command["status"] == "understood"
    assert command["paths"] == paths
    assert command["eaten"] == eaten
    assert command["config"] == "pyproject.toml"


def test_bookwright_cov_in_addopts_no_longer_hides_the_tests_no_control_runs(
    tmp_path: Path,
) -> None:
    files = _addopts(
        "-ra --strict-markers --cov=pkg --cov-report=term-missing",
        "[tool.coverage.report]\nfail_under = 80\n",
    )
    root = _tree(tmp_path, {**PKG, **UNIT, **OTHER, **files})

    report = _survey(
        root, unit="uv run pytest tests/unit -q", acceptance="uv run pytest tests/acceptance -q"
    )

    assert _unrun(report) == ["tests/integration/test_i.py"]
    gate = {"cov": "--cov=pkg", "addopts": "pyproject.toml", "threshold": "80"}
    assert _command(report, "unit")["gate"] == {**gate, "source": "pyproject.toml"}
    assert _command(report, "acceptance")["gate"] == {**gate, "source": "pyproject.toml"}


FAIL_80 = {"setup.cfg": "[coverage:report]\nfail_under = 80\n"}


@pytest.mark.parametrize(
    ("files", "run", "gate"),
    [
        ({".coveragerc": "[report]\nfail_under = 90\n"}, "pytest tests", (".coveragerc", "90")),
        (
            {"setup.cfg": "[coverage:report]\nfail_under = 70.5\n"},
            "pytest tests",
            ("setup.cfg", "70.5"),
        ),
        # The first file coverage.py takes is its configuration, even without a minimum.
        ({".coveragerc": "[run]\nbranch = true\n", **FAIL_80}, "pytest tests", None),
        (
            {"cov.ini": "[report]\nfail_under = 60\n"},
            "pytest tests --cov-config cov.ini",
            ("cov.ini", "60"),
        ),
        ({"setup.cfg": "[coverage:report]\nfail_under = 0\n"}, "pytest tests", None),
        (FAIL_80, "pytest tests --no-cov", None),
        # The command's own minimum is its choice; zero is none.
        (FAIL_80, "pytest tests --cov-fail-under=0", None),
        ({}, "pytest tests", None),
    ],
)
def test_the_minimum_a_command_inherits_with_the_cov_of_addopts(
    tmp_path: Path, files: dict[str, str], run: str, gate: tuple[str, str] | None
) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT, **files, **_addopts("--cov=pkg")})

    found = _command(_survey(root, unit=f"uv run {run}"), "unit")["gate"]

    assert (None if found is None else (found["source"], found["threshold"])) == gate


def test_a_minimum_in_addopts_is_inherited_too(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT, **_addopts("--cov=pkg --cov-fail-under 75")})

    gate = _command(_survey(root, unit="uv run pytest tests/unit"), "unit")["gate"]

    assert gate == {
        "cov": "--cov=pkg",
        "addopts": "pyproject.toml",
        "threshold": "75",
        "source": "pyproject.toml",
    }


def test_without_cov_in_addopts_no_minimum_is_inherited(tmp_path: Path) -> None:
    root = _tree(
        tmp_path, {**PKG, **UNIT, **_addopts("-ra", "[tool.coverage.report]\nfail_under = 80\n")}
    )

    command = _command(_survey(root, unit="uv run pytest tests/unit --cov=pkg"), "unit")

    assert command["gate"] is None


@pytest.mark.parametrize(
    ("run", "tool"),
    [
        ("uv run pytest tests/unit -q", "pytest"),
        ("uv run python -m pytest tests", "pytest"),
        ("uv run ruff check src", "ruff check"),
        ("uv run ruff format --check src", "ruff format"),
        ("uv run ruff format --diff src", "ruff format"),
        # Without `--check` a control formats and passes: it checks nothing.
        ("uv run ruff format src", None),
        ("uv run mypy src", "mypy"),
        ("uv run python -m mypy src", "mypy"),
        ("gitleaks detect", None),
        ("uv run python -I -B -m lictor_complexity src --out {out}", None),
    ],
)
def test_a_control_says_which_check_it_is(tmp_path: Path, run: str, tool: str | None) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT})

    assert _command(_survey(root, c=run), "c")["tool"] == tool


def test_a_checker_reads_the_files_under_its_paths(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {**PKG, **UNIT, "scripts/s.py": "", "pyproject.toml": '[tool.mypy]\nfiles = ["src"]\n'},
    )

    report = _survey(
        root, lint="uv run ruff check", types="uv run mypy", narrow="uv run ruff check src"
    )

    everything = ["scripts/s.py", "src/pkg/__init__.py", "tests/unit/test_a.py"]
    assert _command(report, "lint")["covers"] == everything
    # mypy alone reads the `files` of its configuration.
    assert _command(report, "types")["covers"] == ["src/pkg/__init__.py"]
    assert _command(report, "narrow")["covers"] == ["src/pkg/__init__.py"]


def _ci(root: Path, *steps: dict[str, Any], **runs: str) -> dict[str, Any]:
    commands = [{"id": cid, "run": run} for cid, run in runs.items()]
    ci = [{"file": "ci.yml", "line": i + 1, "origin": "workflow", **s} for i, s in enumerate(steps)]
    return lictor_survey.survey(root, "src", commands, ["pyproject.toml"], ci)


@pytest.mark.parametrize(
    ("run", "origin", "tool"),
    [
        ("uv run ruff format --check src/", "workflow", "ruff format"),
        # A hook that formats fails when it changes a file: it checks.
        ("uv run ruff format", "pre-commit", "ruff format"),
        ("uv run ruff format", "workflow", None),
        ("ruff check --fix", "pre-commit", "ruff check"),
        ("uvx ruff check .", "workflow", "ruff check"),
        ("uv run mypy src/", "workflow", "mypy"),
        ("uv run pytest -m 'not integration' --cov=pkg", "workflow", "pytest"),
        ("uv sync --all-extras", "workflow", None),
        ("uv build", "workflow", None),
        ("uv run --group docs mkdocs build --strict", "workflow", None),
        ("uv python install ${{ matrix.python-version }}", "workflow", None),
        ('echo "$HOME/.cargo/bin"', "workflow", None),
        ("sh", "workflow", None),
        ("awk '{ print", "workflow", None),
        ("python -m zipfile -l dist/x.whl", "workflow", None),
    ],
)
def test_a_step_of_the_ci_is_recognised_with_the_rules_of_the_controls(
    tmp_path: Path, run: str, origin: str, tool: str | None
) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT})

    (step,) = _ci(root, {"run": run, "origin": origin})["ci"]

    assert step["status"] == "understood"
    assert step["tool"] == tool
    assert (step["file"], step["line"], step["run"]) == ("ci.yml", 1, run)


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        ("make lint", "make puede ejecutar cualquier cosa"),
        ("uv run tox -e py", "tox puede ejecutar cualquier cosa"),
        ("hatch run test", "hatch puede ejecutar cualquier cosa"),
        ("bash -c 'uv run pytest'", "bash -c"),
        ("uv run ${{ matrix.cmd }}", "expresión de GitHub Actions"),
        ("uv run pytest ${{ matrix.args }}", "expresión de GitHub Actions"),
        ("uv run pytest -c other.ini", "-c cambia lo que ejecuta"),
        ("uv run ruff check 'src", "comillas sin cerrar"),
    ],
)
def test_a_step_of_the_ci_whose_reach_cant_be_read_is_unknown(
    tmp_path: Path, run: str, reason: str
) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT})

    (step,) = _ci(root, {"run": run})["ci"]

    assert step["status"] == "unknown"
    assert reason in step["reason"]
    assert "covers" not in step


def test_mypy_alone_without_files_in_its_configuration_reaches_nothing(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT})

    (step,) = _ci(root, {"run": "uv run mypy"})["ci"]

    assert (step["status"], step["covers"]) == ("understood", [])


def test_a_hook_that_gets_the_changed_files_reaches_every_file_its_patterns_choose(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT, "scripts/s.py": "", "src/pkg/old_x.py": ""})

    report = _ci(
        root,
        {"run": "ruff check --fix", "origin": "pre-commit", "pass_filenames": True},
        {
            "run": "ruff check --fix",
            "origin": "pre-commit",
            "pass_filenames": True,
            "files": ["^src/"],
            "exclude": ["old_"],
        },
        # Without the files, its patterns only decide when it runs: it reaches its paths.
        {
            "run": "mypy src/",
            "origin": "pre-commit",
            "pass_filenames": False,
            "files": ["^scripts/"],
        },
        {"run": "ruff check", "origin": "pre-commit", "pass_filenames": True, "files": ["("]},
    )

    every, chosen, own, broken = report["ci"]
    assert every["covers"] == [
        "scripts/s.py",
        "src/pkg/__init__.py",
        "src/pkg/old_x.py",
        "tests/unit/test_a.py",
    ]
    assert chosen["covers"] == ["src/pkg/__init__.py"]
    assert own["covers"] == ["src/pkg/__init__.py", "src/pkg/old_x.py"]
    assert broken["status"] == "unknown"
    assert "patrón" in broken["reason"]


def test_the_tests_a_step_of_the_ci_runs_are_marked(tmp_path: Path) -> None:
    root = _tree(tmp_path, {**PKG, **UNIT, **OTHER})

    report = _ci(
        root,
        {"run": "uv run pytest"},
        {"run": "uv run ruff check ."},
        {"run": "uv run pytest tests/unit"},
        unit="uv run pytest tests/unit -q",
    )

    marks = {t["path"]: t.get("ci_run_by", []) for t in report["tests"]}
    assert marks == {"tests/integration/test_i.py": [0], "tests/unit/test_a.py": [0, 2]}
    # A step of the CI is no control: it runs nothing a run verifies with.
    assert _unrun(report) == ["tests/integration/test_i.py"]
