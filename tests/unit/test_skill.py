"""Unit tests for Lictor's skill: the stub a project keeps and what the installed version prints."""

from pathlib import Path

import pytest

from lictor.adapters.config.loader import profile_skill_sections
from lictor.application.skill import (
    SKILL_FILE,
    install_skill_stub,
    render_skill,
    skill_stub,
    skill_stub_warning,
)


def test_the_stub_loads_the_content_of_the_installed_version() -> None:
    stub = skill_stub()

    assert stub.startswith("---\nname: lictor\n")
    assert "description:" in stub.split("---", 2)[1]
    # Claude Code runs the command before the skill reaches the model, and it may not fail.
    assert "\n!`lictor skill --show 2>&1 || true`\n" in stub
    assert "allowed-tools:\n  - Bash(lictor skill --show)\n" in stub
    # What to do when the installed Lictor can't print it.
    assert "0.2.0" in stub
    assert "no sigas" in stub


def test_the_content_carries_the_version_and_each_profile_section() -> None:
    text = render_skill("9.9.9", [("perfil-a", "Texto de A.\n"), ("perfil-b", "Texto de B.")])

    assert "Lictor 9.9.9" in text.splitlines()[0]
    assert "## Perfil perfil-a\n\nTexto de A." in text
    assert "## Perfil perfil-b\n\nTexto de B." in text
    assert text.index("perfil-a") < text.index("perfil-b")
    assert text.endswith("\n")


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
    assert rule in render_skill("0", [])


def test_the_content_never_sends_the_person_to_edit_driver_hints() -> None:
    text = render_skill("0", [])

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
    drafting = _section(render_skill("0", []), "2. Redactar los escenarios con la persona")
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
    assert gap in _section(render_skill("0", []), "2. Redactar los escenarios con la persona")


def test_configuring_offers_a_minimum_coverage_that_the_agent_sees() -> None:
    """F36: measuring without a threshold doesn't change what the agent does."""
    configuring = _section(render_skill("0", []), "3. Configurar")

    assert '{"id": "coverage", "mode": "block", "min_score": 80}' in configuring
    assert "el agente no ve" in configuring


def test_the_profile_says_the_coverage_is_a_score_in_the_report() -> None:
    [(_, section)] = [s for s in profile_skill_sections() if s[0] == "python-uv"]

    assert "Puntuaciones" in section
    assert "su porcentaje queda en la salida del control" not in section


def test_the_own_worktree_takes_one_line_and_no_rule_about_commits() -> None:
    text = render_skill("0", [])

    assert "nadie hace commits" not in text
    [line] = [line for line in text.splitlines() if "propio worktree" in line]
    assert "puede seguir haciendo commits" in line
