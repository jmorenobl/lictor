import hashlib
import json
import tomllib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from pydantic import ValidationError

from lictor.adapters.config.models import (
    AcceptanceConfig,
    BackendConfig,
    ControlChange,
    LictorConfig,
    LictorFile,
    LimitsConfig,
    Profile,
    ProfileVerification,
    ProjectVerification,
    allow_empty_message,
    driver_hints_message,
)
from lictor.application.ports import ConfigInitializer, ProfileInitConfig
from lictor.application.verification_env import VerificationEnvSpec
from lictor.domain.errors import ConfigError
from lictor.domain.rules import Impostors, MapLink

CONFIG_FILENAME = "lictor.json"
DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

_PROFILE_FALLBACK_KEYS = (
    "paths",
    "acceptance",
    "agent_tools",
    "agent_commands",
    "hints",
    "gitignore",
)


@dataclass(frozen=True)
class Defences:
    """What the profile guarantees, read from it on every load and so never out of date."""

    verification: VerificationEnvSpec
    suppressions: Sequence[str]
    banned_patterns: Sequence[str]
    forbidden_paths: Sequence[str]
    driver_exempt_paths: Sequence[str] = ()
    driver_hints: str = ""
    map_link: MapLink = "name"
    red_kinds: Sequence[str] = ()
    impostors: Impostors | None = None


def _resolve_profiles_dir(profiles_dir: Path | None) -> Path:
    return profiles_dir if profiles_dir is not None else DEFAULT_PROFILES_DIR


def _read_profile_file(profile_path: Path) -> Profile:
    try:
        raw = profile_path.read_text(encoding="utf-8")
        return Profile.model_validate_json(raw)
    except (OSError, ValidationError) as exc:
        msg = f"Perfil inválido en '{profile_path}': {exc}"
        raise ConfigError(msg) from exc


def list_profiles(profiles_dir: Path | None = None) -> list[Profile]:
    """Lista todos los perfiles disponibles ordenados por nombre."""
    directory = _resolve_profiles_dir(profiles_dir)
    if not directory.is_dir():
        msg = f"No existe el directorio de perfiles: '{directory}'"
        raise ConfigError(msg)
    profiles = [_read_profile_file(path) for path in sorted(directory.glob("*.json"))]
    if not profiles:
        msg = f"No se encontraron perfiles en '{directory}'"
        raise ConfigError(msg)
    return profiles


def profile_skill_sections(profiles_dir: Path | None = None) -> list[tuple[str, str]]:
    """What each profile adds to Lictor's skill, from `<name>.skill.md` beside its definition.

    It stays out of `config_fingerprint`: it tells a person how to prepare a project, and
    verification never reads it.
    """
    directory = _resolve_profiles_dir(profiles_dir)
    sections: list[tuple[str, str]] = []
    for profile in list_profiles(directory):
        path = directory / f"{profile.name}.skill.md"
        if path.is_file():
            sections.append((profile.name, path.read_text(encoding="utf-8")))
    return sections


def load_profile(name: str, profiles_dir: Path | None = None) -> Profile:
    """Carga un perfil por su nombre o por '<nombre>@<versión>'."""
    base_name, _, requested_version = name.partition("@")
    for profile in list_profiles(profiles_dir):
        if profile.name == base_name:
            if requested_version and profile.version != requested_version:
                msg = (
                    f"La versión solicitada para el perfil '{base_name}' "
                    f"('{requested_version}') no coincide con '{profile.version}'"
                )
                raise ConfigError(msg)
            return profile
    msg = f"Perfil desconocido: '{name}'"
    raise ConfigError(msg)


def detect_profile(project_dir: Path, profiles_dir: Path | None = None) -> Profile:
    """Detecta el perfil del proyecto comprobando sus archivos de detección."""
    for profile in list_profiles(profiles_dir):
        if all((project_dir / marker).exists() for marker in profile.detect):
            return profile
    msg = f"No se pudo detectar ningún perfil en '{project_dir}'"
    raise ConfigError(msg)


def _project_value(project_root: Path, file: str, key: str) -> str | None:
    """Read a dotted key of a project's TOML file, or None if it isn't a string there."""
    try:
        node: object = tomllib.loads((project_root / file).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    for part in key.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node.strip() if isinstance(node, str) and node.strip() else None


def render_hints(profile: Profile, project_root: Path | None) -> str:
    """The profile's `hints` with each `{name}` taken from the project, or its default."""
    hints = profile.hints
    for name, source in profile.hint_values.items():
        found = _project_value(project_root, source.file, source.key) if project_root else None
        hints = hints.replace(f"{{{name}}}", found or source.default)
    return hints


def build_config_from_profile(
    profile: Profile,
    *,
    model: str | None = None,
    project_root: Path | None = None,
) -> LictorConfig:
    """Construye una instancia de LictorConfig a partir de un perfil."""
    try:
        backend = BackendConfig(model=model) if model is not None else BackendConfig()
        return LictorConfig(
            version="0.1",
            profile=f"{profile.name}@{profile.version}",
            backend=backend,
            limits=LimitsConfig(),
            paths=profile.paths,
            acceptance=AcceptanceConfig.model_validate(
                profile.acceptance.model_dump(exclude={"driver_hints"})
            ),
            controls=list(profile.controls),
            agent_tools=list(profile.agent_tools),
            agent_commands=list(profile.agent_commands),
            hints=render_hints(profile, project_root),
            gitignore=list(profile.gitignore),
        )
    except ValidationError as exc:
        msg = f"Configuración inválida al construir desde el perfil '{profile.name}': {exc}"
        raise ConfigError(msg) from exc


def _init_payload(config: LictorConfig) -> str:
    """What `init` writes: what the person decides. The rest comes from the profile on each load,
    so a fix to the profile reaches the project without editing its `lictor.json`."""
    written = {
        "version": config.version,
        "profile": config.profile,
        "backend": {"model": config.backend.model},
        "hints": config.hints,
    }
    return json.dumps(written, indent=2, ensure_ascii=False) + "\n"


def init_config_file(
    project_dir: Path,
    *,
    profile_name: str | None = None,
    model: str | None = None,
    force: bool = False,
    profiles_dir: Path | None = None,
) -> LictorConfig:
    """Genera y escribe lictor.json sin sobrescribir uno existente salvo force=True."""
    target = project_dir / CONFIG_FILENAME
    if target.exists() and not force:
        msg = f"El archivo '{target}' ya existe (usa --force para sobrescribir)"
        raise ConfigError(msg)

    profile = (
        load_profile(profile_name, profiles_dir)
        if profile_name is not None
        else detect_profile(project_dir, profiles_dir)
    )
    config = build_config_from_profile(profile, model=model, project_root=project_dir)
    payload = _init_payload(config)
    try:
        target.write_text(payload, encoding="utf-8")
    except OSError as exc:
        msg = f"No se pudo escribir '{target}': {exc}"
        raise ConfigError(msg) from exc
    return config


class ProfileConfigInitializer(ConfigInitializer):
    """Adapter implementing ConfigInitializer using package profiles."""

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self.profiles_dir = profiles_dir

    def generate_config(
        self,
        project_root: Path,
        profile_name: str | None = None,
        model: str | None = None,
    ) -> ProfileInitConfig:
        profile = (
            load_profile(profile_name, self.profiles_dir)
            if profile_name is not None
            else detect_profile(project_root, self.profiles_dir)
        )
        config = build_config_from_profile(profile, model=model, project_root=project_root)
        payload = _init_payload(config)
        return ProfileInitConfig(
            profile_name=config.profile,
            gitignore_entries=list(config.gitignore),
            config_json=payload,
            model=config.backend.model,
            hints=config.hints,
        )


def _merge_profile_defaults(
    raw_data: dict[str, object],
    written: LictorFile,
    profiles_dir: Path | None,
    project_root: Path,
) -> dict[str, object]:
    profile = load_profile(written.profile, profiles_dir)
    profile_dump = profile.model_dump(mode="json")
    # The driver's instructions are the profile's alone: `resolve_defences` reads them.
    del profile_dump["acceptance"]["driver_hints"]
    profile_dump["hints"] = render_hints(profile, project_root)
    merged = dict(raw_data)
    for key in _PROFILE_FALLBACK_KEYS:
        if key not in merged:
            merged[key] = profile_dump[key]
    merged["controls"] = _resolve_controls(profile_dump["controls"], written.controls)

    acc = merged.get("acceptance")
    if isinstance(acc, dict):
        inherited = profile_dump["acceptance"]
        merged["acceptance"] = {"failure_exit_codes": inherited["failure_exit_codes"], **acc}
    return merged


_NEEDED_TO_ADD = ("level", "run", "report")


def _resolve_controls(
    profile_controls: list[dict[str, object]], changes: Sequence[ControlChange]
) -> list[dict[str, object]]:
    """Apply the project's changes by `id` over the profile's controls.

    A control of the profile keeps its place. One the project adds goes after the control it
    writes before it, or at the end if it is the first: so a full copy of the profile, with or
    without controls of its own in between, resolves in the order it was written.
    """
    resolved = [dict(control) for control in profile_controls]
    known = ", ".join(str(control["id"]) for control in profile_controls)
    dropped: set[str] = set()
    previous: int | None = None
    for change in changes:
        fields = change.model_dump(mode="json", exclude_unset=True, exclude={"enabled"})
        place = next((i for i, c in enumerate(resolved) if c["id"] == change.id), None)
        if place is None:
            _check_can_add(change, fields, known)
            place = len(resolved) if previous is None else previous + 1
            resolved.insert(place, {})
        resolved[place] = {**resolved[place], **fields}
        if not change.enabled:
            dropped.add(change.id)
        previous = place
    return [control for control in resolved if control["id"] not in dropped]


def _check_can_add(change: ControlChange, fields: dict[str, object], known: str) -> None:
    if not change.enabled:
        msg = (
            f"El control '{change.id}' que quitas con enabled: false no está en el perfil, "
            f"que tiene {known}"
        )
        raise ConfigError(msg)
    missing = [name for name in _NEEDED_TO_ADD if name not in fields]
    if missing:
        msg = (
            f"El control '{change.id}' no está en el perfil ({known}), así que lo añade: "
            f"escribe también {' y '.join(missing)}"
        )
        raise ConfigError(msg)


def load_config(path_or_dir: Path, profiles_dir: Path | None = None) -> LictorConfig:
    """Lee y valida lictor.json desde una ruta de archivo o directorio de proyecto."""
    config_path = path_or_dir / CONFIG_FILENAME if path_or_dir.is_dir() else path_or_dir
    if not config_path.is_file():
        msg = f"No se encontró el archivo de configuración: '{config_path}'"
        raise ConfigError(msg)

    try:
        raw_text = config_path.read_text(encoding="utf-8")
        parsed = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"Error al leer JSON de configuración en '{config_path}': {exc}"
        raise ConfigError(msg) from exc

    if not isinstance(parsed, dict):
        msg = f"El contenido de '{config_path}' debe ser un objeto JSON"
        raise ConfigError(msg)

    _reject_allow_empty(parsed, profiles_dir)
    acceptance = parsed.get("acceptance")
    if isinstance(acceptance, dict) and "driver_hints" in acceptance:
        raise ConfigError(driver_hints_message())
    try:
        written = LictorFile.model_validate(parsed)
    except ValidationError as exc:
        msg = f"Configuración inválida en '{config_path}': {exc}"
        raise ConfigError(msg) from exc
    merged = _merge_profile_defaults(parsed, written, profiles_dir, config_path.parent)
    try:
        return LictorConfig.model_validate(merged)
    except ValidationError as exc:
        msg = f"Configuración inválida en '{config_path}': {exc}"
        raise ConfigError(msg) from exc


def _reject_allow_empty(raw: dict[str, object], profiles_dir: Path | None) -> None:
    """Refuse the old field before validating, naming what the profile declares instead."""
    controls = raw.get("controls")
    if not isinstance(controls, list):
        return
    for control in controls:
        if not isinstance(control, dict) or "allow_empty" not in control:
            continue
        control_id = str(control.get("id", ""))
        profile_ref = raw.get("profile")
        codes: list[int] | None = None
        if isinstance(profile_ref, str):
            try:
                profile = load_profile(profile_ref, profiles_dir)
            except ConfigError:
                profile = None
            if profile is not None:
                same = [c.empty_exit_codes for c in profile.controls if c.id == control_id]
                codes = same[0] if same else None
        raise ConfigError(allow_empty_message(control_id, codes))


def _reject_reserved_names(project: ProjectVerification, profile: ProfileVerification) -> None:
    fixed_by_profile = {
        *profile.env,
        *profile.inherit,
        *profile.prepare_inherit,
        *profile.prepare_ask,
        *profile.prepare_home_files,
        profile.plugin_variable,
    }
    for name in (*project.env, *project.inherit, *project.prepare_inherit):
        if name in fixed_by_profile or any(fnmatchcase(name, p) for p in profile.reserved):
            msg = f"verification no puede declarar '{name}': esa variable la gobierna el perfil"
            raise ConfigError(msg)


def _profile_files(profile: ProfileVerification, profiles_dir: Path | None) -> list[Path]:
    sources = [_resolve_profiles_dir(profiles_dir) / file for file in profile.files]
    for source in sources:
        if not source.is_file():
            raise ConfigError(f"Falta el fichero del perfil: '{source}'")
    return sources


def resolve_defences(config: LictorConfig, profiles_dir: Path | None = None) -> Defences:
    """Combine the project's needs with its profile: the profile wins, the project only adds."""
    profile = load_profile(config.profile, profiles_dir)
    project = config.verification
    _reject_reserved_names(project, profile.verification)
    return Defences(
        verification=VerificationEnvSpec(
            prepare=profile.verification.prepare,
            prepare_inherit=[*profile.verification.prepare_inherit, *project.prepare_inherit],
            prepare_ask=profile.verification.prepare_ask,
            prepare_home_files=profile.verification.prepare_home_files,
            inherit=[*profile.verification.inherit, *project.inherit],
            env={**project.env, **profile.verification.env},
            files_dir_command=profile.verification.files_dir_command,
            files=_profile_files(profile.verification, profiles_dir),
            plugin_variable=profile.verification.plugin_variable,
            plugins=profile.verification.plugins,
            plugins_command=profile.verification.plugins_command,
        ),
        suppressions=profile.suppressions,
        banned_patterns=profile.banned_patterns,
        forbidden_paths=profile.forbidden_paths,
        driver_exempt_paths=profile.driver_exempt_paths,
        driver_hints=profile.acceptance.driver_hints,
        map_link=profile.oracle.link,
        red_kinds=tuple(profile.oracle.red_kinds),
        impostors=(
            Impostors(imp.pattern, tuple(imp.replacements))
            if (imp := profile.oracle.impostors) is not None
            else None
        ),
    )


def config_fingerprint(config: LictorConfig, defences: Defences) -> str:
    """Hash what a run depends on: the resolved values, whatever file each one came from.

    Files count by name and content, so moving the installation doesn't look like a change.
    """
    resolved = asdict(defences)
    resolved["verification"]["files"] = {
        source.name: hashlib.sha256(source.read_bytes()).hexdigest()
        for source in defences.verification.files
    }
    # Retention is read only when a run starts: changing it mustn't keep one from resuming.
    project = config.model_dump(mode="json", exclude={"retention"})
    payload = {"config": project, "defences": resolved}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def export_config_schema(output_path: Path | None = None) -> dict[str, object]:
    """Exporta el JSON Schema de lictor.json (`LictorFile`) y opcionalmente lo guarda en disco."""
    schema: dict[str, object] = LictorFile.model_json_schema()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
        output_path.write_text(payload, encoding="utf-8")
    return schema
