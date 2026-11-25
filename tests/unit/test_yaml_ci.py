"""The commands of a project's CI, read with a YAML parser from its files (ADR-0015, F76).

The fixtures in `tests/fixtures/ci/` are the workflows and pre-commit configurations of the four
projects measured for 0.19.0, and `hard/w.yml` the review's valid YAML that a reading by lines
gets wrong in silence (`revision-fable.md`, #17).
"""

import shutil
from pathlib import Path

import pytest

from lictor.adapters.ci.yaml_ci import YamlCiReader, split_script
from lictor.domain.ci import CiStep

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ci"
HOOKS = {"ruff": "ruff check", "ruff-check": "ruff check", "ruff-format": "ruff format"}
HOOKS |= {"mypy": "mypy"}
WORKFLOWS = Path(".github") / "workflows"


def _project(root: Path, name: str) -> Path:
    source = FIXTURES / name
    if (source / "pre-commit-config.yaml").exists():
        shutil.copy(source / "pre-commit-config.yaml", root / ".pre-commit-config.yaml")
    workflows = root / WORKFLOWS
    workflows.mkdir(parents=True)
    for workflow in sorted(source.glob("*.yml")) + sorted((source / "workflows").glob("*.yml")):
        shutil.copy(workflow, workflows / workflow.name)
    return root


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return root


def _runs(root: Path, file: str | None = None) -> list[tuple[str, str]]:
    steps = YamlCiReader(HOOKS).read(root).steps
    return [(s.place(), s.run) for s in steps if file is None or s.file == file]


def test_pyverifactu_checks_what_its_workflow_and_its_remote_hooks_say(tmp_path: Path) -> None:
    root = _project(tmp_path, "pyverifactu")

    steps = YamlCiReader(HOOKS).read(root).steps

    test = [(s.place(), s.run) for s in steps if s.file.endswith("test.yml")]
    assert test[-4:] == [
        (
            ".github/workflows/test.yml:25",
            'uv run pytest -m "not integration" --cov=verifactu_py --cov-report=xml '
            "--cov-report=term",
        ),
        (".github/workflows/test.yml:27", "uv run mypy src/"),
        (".github/workflows/test.yml:29", "uv run ruff check src/"),
        (".github/workflows/test.yml:31", "uv run ruff format --check src/"),
    ]
    hooks = [s for s in steps if s.origin == "pre-commit"]
    # A remote hook runs the command the profile knows for its id, with its `args`; the hooks
    # nobody knows (`trailing-whitespace`...) check nothing a control compares with.
    assert hooks == [
        CiStep(".pre-commit-config.yaml", 11, "ruff check --fix", "pre-commit", True),
        CiStep(".pre-commit-config.yaml", 16, "ruff format", "pre-commit", True),
        CiStep(
            ".pre-commit-config.yaml",
            25,
            "mypy --config-file=pyproject.toml src/",
            "pre-commit",
            pass_filenames=False,
            files=("^src/verifactu_py/",),
            exclude=("^(ref/|build/|dist/)",),
        ),
    ]


def test_bookwright_and_the_local_hooks_run_their_entry(tmp_path: Path) -> None:
    root = _project(tmp_path, "bookwright")

    assert _runs(root, ".pre-commit-config.yaml") == [
        (".pre-commit-config.yaml:8", "uv run ruff format"),
        (".pre-commit-config.yaml:13", "uv run ruff check --fix --exit-non-zero-on-fix"),
    ]
    tests = _runs(root, ".github/workflows/tests.yml")
    assert (".github/workflows/tests.yml:27", "uv run ruff check .") in tests
    assert (".github/workflows/tests.yml:35", "uv run pytest") in tests
    # A pipe is two commands, each on the step's line.
    assert (".github/workflows/tests.yml:44", "grep -q 'bookwright/resources/'") in tests


def test_a_script_of_several_lines_gives_each_command_its_line(tmp_path: Path) -> None:
    root = _project(tmp_path, "bookwright")

    release = dict(_runs(root, ".github/workflows/release.yml"))

    assert release[".github/workflows/release.yml:40"] == "exit 1"
    # A quoted string that spans lines is one command, which starts where it starts.
    assert release[".github/workflows/release.yml:56"].startswith('awk -v ver="$ver" \'')
    assert release[".github/workflows/release.yml:56"].endswith("' CHANGELOG.md")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "agentix",
            [
                (".pre-commit-config.yaml:7", "uv lock --check", False),
                (".pre-commit-config.yaml:13", "uv run ruff check", False),
                (".pre-commit-config.yaml:19", "uv run ruff format --check", False),
                (".pre-commit-config.yaml:25", "uv run mypy", False),
                (".pre-commit-config.yaml:31", "uv run pytest -q -n 4", False),
            ],
        ),
        (
            "local-llm-bench",
            [
                (".pre-commit-config.yaml:18", "uv run ruff check --fix", True),
                (".pre-commit-config.yaml:25", "uv run ruff format", True),
                (".pre-commit-config.yaml:32", "uv run mypy", False),
                (".pre-commit-config.yaml:40", "uv run pytest -q", False),
            ],
        ),
    ],
)
def test_a_project_with_only_pre_commit_still_has_a_ci(
    tmp_path: Path, name: str, expected: list[tuple[str, str, bool]]
) -> None:
    root = _project(tmp_path, name)

    steps = YamlCiReader(HOOKS).read(root).steps

    assert [(s.place(), s.run, s.pass_filenames) for s in steps] == expected


def test_valid_yaml_that_a_reading_by_lines_gets_wrong(tmp_path: Path) -> None:
    root = _project(tmp_path, "hard")

    assert _runs(root) == [
        (".github/workflows/w.yml:9", "uv run ruff check src"),
        # A folded block and a plain scalar on two lines are one command.
        (".github/workflows/w.yml:10", "uv run pytest tests/unit -q"),
        (".github/workflows/w.yml:13", "uv run pytest tests/integration"),
        # In a quoted scalar the shell still sees a comment.
        (".github/workflows/w.yml:16", "uv run mypy --strict src"),
        # A step written as a flow mapping.
        (".github/workflows/w.yml:17", "uv run ruff format --check src"),
        (".github/workflows/w.yml:18", "uv run ${{ matrix.cmd }}"),
        # The step that may fail demands nothing; the quoted key is a key; a `run:` inside another
        # step's script is text.
        (".github/workflows/w.yml:24", "uv run pytest tests/e2e"),
    ]


def test_a_hook_only_run_by_hand_or_a_job_that_may_fail_demands_nothing(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        ".pre-commit-config.yaml",
        "default_stages: [pre-commit]\nfiles: ^src/\nrepos:\n  - repo: local\n    hooks:\n"
        "      - id: slow\n        entry: uv run pytest tests/slow\n        stages: [manual]\n"
        "      - id: fast\n        entry: uv run pytest\n        args: [tests/unit, -k, 'a b']\n"
        "        exclude: _old\\.py$\n  - repo: meta\n    hooks:\n      - id: identity\n",
    )
    _write(
        root,
        ".github/workflows/ci.yaml",
        "jobs:\n  maybe:\n    continue-on-error: true\n    steps:\n      - run: uv run mypy src\n"
        "  sure:\n    steps:\n      - uses: actions/checkout@v4\n      - run: |\n"
        "          uv sync && \\\n            uv run ruff check src   # lint\n"
        "          uv run pytest 2>&1 > out.txt\n",
    )

    steps = YamlCiReader(HOOKS).read(root).steps

    assert [(s.place(), s.run, s.files, s.exclude) for s in steps] == [
        (".github/workflows/ci.yaml:10", "uv sync", (), ()),
        (".github/workflows/ci.yaml:11", "uv run ruff check src", (), ()),
        (".github/workflows/ci.yaml:12", "uv run pytest", (), ()),
        (
            ".pre-commit-config.yaml:9",
            "uv run pytest tests/unit -k 'a b'",
            ("^src/",),
            ("_old\\.py$",),
        ),
    ]


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("jobs: [unclosed\n", "no es YAML válido (línea 2)"),
        (b"\xff\xfe".decode("latin-1"), "no está en UTF-8"),
    ],
)
def test_a_file_that_cant_be_read_is_named_and_the_rest_are_read(
    tmp_path: Path, text: str, why: str
) -> None:
    root = _write(tmp_path, ".github/workflows/bad.yml", "")
    (root / WORKFLOWS / "bad.yml").write_bytes(text.encode("latin-1"))
    _write(
        root, ".github/workflows/good.yml", "jobs:\n  a:\n    steps:\n      - run: uv run mypy\n"
    )

    read = YamlCiReader(HOOKS).read(root)

    assert read.unreadable == (f".github/workflows/bad.yml: {why}",)
    assert [s.run for s in read.steps] == ["uv run mypy"]


def test_a_tree_without_ci_has_no_steps(tmp_path: Path) -> None:
    _write(tmp_path, ".github/workflows/notes.txt", "run: uv run pytest\n")
    _write(tmp_path, ".github/workflows/empty.yml", "")
    _write(tmp_path, ".github/workflows/odd.yml", "- just\n- a list\n")

    read = YamlCiReader(HOOKS).read(tmp_path)

    assert read.steps == ()
    assert read.unreadable == ()


@pytest.mark.parametrize(
    ("script", "commands"),
    [
        ("a && b || c; d | e & f", ["a", "b", "c", "d", "e", "f"]),
        ("echo 'x; y' \"a && b\"", ["echo 'x; y' \"a && b\""]),
        ('echo "a \\" ; b" ; c', ['echo "a \\" ; b"', "c"]),
        ("cmd 2>&1 >> log.txt | tee x", ["cmd", "tee x"]),
        ("cmd2>x", ["cmd2"]),
        ("a \\\n  b\nc", ["a b", "c"]),
        ("# only a comment\n\n  ", []),
        ("x#y # comment", ["x#y"]),
    ],
)
def test_a_script_splits_into_its_commands(script: str, commands: list[str]) -> None:
    assert [text for _line, text in split_script(script)] == commands


def test_each_command_keeps_the_line_it_starts_on() -> None:
    # The text is the command's words: a quoted line break reads as a space.
    assert split_script("a \\\n b\n\nc 'x\ny'\nd") == [(0, "a b"), (3, "c 'x y'"), (5, "d")]
