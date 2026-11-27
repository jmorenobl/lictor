"""Unit tests for Lictor's skill: the stub a project keeps and what the installed version prints."""

from pathlib import Path

import pytest

from lictor.adapters.config.loader import PackagedProfiles
from lictor.application.skill import (
    DETAIL_MARK,
    END_LINE,
    SKILL_FILE,
    ProfilePart,
    ProfileSkill,
    ProjectProfile,
    install_skill_stub,
    parse_profile_skill,
    profile_part,
    profile_summary,
    render_skill,
    skill_stub,
    skill_stub_warning,
)
from lictor.domain.errors import ConfigError

ROOT = Path("/proyecto")
TEXTS = {
    "perfil-a": f"Para proyectos A.\n\nLo de A.\n{DETAIL_MARK}\nMás de A.\n",
    "perfil-b": f"Para proyectos B.\n{DETAIL_MARK}\nMás de B.\n",
}


class FakeCatalog:
    """Profiles in memory, recording which skill files were read."""

    def __init__(
        self, texts: dict[str, str | None] | None = None, detected: str | None = None
    ) -> None:
        self.texts: dict[str, str | None] = dict(TEXTS if texts is None else texts)
        self.found = detected
        self.read: list[str] = []
        self.detections = 0

    def names(self) -> list[str]:
        return sorted(self.texts)

    def skill_text(self, name: str) -> str | None:
        self.read.append(name)
        return self.texts[name]

    def requested(self, name: str) -> str:
        if name not in self.texts:
            raise ConfigError(f"Perfil desconocido: '{name}'")
        return name

    def detected(self, project_root: Path) -> str | None:
        assert project_root == ROOT
        self.detections += 1
        return self.found


def _project(**fields: str) -> ProjectProfile:
    return ProjectProfile(root=ROOT, cwd=ROOT, **fields)


def _render() -> str:
    """What `--show` prints for a project of the packaged profile."""
    return render_skill("0", profile_part(_project(declared="python-uv"), PackagedProfiles()))


def test_the_stub_loads_the_content_of_the_installed_version() -> None:
    stub = skill_stub()

    assert stub.startswith("---\nname: lictor\n")
    assert "description:" in stub.split("---", 2)[1]
    # Claude Code runs the command before the skill reaches the model, and it may not fail.
    assert "\n!`lictor skill --show 2>&1 || true`\n" in stub
    # The Agent Skills standard writes `allowed-tools` as one space-separated string, not a
    # YAML list; the rule ending in ` *` also covers the command with no arguments.
    assert "\nallowed-tools: Bash(lictor skill --show *)\n" in stub
    # What to do when the installed Lictor can't print it.
    assert "0.2.0" in stub
    assert "no sigas" in stub


def test_the_description_says_when_to_load_the_skill_and_nothing_of_running_it() -> None:
    """What to do when it doesn't load is the stub's body, which says it; not a trigger."""
    description = skill_stub().split("---", 2)[1]

    assert "Úsala cuando" in description
    assert "no improvises" not in description
    assert "no improvises" in skill_stub().split("---", 2)[2]


def test_the_content_puts_the_profile_s_essentials_early_and_its_detail_at_the_end() -> None:
    text = render_skill("9.9.9", profile_part(_project(declared="perfil-a"), FakeCatalog()))

    assert "Lictor 9.9.9" in text.splitlines()[0]
    order = [
        "## Límites",
        "## Perfil perfil-a: lo esencial\n\nPara proyectos A.\n\nLo de A.",
        "El detalle de este perfil va al final de la skill.",
        "## 1. Preparar el proyecto",
        "## 4. Diagnosticar",
        "## Perfil perfil-a: el detalle\n\nMás de A.",
    ]
    assert [text.index(part) for part in order] == sorted(text.index(part) for part in order)
    assert "perfil-b" not in text
    assert "<!--" not in text
    assert text.endswith(f"\n\n{END_LINE}\n")


def test_the_body_never_points_to_a_detail_that_may_not_be_there() -> None:
    """Only the profile's own part says its detail is at the end: with a list there is none."""
    text = render_skill("0", profile_part(_project(), FakeCatalog()))

    assert "al final" not in text.split("## 4. Diagnosticar")[0].split("## Límites")[1]


def test_the_header_says_how_to_tell_a_cut_skill_and_get_it_whole() -> None:
    """A compacted conversation keeps only the start of a skill: the end line tells it."""
    header = _render().split("## Límites")[0]

    assert f"«{END_LINE}»" in header
    assert "`lictor skill --show`" in header


def test_the_essentials_stay_well_within_what_a_compaction_keeps() -> None:
    """Claude Code keeps the first 5000 tokens of a skill when it compacts a conversation.

    Measured with the real tokenizer (2026-11-17), this text gives about 2.4 characters a token,
    and the stub and the content up to the end of the essentials were about 1000 tokens. The
    limit leaves room for both to grow: 6000 characters are about 2500 tokens.
    """
    shown = skill_stub().replace("!`lictor skill --show 2>&1 || true`", _render())

    assert shown.index("## 1. Preparar el proyecto") < 6000


def test_every_packaged_profile_has_its_essentials_and_its_detail() -> None:
    """A profile without its file would be told as one the skill knows nothing of."""
    catalog = PackagedProfiles()
    assert catalog.names() == ["generic", "python-uv"]
    for name in catalog.names():
        text = catalog.skill_text(name)
        assert text is not None, name
        parse_profile_skill(name, text)
    python_uv = parse_profile_skill("python-uv", catalog.skill_text("python-uv") or "")
    assert profile_summary(python_uv.essentials).startswith(
        "Para proyectos Python gestionados con `uv`"
    )
    for critical in ("uv add --dev", "capsys", "`min_score`", "nada de `--cov`", "gitleaks"):
        assert critical in python_uv.essentials
    generic = parse_profile_skill("generic", catalog.skill_text("generic") or "")
    for critical in (
        "lea una vez el\n  driver sellado",
        "nunca devuelven un valor",
        "`code_exclude`",
        "`agent_tools`",
        "lictor register",
    ):
        assert critical in generic.essentials, critical


@pytest.mark.parametrize(
    "text",
    [
        "Sin marca.",
        f"Uno.\n{DETAIL_MARK}\nDos.\n{DETAIL_MARK}\nTres.",
        f"\n{DETAIL_MARK}\nSolo el detalle.",
        f"Solo lo esencial.\n{DETAIL_MARK}\n",
    ],
)
def test_a_profile_skill_needs_one_mark_between_two_parts(text: str) -> None:
    with pytest.raises(ConfigError, match="perfil 'x'"):
        parse_profile_skill("x", text)


def test_a_profile_skill_splits_at_its_mark() -> None:
    parsed = parse_profile_skill("x", f"Para X.\n\n{DETAIL_MARK}\n\nMás.\n")

    assert parsed == ProfileSkill(name="x", essentials="Para X.", detail="Más.")


def test_the_profile_lictor_json_names_wins_and_nothing_else_is_read() -> None:
    catalog = FakeCatalog(detected="perfil-a")

    part = profile_part(_project(declared="perfil-b"), catalog)

    assert part == ProfilePart(
        essentials=(
            "## Perfil perfil-b: lo esencial\n\nPara proyectos B.\n\n"
            "El detalle de este perfil va al final de la skill."
        ),
        detail="## Perfil perfil-b: el detalle\n\nMás de B.",
    )
    assert catalog.read == ["perfil-b"]
    assert catalog.detections == 0


def test_a_broken_skill_of_another_profile_doesn_t_break_this_one() -> None:
    catalog = FakeCatalog({**TEXTS, "perfil-c": "Sin marca."})

    part = profile_part(_project(declared="perfil-a"), catalog)

    assert part.detail == "## Perfil perfil-a: el detalle\n\nMás de A."


def test_a_profile_without_its_skill_file_is_still_the_project_s() -> None:
    part = profile_part(_project(declared="perfil-a"), FakeCatalog({"perfil-a": None}))

    assert part == ProfilePart(
        essentials=(
            "## Perfil perfil-a\n\nEl perfil perfil-a no trae una parte propia en esta skill: "
            "sigue solo lo común."
        )
    )


def test_the_profile_asked_for_wins_over_the_project_s() -> None:
    catalog = FakeCatalog()

    part = profile_part(_project(declared="perfil-b"), catalog, requested="perfil-a")

    assert part.essentials.startswith("## Perfil perfil-a: lo esencial")
    assert catalog.detections == 0


def test_a_profile_asked_for_that_the_catalog_refuses_is_refused() -> None:
    with pytest.raises(ConfigError, match="'cobol'"):
        profile_part(_project(), FakeCatalog(), requested="cobol")


@pytest.mark.parametrize("requested", ["", "  "])
def test_an_empty_profile_asked_for_names_the_ones_there_are(requested: str) -> None:
    with pytest.raises(ConfigError, match="--profile necesita el nombre de un perfil: perfil-a"):
        profile_part(_project(), FakeCatalog(), requested=requested)


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({}, "Aún no hay `lictor.json`"),
        (
            {"declared": "cobol"},
            "`lictor.json` pide el perfil «cobol», que esta versión no tiene",
        ),
        (
            {"unreadable": "`lictor.json` no se pudo leer (JSON roto)"},
            "`lictor.json` no se pudo leer (JSON roto) (`lictor doctor` lo detalla)",
        ),
    ],
)
def test_without_a_profile_from_lictor_json_the_detected_one_says_why(
    fields: dict[str, str], why: str
) -> None:
    part = profile_part(_project(**fields), FakeCatalog(detected="perfil-a"))

    assert part.essentials.startswith(
        f"## Perfil perfil-a: lo esencial\n\n{why}: este es el perfil que `lictor init` "
        "detecta aquí.\n\nPara proyectos A."
    )
    assert part.detail == "## Perfil perfil-a: el detalle\n\nMás de A."


def test_from_a_subdirectory_the_skill_names_the_project_s_root() -> None:
    project = ProjectProfile(root=ROOT, cwd=ROOT / "src", declared="perfil-a")

    part = profile_part(project, FakeCatalog())

    assert part.essentials.startswith(
        "## Perfil perfil-a: lo esencial\n\nEl proyecto es `/proyecto`: ejecuta las órdenes de "
        "`lictor` desde ese directorio.\n\nPara proyectos A."
    )


def test_with_no_profile_at_all_the_person_chooses_from_the_list() -> None:
    catalog = FakeCatalog({**TEXTS, "perfil-c": "\n\nSin marca, pero con su primera línea."})

    part = profile_part(_project(declared="cobol"), catalog)

    assert part.detail is None
    assert part.essentials.startswith("## Perfil del proyecto\n\n`lictor.json` pide el perfil")
    assert (
        "- `perfil-a`: Para proyectos A.\n- `perfil-b`: Para proyectos B.\n"
        "- `perfil-c`: Sin marca, pero con su primera línea." in part.essentials
    )
    assert "Lo de A." not in part.essentials
    assert "Pregunta a la persona" in part.essentials
    assert "`lictor skill --show --profile <nombre>`" in part.essentials
    text = render_skill("0", part)
    assert "## Perfil perfil-" not in text
    assert text.endswith(f"{END_LINE}\n")


def test_a_profile_listed_without_its_skill_file_says_so() -> None:
    assert profile_summary(None) == "sin descripción en la skill"
    assert profile_summary("\n  \n") == "sin descripción en la skill"


@pytest.mark.parametrize(
    "rule",
    [
        "No escribas tests",
        "No lances `lictor run`",
        "No hagas commits",
        "eso es configuración tuya, no un escenario",
        "`lictor doctor`",
        "`hints`",
    ],
)
def test_the_content_states_the_rules_the_skill_never_breaks(rule: str) -> None:
    assert rule in _render()


def test_the_content_never_sends_the_person_to_edit_driver_hints() -> None:
    text = _render()

    for line in text.splitlines():
        if "driver_hints" in line:
            assert "No propongas" in line or "no " in line.lower()


def test_installing_writes_the_stub_and_restores_an_edited_one(tmp_path: Path) -> None:
    path = install_skill_stub(tmp_path)

    assert path == tmp_path / SKILL_FILE
    assert path.read_text(encoding="utf-8") == skill_stub()

    path.write_text("editado\n", encoding="utf-8")
    install_skill_stub(tmp_path)
    assert path.read_text(encoding="utf-8") == skill_stub()


def test_no_stub_is_a_decision_and_raises_no_warning(tmp_path: Path) -> None:
    assert skill_stub_warning(tmp_path) is None


def test_the_installed_stub_raises_no_warning(tmp_path: Path) -> None:
    install_skill_stub(tmp_path)

    assert skill_stub_warning(tmp_path) is None


def test_a_stub_that_is_not_this_version_s_says_how_to_restore_it(tmp_path: Path) -> None:
    path = install_skill_stub(tmp_path)
    path.write_text(skill_stub().replace("lictor skill --show", "lictor ayuda"), encoding="utf-8")

    warning = skill_stub_warning(tmp_path)

    assert warning is not None
    assert ".claude/skills/lictor/SKILL.md" in warning
    assert "ejecuta `lictor skill`" in warning


def _section(text: str, title: str) -> str:
    return text.split(f"## {title}")[1].split("\n## ", maxsplit=1)[0]


def test_every_decision_gets_a_scenario_or_is_left_to_the_agent_out_loud() -> None:
    """F34: a decision approved in the conversation and written nowhere doesn't exist for Lictor."""
    drafting = _section(_render(), "2. Redactar los escenarios con la persona")
    check = drafting.index("Antes de escribir `features/`")

    assert "cada decisión aprobada" in drafting[check:]
    assert "a criterio del agente" in drafting[check:]
    assert check < drafting.index("Después de escribir")


@pytest.mark.parametrize(
    "gap",
    [
        "prefijos o formatos que existen pero no se aceptan",
        "espacios alrededor",
        "el formato completo del mensaje de error",
        "`usage:`",
    ],
)
def test_a_cli_that_validates_input_is_asked_about_its_quieter_gaps(gap: str) -> None:
    """F35: what nobody asked, the agent decided."""
    assert gap in _section(_render(), "2. Redactar los escenarios con la persona")


def test_configuring_offers_a_minimum_coverage_that_the_agent_sees() -> None:
    """F36: measuring without a threshold doesn't change what the agent does."""
    configuring = _section(_render(), "3. Configurar")

    assert '`"mode": "block"` y `"min_score": 80`' in configuring
    assert "el agente no ve" in configuring


def test_the_profile_says_the_coverage_is_a_score_in_the_report() -> None:
    section = PackagedProfiles().skill_text("python-uv") or ""

    assert "Puntuaciones" in section
    assert "su porcentaje queda en la salida del control" not in section


def test_the_own_worktree_takes_one_line_and_no_rule_about_commits() -> None:
    text = _render()

    assert "nadie hace commits" not in text
    [line] = [line for line in text.splitlines() if "propio worktree" in line]
    assert "puede seguir haciendo commits" in line
