"""The three classes of defences (ADR-0020): what joins the profile's, what only a project whose
profile declares none may write, and what only the profile declares."""

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from lictor.adapters.config.loader import (
    _PROFILE_ONLY_ACCEPTANCE,
    DEFAULT_PROFILES_DIR,
    PackagedProfiles,
    ProfileConfigInitializer,
    config_fingerprint,
    detect_profile,
    load_config,
    resolve_defences,
)
from lictor.adapters.config.models import Profile, domain_controls
from lictor.application.usecases.init_project import InitProjectResult
from lictor.bootstrap import proof_defences
from lictor.cli import describe_init
from lictor.domain.controls import controls_of_stage
from lictor.domain.errors import ConfigError, ExitCode
from lictor.domain.proof_level import READ, proof_level
from lictor.domain.stages import ATTEMPT

BARE = {
    "name": "bare",
    "version": "0.1",
    "status": "alpha",
    "detect": [],
    "controls": [],
    "forbidden_paths": [".claude"],
    "verification": {"env": {"HOME": "{home_dir}"}, "inherit": ["PATH"]},
}
PATHS = {"writable": ["iva/"], "driver": ["acceptance/"], "protected": ["features/", "go.mod"]}
ACCEPTANCE = {"run": "gotestsum --junitfile {out} -- ./acceptance/..."}


@pytest.fixture
def profiles(tmp_path: Path) -> Path:
    directory = tmp_path / "profiles"
    shutil.copytree(DEFAULT_PROFILES_DIR, directory)
    (directory / "bare.json").write_text(json.dumps(BARE), encoding="utf-8")
    return directory


def _write(root: Path, **fields: object) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "lictor.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def _bare(root: Path, **fields: object) -> Path:
    written = {"paths": PATHS, "acceptance": ACCEPTANCE, "code_paths": ["iva/"], **fields}
    return _write(root, profile="bare@0.1", **written)


def _refused(path: Path, profiles: Path | None = None) -> str:
    with pytest.raises(ConfigError) as exc_info:
        load_config(path, profiles)
    assert exc_info.value.exit_code == ExitCode.PRECONDITION
    return str(exc_info.value)


# What joins the profile's.


def test_the_lists_a_project_writes_join_the_profiles_in_order(tmp_path: Path) -> None:
    config = load_config(
        _write(
            tmp_path,
            profile="python-uv@0.2",
            suppressions=["# nolint", "# noqa"],
            banned_patterns=[r"\bt\.Skip\("],
            forbidden_paths=["go.mod", "uv.lock"],
        )
    )
    defences = resolve_defences(config)

    assert defences.suppressions == ("# noqa", "# type: ignore", "# pragma: no cover", "# nolint")
    assert defences.banned_patterns[-1] == r"\bt\.Skip\("
    assert defences.banned_patterns[0] == "#\\s*noqa"
    assert defences.forbidden_paths[-1] == "go.mod"
    assert defences.forbidden_paths.count("uv.lock") == 1


def test_what_a_project_adds_counts_in_the_fingerprint(tmp_path: Path) -> None:
    plain = load_config(_write(tmp_path / "a", profile="python-uv@0.2"))
    more = load_config(_write(tmp_path / "b", profile="python-uv@0.2", suppressions=["# x"]))

    assert config_fingerprint(plain, resolve_defences(plain)) != config_fingerprint(
        more, resolve_defences(more)
    )


def test_a_banned_pattern_of_the_project_must_be_a_regex(tmp_path: Path) -> None:
    message = _refused(_write(tmp_path, profile="python-uv@0.2", banned_patterns=["(("]))

    assert "banned_patterns contiene una expresión inválida '(('" in message


# What only a project whose profile declares none may write.


def test_a_profile_that_builds_its_environment_keeps_it(tmp_path: Path) -> None:
    message = _refused(
        _write(tmp_path, profile="python-uv@0.2", verification={"prepare": "uv sync"})
    )

    assert message.startswith("lictor.json no puede declarar verification.prepare: el perfil")
    assert "python-uv" in message


def test_a_profile_that_says_where_the_code_is_refuses_an_exclusion(tmp_path: Path) -> None:
    message = _refused(_write(tmp_path, profile="python-uv@0.2", code_exclude=["*_test.py"]))

    assert message.startswith("lictor.json no puede declarar code_exclude: el perfil python-uv")
    assert "L0-alcance" in message


def test_without_the_profiles_the_project_writes_them_and_they_are_its_defences(
    tmp_path: Path, profiles: Path
) -> None:
    config = load_config(
        _bare(
            tmp_path,
            acceptance={**ACCEPTANCE, "driver_hints": "Recoge el panic en cada test."},
            verification={"prepare": "sh -c 'npm ci'"},
            code_exclude=["*_test.go"],
            banned_patterns=[r"//\s*nolint"],
        ),
        profiles,
    )
    defences = resolve_defences(config, profiles)

    assert defences.driver_hints == "Recoge el panic en cada test."
    assert defences.verification.prepare == "sh -c 'npm ci'"
    assert defences.code_paths == ("iva/",)
    assert defences.code_exclude == ("*_test.go",)
    assert defences.banned_patterns == (r"//\s*nolint",)
    assert defences.forbidden_paths == (".claude",)


def test_the_profiles_driver_hints_are_the_ones_the_driver_gets(tmp_path: Path) -> None:
    defences = resolve_defences(load_config(_write(tmp_path, profile="python-uv@0.2")))

    assert defences.driver_hints.startswith("Usa pytest-bdd.")


# What a profile that knows no layout leaves to the project.


def test_everything_the_profile_leaves_is_named_at_once(tmp_path: Path, profiles: Path) -> None:
    message = _refused(_write(tmp_path, profile="bare@0.1"), profiles)

    assert message.startswith("El perfil bare no sabe cómo es tu proyecto: escribe en lictor.json")
    assert message.index("`paths`") < message.index("`acceptance`") < message.index("`code_paths`")
    assert message.endswith("La skill de Lictor te ayuda: /lictor en Claude Code.")


def test_only_what_is_missing_is_named(tmp_path: Path, profiles: Path) -> None:
    message = _refused(_write(tmp_path, profile="bare@0.1", paths=PATHS), profiles)

    assert "`paths`" not in message
    assert "`acceptance` (la orden que ejecuta la aceptación y deja un JUnit en {out}) y" in message


def test_code_paths_must_still_reach_what_the_agent_writes(tmp_path: Path, profiles: Path) -> None:
    load = load_config(_bare(tmp_path, code_paths=["lib/"]), profiles)

    with pytest.raises(ConfigError, match="no incluye ninguna ruta de código"):
        resolve_defences(load, profiles)


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"survey": {"run": "x {in} {out}"}}, "survey"),
        ({"refactor_hints": "- Corto."}, "refactor_hints"),
        ({"acceptance": {**ACCEPTANCE, "needs_plugins": {}}}, "needs_plugins"),
        ({"oracle": {}}, "lictor.json no puede declarar oracle: solo lo declara el perfil"),
        ({"driver_exempt_paths": []}, "declarar driver_exempt_paths: solo lo declara el perfil"),
        ({"verification": {"reserved": []}}, "declarar verification.reserved: solo lo declara"),
        (
            {"status": "stable"},
            "declarar status: solo lo declara el perfil, y ningún proyecto lo escribe",
        ),
    ],
)
def test_what_only_a_profile_declares_stays_refused_without_one(
    tmp_path: Path, profiles: Path, fields: dict[str, object], says: str
) -> None:
    assert says in _refused(_bare(tmp_path, **fields), profiles)


# Detecting and initialising.


def test_a_profile_without_markers_is_never_detected(tmp_path: Path, profiles: Path) -> None:
    (profiles / "python-uv.json").unlink()

    with pytest.raises(ConfigError, match="No se pudo detectar"):
        detect_profile(tmp_path, profiles)
    assert PackagedProfiles(profiles).detected(tmp_path) is None


def test_init_without_a_profile_in_an_empty_project_doesnt_choose_one(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No se pudo detectar"):
        ProfileConfigInitializer().generate_config(tmp_path)


def test_init_with_a_bare_profile_writes_four_keys_and_says_what_is_left(
    tmp_path: Path, profiles: Path
) -> None:
    init = ProfileConfigInitializer(profiles).generate_config(tmp_path, "bare")

    assert init.to_write == ("paths", "acceptance", "code_paths")
    assert init.status == "alpha"
    assert sorted(json.loads(init.config_json)) == ["backend", "hints", "profile", "version"]
    assert init.profile_name == "bare@0.1"


def test_impostors_without_red_kinds_are_a_mistake_of_the_profile() -> None:
    impostors = {"pattern": "panic", "replacements": [{"code": "return", "name": "cero"}]}

    with pytest.raises(ValidationError, match=r"oracle\.impostors necesita oracle\.red_kinds"):
        Profile.model_validate({**BARE, "oracle": {"impostors": impostors}})


def test_init_tells_the_person_what_a_bare_profile_leaves_to_them() -> None:
    base = InitProjectResult(Path("lictor.json"), "bare@0.1", gitignore_updated=False)
    told = describe_init(
        InitProjectResult(
            Path("lictor.json"),
            "bare@0.1",
            gitignore_updated=False,
            to_write=("paths", "acceptance", "code_paths"),
            status="alpha",
        )
    )

    assert "Perfil: bare@0.1 (alpha: puede cambiar sin aviso)" in told
    assert "escribe en lictor.json `paths`, `acceptance` y `code_paths`, y en `agent_tools`" in told
    assert "no sabe cómo es tu proyecto" not in describe_init(base)
    assert "alpha" not in describe_init(base)


def test_the_packaged_generic_profile_loads_what_a_go_project_writes(tmp_path: Path) -> None:
    config = load_config(
        _write(
            tmp_path,
            profile="generic@0.2",
            paths=PATHS,
            acceptance=ACCEPTANCE,
            code_paths=["iva/"],
            code_exclude=["*_test.go"],
        )
    )
    defences = resolve_defences(config)
    controls = controls_of_stage(domain_controls(config), ATTEMPT)

    proof = proof_level(proof_defences(defences, controls), ())

    assert proof.level == READ
    assert defences.declared_impostors
    assert defences.impostors is None
    assert len(proof.gaps) == 7
    assert defences.forbidden_paths[0] == ".gitignore"
    assert defences.refactor_hints.startswith("- Funciones cortas")
    assert "src/<paquete>" not in defences.refactor_hints


def test_the_first_generic_profile_is_retired_saying_what_its_stubs_now_carry(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigError) as raised:
        load_config(_write(tmp_path, profile="generic@0.1", paths=PATHS, acceptance=ACCEPTANCE))

    assert "El perfil generic@0.1 ya no existe: usa generic@0.2, que pide que cada stub" in str(
        raised.value
    )
    assert "«lictor-impostor:»" in str(raised.value)


def test_init_with_generic_names_what_the_person_writes(tmp_path: Path) -> None:
    init = ProfileConfigInitializer().generate_config(tmp_path, "generic")

    assert init.to_write == ("paths", "acceptance", "code_paths")
    assert init.status == "alpha"
    assert json.loads(init.config_json)["hints"] == ""


def test_an_empty_list_of_the_project_takes_nothing_from_the_profile(tmp_path: Path) -> None:
    plain = resolve_defences(load_config(_write(tmp_path / "a", profile="python-uv@0.2")))
    emptied = resolve_defences(
        load_config(
            _write(
                tmp_path / "b",
                profile="python-uv@0.2",
                suppressions=[],
                banned_patterns=[],
                forbidden_paths=[],
                code_paths=[],
            )
        )
    )

    assert emptied == plain


def test_every_field_only_a_profile_declares_has_the_common_sentence(tmp_path: Path) -> None:
    for key in ("name", "detect", "rule_files", "hint_values", "retired_versions"):
        message = _refused(_write(tmp_path / key, profile="python-uv@0.2", **{key: []}))
        assert message == (
            f"lictor.json no puede declarar {key}: solo lo declara el perfil, y ningún proyecto "
            "lo escribe. Bórralo."
        ), key


def test_a_field_only_the_profiles_acceptance_has_is_refused_the_same_way() -> None:
    assert _PROFILE_ONLY_ACCEPTANCE == ("needs_plugins",)


def test_a_control_the_person_exempts_from_the_driver_reaches_the_domain(tmp_path: Path) -> None:
    """G3: `"driver": false` on a control of the profile, changed by its `id`, and on one added."""
    added = {"id": "largo", "level": "L1", "run": "true", "report": "exit-code", "driver": False}
    config = load_config(
        _write(
            tmp_path,
            profile="python-uv@0.2",
            controls=[{"id": "lint", "driver": False}, added],
        )
    )

    checks = {control.id: control.checks_driver for control in domain_controls(config)}

    assert checks["lint"] is False
    assert checks["largo"] is False
    assert checks["types"] is True
