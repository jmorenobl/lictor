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
    CommandControlConfig,
    ControlChange,
    LictorConfig,
    LictorFile,
    LimitsConfig,
    Profile,
    ProfileVerification,
    ProjectVerification,
    ReaderControlConfig,
    allow_empty_message,
    driver_hints_message,
    no_shape_message,
    plugin_without_list_message,
    two_shapes_message,
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


# The fields of a run of its own, which a reader doesn't have (ADR-0008, point 2).
RUN_FIELDS = ("empty_exit_codes", "timeout_s", "when_exists")


def _resolve_controls(
    profile_controls: list[dict[str, object]], changes: Sequence[ControlChange]
) -> list[dict[str, object]]:
    """Apply the project's changes by `id` over the profile's controls.

    A control of the profile keeps its place. One the project adds goes after the control it
    writes before it, or at the end if it is the first: so a copy of the profile, with or without
    controls of its own in between, resolves in the order it was written. `enabled: false` drops
    a control and merges nothing.
    """
    resolved: list[dict[str, object] | None] = [dict(control) for control in profile_controls]
    ids = [str(control["id"]) for control in profile_controls]
    known = ", ".join(ids)
    previous: int | None = None
    for change in changes:
        place = ids.index(change.id) if change.id in ids else None
        if not change.enabled:
            _check_drop(change, place, known)
            if place is not None:
                resolved[place] = None
            previous = place if place is not None else previous
            continue
        base = resolved[place] if place is not None else None
        control = resolve_shape(base, change, known)
        if place is None:
            place = len(resolved) if previous is None else previous + 1
            resolved.insert(place, control)
            ids.insert(place, change.id)
        else:
            resolved[place] = control
        previous = place
    return [control for control in resolved if control is not None]


def _check_drop(change: ControlChange, place: int | None, known: str) -> None:
    if place is None:
        msg = (
            f"El control '{change.id}' que quitas con enabled: false no está en el perfil, "
            f"que tiene {known}"
        )
        raise ConfigError(msg)
    extra = sorted(change.model_fields_set - {"id", "enabled"})
    if extra:
        msg = (
            f"El control '{change.id}' se quita con enabled: false, así que {_names(extra)} "
            "no se aplicaría: quítalo."
        )
        raise ConfigError(msg)


def _names(fields: Sequence[str]) -> str:
    return " y ".join(", ".join(fields).rsplit(", ", 1)) if fields else ""


def resolve_shape(
    base: dict[str, object] | None, change: ControlChange, known: str = ""
) -> dict[str, object]:
    """What a control is once a change of `lictor.json` applies over its base (ADR-0008).

    The base is the profile's control, or None for a new `id`. Written is what the change has
    in `model_fields_set`, a null included; what the shape decides is resolved here, before
    pydantic, so each mistake gets its own message and never the error of a union's branches.
    """
    written = change.model_dump(mode="json", include=change.model_fields_set - {"enabled"})
    has_run = "run" in written
    reads = [name for name in ("reads", "adds") if name in written]
    run_fields = [name for name in RUN_FIELDS if name in written]
    if has_run and reads:
        raise ConfigError(two_shapes_message(change.id))
    if base is None:
        return _new_control(change.id, written, reads, run_fields, known)
    if "reads" not in base:
        if has_run or not reads:
            return {**base, **written}
        return {**_without(base, ("run", *RUN_FIELDS)), **_as_reader(change.id, base, written)}
    if has_run:
        return {**_without(base, ("reads", "adds")), **written}
    _check_reader_report(change.id, written)
    if run_fields:
        raise ConfigError(_run_fields_message(change.id, run_fields, str(base["reads"])))
    return {**base, **written}


def _as_reader(
    control_id: str, base: dict[str, object], written: dict[str, object]
) -> dict[str, object]:
    """A control of the profile that runs, turned into a reader by what was written."""
    missing = [name for name in ("reads", "adds") if name not in written]
    if missing:
        msg = (
            f"El control '{control_id}' del perfil ejecuta un comando: para que lea la "
            f"ejecución de otro hacen falta reads y adds, y falta {missing[0]}."
        )
        raise ConfigError(msg)
    _check_reader_report(control_id, {"report": base["report"], **written})
    run_fields = [name for name in RUN_FIELDS if name in written]
    if run_fields:
        raise ConfigError(_run_fields_message(control_id, run_fields, str(written["reads"])))
    return written


def _new_control(
    control_id: str,
    written: dict[str, object],
    reads: Sequence[str],
    run_fields: Sequence[str],
    known: str,
) -> dict[str, object]:
    adds = f"El control '{control_id}' no está en el perfil ({known}), así que lo añade"
    if "run" in written:
        missing = [name for name in ("level", "report") if name not in written]
        if missing:
            raise ConfigError(f"{adds}: escribe también {_names(missing)}")
        return written
    if not reads:
        msg = (
            f"{adds}. {no_shape_message(control_id)}: un control que ejecuta necesita level, "
            'report y run, y uno que lee la ejecución de otro, level, report: "coverage", reads '
            "y adds."
        )
        raise ConfigError(msg)
    missing = [name for name in ("level", "report", "reads", "adds") if name not in written]
    if missing:
        msg = (
            f"{adds}, y lee la ejecución de otro: escribe también {_names(missing)} "
            '(report: "coverage")'
        )
        raise ConfigError(msg)
    _check_reader_report(control_id, written)
    if run_fields:
        raise ConfigError(_run_fields_message(control_id, run_fields, str(written["reads"])))
    return written


def _check_reader_report(control_id: str, written: dict[str, object]) -> None:
    report = written.get("report")
    if report is not None and report != "coverage":
        msg = (
            f"El control '{control_id}' lee la ejecución de otro, y un lector solo puede ser de "
            f'cobertura: su report es "coverage", no "{report}".'
        )
        raise ConfigError(msg)


def _run_fields_message(control_id: str, run_fields: Sequence[str], reads: str) -> str:
    return (
        f"El control '{control_id}' lee la ejecución de '{reads}' y no ejecuta nada: "
        f"{_names(run_fields)} son de una ejecución y no se aplicarían. Quítalos, o "
        f"escríbelos en '{reads}'."
    )


def _without(control: dict[str, object], names: Sequence[str]) -> dict[str, object]:
    return {key: value for key, value in control.items() if key not in names}


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
        msg = f"Configuración inválida en '{config_path}': {_validation_text(exc)}"
        raise ConfigError(msg) from exc


def _validation_text(exc: ValidationError) -> str:
    """Lictor's own messages alone when every error is one: they say what to fix.

    Pydantic's dump of the merged configuration would bury them; any other error keeps it.
    """
    errors = exc.errors()
    if all(error["type"] == "value_error" for error in errors):
        return "\n".join(str(error["msg"]).removeprefix("Value error, ") for error in errors)
    return str(exc)


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
                raise ConfigError(_allow_empty_in_profile(profile, control_id))
        raise ConfigError(allow_empty_message(control_id, codes))


def _allow_empty_in_profile(profile: Profile, control_id: str) -> str:
    """Name the codes that apply instead: the control's own, or its read control's."""
    by_id = {c.id: c for c in profile.controls}
    control = by_id.get(control_id)
    if isinstance(control, ReaderControlConfig):
        read = by_id.get(control.reads)
        codes = read.empty_exit_codes if isinstance(read, CommandControlConfig) else None
        return allow_empty_message(control_id, codes, reads=control.reads)
    codes = control.empty_exit_codes if control is not None else None
    return allow_empty_message(control_id, codes)


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
    if not profile.verification.plugins_command:
        for control in config.controls:
            if control.when_plugin is not None:
                raise ConfigError(plugin_without_list_message(control.id))
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
