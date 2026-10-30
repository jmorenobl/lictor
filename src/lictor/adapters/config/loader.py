import json
from pathlib import Path

from pydantic import ValidationError

from lictor.adapters.config.models import (
    BackendConfig,
    LictorConfig,
    LimitsConfig,
    Profile,
)
from lictor.domain.errors import ConfigError

CONFIG_FILENAME = "lictor.json"
DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

_PROFILE_FALLBACK_KEYS = (
    "paths",
    "acceptance",
    "controls",
    "suppressions",
    "agent_tools",
    "hints",
    "gitignore",
)


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


def build_config_from_profile(
    profile: Profile,
    *,
    model: str | None = None,
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
            acceptance=profile.acceptance,
            controls=list(profile.controls),
            suppressions=list(profile.suppressions),
            agent_tools=list(profile.agent_tools),
            hints=profile.hints,
            gitignore=list(profile.gitignore),
        )
    except ValidationError as exc:
        msg = f"Configuración inválida al construir desde el perfil '{profile.name}': {exc}"
        raise ConfigError(msg) from exc


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
    config = build_config_from_profile(profile, model=model)
    payload = json.dumps(config.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
    try:
        target.write_text(payload, encoding="utf-8")
    except OSError as exc:
        msg = f"No se pudo escribir '{target}': {exc}"
        raise ConfigError(msg) from exc
    return config


def _merge_profile_defaults(
    raw_data: dict[str, object],
    profiles_dir: Path | None,
) -> dict[str, object]:
    profile_ref = raw_data.get("profile")
    if not isinstance(profile_ref, str) or not profile_ref.strip():
        return raw_data
    if all(key in raw_data for key in _PROFILE_FALLBACK_KEYS):
        return raw_data

    profile = load_profile(profile_ref, profiles_dir)
    profile_dump = profile.model_dump(mode="json")
    merged = dict(raw_data)
    for key in _PROFILE_FALLBACK_KEYS:
        if key not in merged:
            merged[key] = profile_dump[key]

    acc = merged.get("acceptance")
    if isinstance(acc, dict) and "driver_hints" not in acc:
        acc_copy = dict(acc)
        acc_copy["driver_hints"] = profile.acceptance.driver_hints
        merged["acceptance"] = acc_copy
    return merged


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

    merged = _merge_profile_defaults(parsed, profiles_dir)
    try:
        return LictorConfig.model_validate(merged)
    except ValidationError as exc:
        msg = f"Configuración inválida en '{config_path}': {exc}"
        raise ConfigError(msg) from exc


def export_config_schema(output_path: Path | None = None) -> dict[str, object]:
    """Exporta el JSON Schema de LictorConfig y opcionalmente lo guarda en disco."""
    schema: dict[str, object] = LictorConfig.model_json_schema()
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
        output_path.write_text(payload, encoding="utf-8")
    return schema

