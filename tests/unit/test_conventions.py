"""The project's rules of code (F79): which files are rule files, what `doctor` says of them,
what the prompts and `report.md` get, and how `lictor.json` keeps them."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lictor.adapters.config.loader import (
    config_fingerprint,
    load_config,
    resolve_defences,
    rule_files,
    write_reviewed_conventions,
)
from lictor.adapters.vcs.git_cli import GitCli
from lictor.application.conventions import rule_files_at, rule_files_warnings
from lictor.application.ports import Executor, Vcs
from lictor.application.usecases.preconditions import CheckPreconditions, PreconditionsRequest
from lictor.bootstrap import project_conventions, record_reviewed_conventions
from lictor.domain.conventions import (
    CONVENTIONS_CHECK,
    MAX_CONVENTIONS_CHARS,
    FoundRuleFile,
    RuleFile,
    TreeEntry,
    by_content,
    conventions_block,
    conventions_report_lines,
    conventions_warning,
    describe_conventions,
    describe_recorded,
    is_rule_file,
    link_target,
    reviewed_record,
    rule_file_name,
    rule_file_state,
    unreviewed,
)
from lictor.domain.errors import ConfigError
from lictor.domain.model import Health

GIT_BIN = shutil.which("git") or "git"
NAMES = ["AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", ".github/copilot-instructions.md"]
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- Which files are rule files --------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("AGENTS.md", True),
        ("docs/sub/CLAUDE.md", True),
        ("claude.md", True),
        (".claude/CLAUDE.md", True),
        (".github/copilot-instructions.md", True),
        ("x/.github/copilot-instructions.md", True),
        ("copilot-instructions.md", False),
        ("other.github/copilot-instructions.md", False),
        ("AGENTS.md.bak", False),
        ("MYAGENTS.md", False),
        ("AGENTS", False),
        ("README.md", False),
    ],
)
def test_a_rule_file_is_named_by_the_profile_at_any_depth(path: str, expected: bool) -> None:
    assert is_rule_file(path, NAMES) is expected


def test_without_names_nothing_is_a_rule_file() -> None:
    assert not is_rule_file("AGENTS.md", [])


@pytest.mark.parametrize(
    ("link", "target", "expected"),
    [
        ("CLAUDE.md", "AGENTS.md", "AGENTS.md"),
        ("docs/CLAUDE.md", "../AGENTS.md", "AGENTS.md"),
        ("docs/CLAUDE.md", "./a/../AGENTS.md", "docs/AGENTS.md"),
        ("CLAUDE.md", "../outside.md", None),
        ("CLAUDE.md", "..", None),
        ("CLAUDE.md", ".", None),
        ("CLAUDE.md", "/etc/passwd", None),
        ("CLAUDE.md", "", None),
    ],
)
def test_a_link_is_followed_only_inside_the_repository(
    link: str, target: str, expected: str | None
) -> None:
    assert link_target(link, target) == expected


def test_tree_entries_tell_files_and_links_apart() -> None:
    assert TreeEntry("a", "100644").is_file
    assert TreeEntry("a", "100755").is_file
    assert not TreeEntry("a", "120000").is_file
    assert TreeEntry("a", "120000").is_link
    assert not TreeEntry("a", "160000").is_link
    assert not TreeEntry("a", "160000").is_file


# --- A content counts once -------------------------------------------------------------------


def test_a_link_and_its_target_are_one_file_named_by_the_target() -> None:
    # In pyverifactu `CLAUDE.md` is a link to `AGENTS.md`.
    files = by_content(
        [
            FoundRuleFile("CLAUDE.md", "AGENTS.md", DIGEST_A),
            FoundRuleFile("AGENTS.md", "AGENTS.md", DIGEST_A),
            FoundRuleFile("CONTRIBUTING.md", "CONTRIBUTING.md", DIGEST_B),
        ]
    )
    assert files == (
        RuleFile("AGENTS.md", DIGEST_A, ("CLAUDE.md",)),
        RuleFile("CONTRIBUTING.md", DIGEST_B),
    )


def test_two_copies_of_one_content_are_one_file_named_by_the_first() -> None:
    files = by_content(
        [
            FoundRuleFile("b/AGENTS.md", "b/AGENTS.md", DIGEST_A),
            FoundRuleFile("a/AGENTS.md", "a/AGENTS.md", DIGEST_A),
        ]
    )
    assert files == (RuleFile("a/AGENTS.md", DIGEST_A, ("b/AGENTS.md",)),)


def test_a_link_to_a_file_that_is_no_rule_file_is_named_by_the_link() -> None:
    files = by_content([FoundRuleFile("CLAUDE.md", "docs/agents.txt", DIGEST_A)])
    assert files == (RuleFile("CLAUDE.md", DIGEST_A),)


def test_rule_files_come_sorted_by_path() -> None:
    files = by_content(
        [FoundRuleFile("z.md", "z.md", DIGEST_A), FoundRuleFile("a.md", "a.md", DIGEST_B)]
    )
    assert [f.path for f in files] == ["a.md", "z.md"]


# --- What was reviewed -----------------------------------------------------------------------


def test_a_content_is_reviewed_under_any_of_its_paths() -> None:
    file = RuleFile("AGENTS.md", DIGEST_A, ("CLAUDE.md",))
    assert unreviewed([file], {"CLAUDE.md": DIGEST_A}) == ()
    assert unreviewed([file], {"AGENTS.md": DIGEST_A}) == ()
    assert unreviewed([file], {"AGENTS.md": DIGEST_B}) == (file,)
    assert unreviewed([file], {}) == (file,)


def test_the_state_tells_a_changed_file_from_one_never_reviewed() -> None:
    file = RuleFile("AGENTS.md", DIGEST_A, ("CLAUDE.md",))
    assert rule_file_state(file, {"AGENTS.md": DIGEST_A}) == "revisado"
    assert rule_file_state(file, {"CLAUDE.md": DIGEST_B}) == "cambió desde que se revisó"
    assert rule_file_state(file, {"OTHER.md": DIGEST_A}) == "sin revisar"


def test_what_reviewed_writes_is_each_contents_hash_under_its_path() -> None:
    files = [RuleFile("AGENTS.md", DIGEST_A, ("CLAUDE.md",)), RuleFile("C.md", DIGEST_B)]
    assert reviewed_record(files) == {"AGENTS.md": DIGEST_A, "C.md": DIGEST_B}


def test_a_name_says_the_paths_with_the_same_content() -> None:
    assert rule_file_name(RuleFile("AGENTS.md", DIGEST_A)) == "`AGENTS.md`"
    assert rule_file_name(RuleFile("AGENTS.md", DIGEST_A, ("CLAUDE.md", "x/CLAUDE.md"))) == (
        "`AGENTS.md` (y `CLAUDE.md` y `x/CLAUDE.md`, con el mismo contenido)"
    )


def test_doctor_warns_of_each_file_not_reviewed_and_says_what_to_do() -> None:
    files = [RuleFile("AGENTS.md", DIGEST_A), RuleFile("C.md", DIGEST_B)]
    warning = conventions_warning(files, {"AGENTS.md": DIGEST_B, "C.md": DIGEST_B})
    assert warning is not None
    assert warning.startswith("`AGENTS.md`: cambió desde que se revisó. ")
    assert "`C.md`" not in warning
    assert "lictor conventions --reviewed" in warning
    assert "solo las reglas de código" in warning
    assert conventions_warning(files, reviewed_record(files)) is None


# --- The prompts -----------------------------------------------------------------------------


def test_without_rules_no_prompt_gets_a_block() -> None:
    assert conventions_block("", "implement") == ""
    assert conventions_block(" \n ", "driver") == ""


def test_the_block_states_the_whole_order_of_precedence() -> None:
    # Fable #11: Lictor's rules, the scenario's literal values, the task, the project, the style.
    block = conventions_block("  Inglés.\n", "refactor")
    assert block == (
        "Reglas del proyecto:\nInglés.\n\n"
        "Si dos indicaciones chocan, manda las reglas y rutas de Lictor de este mensaje, después "
        "los valores literales del escenario, después el contexto del proyecto, que es la "
        "indicación de esta tarea, después estas reglas del proyecto, después los objetivos de "
        "calidad. Si una indicación de esta tarea contradice una regla del proyecto, sigue la "
        "indicación.\n\n"
    )


def test_only_the_driver_hears_of_the_profiles_instructions_and_of_names() -> None:
    driver = conventions_block("Inglés.", "driver")
    implement = conventions_block("Inglés.", "implement")
    assert "de este mensaje y las instrucciones del perfil, después" in driver
    assert "no el idioma del Gherkin, salvo un valor literal que fije el escenario.\n\n" in driver
    assert "instrucciones del perfil" not in implement
    assert "Gherkin" not in implement
    assert "objetivos de calidad" not in implement
    assert implement.endswith("sigue la indicación.\n\n")


# --- report.md -------------------------------------------------------------------------------


def test_the_report_says_the_rules_once_and_that_nothing_checks_them() -> None:
    lines = conventions_report_lines({"text": "Inglés.\n\nSin clases.", "sha256": "x"})
    assert lines == [
        "",
        "## Reglas del proyecto",
        "*Lictor las da a los agentes y no comprueba que se sigan.*",
        "",
        "> Inglés.",
        ">",
        "> Sin clases.",
    ]


@pytest.mark.parametrize("recorded", [None, {}, {"text": ""}, {"text": 3}, "texto", {"text": " "}])
def test_the_report_has_no_section_without_rules(recorded: object) -> None:
    assert conventions_report_lines(recorded) == []


# --- What `lictor conventions` prints --------------------------------------------------------


def test_conventions_prints_the_rules_and_each_files_state() -> None:
    files = [RuleFile("AGENTS.md", DIGEST_A), RuleFile("C.md", DIGEST_B)]
    text = describe_conventions("Inglés.", files, {"AGENTS.md": DIGEST_A})
    assert text.splitlines()[:6] == [
        "Reglas del proyecto (conventions.text):",
        "Inglés.",
        "",
        "Ficheros de reglas en HEAD:",
        "- `AGENTS.md`: revisado",
        "- `C.md`: sin revisar",
    ]
    assert "lictor conventions --reviewed" in text
    assert "lictor conventions --reviewed" not in describe_conventions(
        "", files, reviewed_record(files)
    )


def test_conventions_says_when_there_are_no_rules_nor_files() -> None:
    assert describe_conventions(" ", [], {}) == (
        "Reglas del proyecto (conventions.text):\n(ninguna: conventions.text está vacío)\n\n"
        "Ningún fichero de reglas en HEAD.\n"
    )


def test_reviewed_says_what_it_wrote() -> None:
    assert describe_recorded([]) == (
        "Ningún fichero de reglas en HEAD: conventions.reviewed queda vacío.\n"
    )
    assert describe_recorded([RuleFile("A.md", DIGEST_A), RuleFile("B.md", DIGEST_B)]) == (
        "Revisados en lictor.json, como están en HEAD: `A.md` y `B.md`.\n"
    )


# --- Reading them from a commit --------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run([GIT_BIN, *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Ana")
    _git(root, "config", "user.email", "ana@example.com")
    (root / "AGENTS.md").write_text("Reglas.\r\n", encoding="utf-8")
    os.symlink("AGENTS.md", root / "CLAUDE.md")
    (root / "docs").mkdir()
    (root / "docs" / "CONTRIBUTING.md").write_text("Contribuir.\n", encoding="utf-8")
    os.symlink("../../fuera.md", root / "docs" / "AGENTS.md")
    os.symlink("CLAUDE.md", root / "docs" / "CLAUDE.md")
    (root / ".gitignore").write_text("ignorado/\n", encoding="utf-8")
    (root / "ignorado").mkdir()
    (root / "ignorado" / "AGENTS.md").write_text("No cuenta.\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "inicio")
    # What isn't committed doesn't count: the rules are read from HEAD.
    (root / "AGENTS.md").write_text("Sin confirmar.\n", encoding="utf-8")
    return root


def test_rule_files_are_read_from_head_following_links_inside_the_repository(repo: Path) -> None:
    files = rule_files_at(GitCli(repo), "HEAD", NAMES)

    # A link to itself and a link out of the repository read no file; CRLF is hashed as kept.
    assert files == (
        RuleFile("AGENTS.md", hashlib.sha256(b"Reglas.\r\n").hexdigest(), ("CLAUDE.md",)),
        RuleFile("docs/CONTRIBUTING.md", _sha("Contribuir.\n")),
    )


def test_without_names_nothing_is_read() -> None:
    vcs = MagicMock(spec=Vcs)
    assert rule_files_at(vcs, "HEAD", []) == ()
    vcs.tree_entries.assert_not_called()


def test_a_chain_of_links_too_long_reads_nothing() -> None:
    vcs = MagicMock(spec=Vcs)
    vcs.tree_entries.return_value = [TreeEntry("AGENTS.md", "120000"), TreeEntry("x", "120000")]
    vcs.read_bytes.side_effect = lambda _rev, path: b"x" if path == "AGENTS.md" else b"AGENTS.md"
    assert rule_files_at(vcs, "HEAD", NAMES) == ()


def test_a_link_to_a_submodule_reads_nothing() -> None:
    vcs = MagicMock(spec=Vcs)
    vcs.tree_entries.return_value = [TreeEntry("AGENTS.md", "120000"), TreeEntry("sub", "160000")]
    vcs.read_bytes.return_value = b"sub"
    assert rule_files_at(vcs, "HEAD", NAMES) == ()


def test_doctors_warnings_are_none_without_rule_files_and_empty_once_reviewed(repo: Path) -> None:
    vcs = GitCli(repo)
    assert rule_files_warnings(vcs, ["NADA.md"], {}) is None
    files = rule_files_at(vcs, "HEAD", NAMES)
    assert rule_files_warnings(vcs, NAMES, reviewed_record(files)) == ()
    warnings = rule_files_warnings(vcs, NAMES, {})
    assert warnings is not None
    assert "`AGENTS.md` (y `CLAUDE.md`, con el mismo contenido): sin revisar" in warnings[0]


def _preconditions(vcs: Vcs, root: Path, reviewed: dict[str, str]) -> CheckPreconditions:
    executor = MagicMock(spec=Executor)
    executor.health.return_value = Health(ok=True, cli_version="2", auth_status="subscription")
    return CheckPreconditions(vcs, executor, None, root, rule_files=NAMES, reviewed=reviewed)


def test_doctor_warns_without_changing_the_exit_code(repo: Path) -> None:
    report = _preconditions(GitCli(repo), repo, {}).execute(
        PreconditionsRequest(run_id="r", clean_tree=False, conventions=True)
    )
    rules = next(check for check in report.checks if check.name == CONVENTIONS_CHECK)
    assert rules.problems == ()
    assert len(rules.warnings) == 1


def test_doctor_marks_reviewed_rule_files_as_passed(repo: Path) -> None:
    reviewed = reviewed_record(rule_files_at(GitCli(repo), "HEAD", NAMES))
    report = _preconditions(GitCli(repo), repo, reviewed).execute(
        PreconditionsRequest(run_id="r", clean_tree=False, conventions=True)
    )
    rules = next(check for check in report.checks if check.name == CONVENTIONS_CHECK)
    assert rules.warnings == ()


def test_only_doctor_asks_for_the_rule_files(repo: Path) -> None:
    report = _preconditions(GitCli(repo), repo, {}).execute(
        PreconditionsRequest(run_id="r", clean_tree=False)
    )
    assert CONVENTIONS_CHECK not in [check.name for check in report.checks]


def test_without_commits_the_rule_files_are_not_looked_at(tmp_path: Path) -> None:
    vcs = MagicMock(spec=Vcs)
    vcs.has_commits.return_value = False
    report = _preconditions(vcs, tmp_path, {}).execute(
        PreconditionsRequest(run_id="r", clean_tree=False, conventions=True)
    )
    assert CONVENTIONS_CHECK not in [check.name for check in report.checks]
    vcs.tree_entries.assert_not_called()


# --- lictor.json -----------------------------------------------------------------------------


def _project(root: Path, extra: dict[str, object] | None = None) -> Path:
    payload = {"version": "0.1", "profile": "python-uv@0.2", "hints": "H", **(extra or {})}
    (root / "lictor.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return root


def test_the_rules_load_and_default_to_none(tmp_path: Path) -> None:
    assert load_config(_project(tmp_path)).conventions.text == ""
    loaded = load_config(_project(tmp_path, {"conventions": {"text": "Inglés."}}))
    assert loaded.conventions.text == "Inglés."
    assert loaded.conventions.reviewed == {}


def test_rules_longer_than_the_maximum_are_refused_at_load(tmp_path: Path) -> None:
    long_text = "x" * (MAX_CONVENTIONS_CHARS + 1)
    with pytest.raises(ConfigError, match=f"tiene {MAX_CONVENTIONS_CHARS + 1} caracteres"):
        load_config(_project(tmp_path, {"conventions": {"text": long_text}}))
    fits = "x" * MAX_CONVENTIONS_CHARS
    assert load_config(_project(tmp_path, {"conventions": {"text": fits}})).conventions.text


def test_a_reviewed_hash_written_by_hand_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no es un sha256"):
        load_config(_project(tmp_path, {"conventions": {"reviewed": {"AGENTS.md": "hoy"}}}))


def test_the_rules_enter_the_fingerprint_and_what_was_reviewed_does_not(tmp_path: Path) -> None:
    def fingerprint(conventions: dict[str, object]) -> str:
        config = load_config(_project(tmp_path, {"conventions": conventions}))
        return config_fingerprint(config, resolve_defences(config))

    base = fingerprint({"text": "Inglés."})
    assert fingerprint({"text": "Inglés.", "reviewed": {"AGENTS.md": DIGEST_A}}) == base
    assert fingerprint({"text": "Español."}) != base


def test_the_rule_files_are_the_profiles(tmp_path: Path) -> None:
    names = rule_files(load_config(_project(tmp_path)))
    assert "AGENTS.md" in names
    assert ".github/copilot-instructions.md" in names


def test_reviewed_rewrites_only_its_key(tmp_path: Path) -> None:
    _project(tmp_path, {"conventions": {"text": "Inglés.", "reviewed": {"VIEJO.md": DIGEST_B}}})
    before = json.loads((tmp_path / "lictor.json").read_text(encoding="utf-8"))

    write_reviewed_conventions(tmp_path, {"Z.md": DIGEST_B, "A.md": DIGEST_A})

    raw = (tmp_path / "lictor.json").read_text(encoding="utf-8")
    after = json.loads(raw)
    assert after == {
        **before,
        "conventions": {"text": "Inglés.", "reviewed": {"A.md": DIGEST_A, "Z.md": DIGEST_B}},
    }
    assert list(after) == list(before)
    assert raw == json.dumps(after, indent=2, ensure_ascii=False) + "\n"
    assert not list(tmp_path.glob(".lictor.json*"))


def test_reviewed_adds_the_key_where_there_was_none(tmp_path: Path) -> None:
    _project(tmp_path)
    write_reviewed_conventions(tmp_path, {})
    after = json.loads((tmp_path / "lictor.json").read_text(encoding="utf-8"))
    assert after["conventions"] == {"reviewed": {}}
    assert after["hints"] == "H"


def test_reviewed_doesnt_overwrite_a_file_that_doesnt_load(tmp_path: Path) -> None:
    (tmp_path / "lictor.json").write_text('{"profile": "python-uv@0.2", "x": 1}\n', "utf-8")
    with pytest.raises(ConfigError):
        write_reviewed_conventions(tmp_path, {})
    assert (tmp_path / "lictor.json").read_text("utf-8") == '{"profile": "python-uv@0.2", "x": 1}\n'


def test_conventions_and_reviewed_end_to_end(repo: Path) -> None:
    _project(repo)
    assert "- `AGENTS.md` (y `CLAUDE.md`, con el mismo contenido): sin revisar" in (
        project_conventions(repo)
    )

    said = record_reviewed_conventions(repo)

    assert said.startswith("Revisados en lictor.json, como están en HEAD: `AGENTS.md`")
    status = project_conventions(repo)
    assert "- `AGENTS.md` (y `CLAUDE.md`, con el mismo contenido): revisado" in status
    assert "- `docs/CONTRIBUTING.md`: revisado" in status


def test_conventions_needs_a_commit(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-b", "main")
    _project(tmp_path)
    with pytest.raises(ConfigError, match="se leen de HEAD"):
        project_conventions(tmp_path)
