import hashlib
import json
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Final

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
    ProfileAcceptance,
    ProfileOracle,
    ProfileVerification,
    ProjectVerification,
    ReaderControlConfig,
    agent_commands_message,
    agent_run_problems,
    allow_empty_message,
    code_exclude_message,
    driver_hints_message,
    granted_check,
    needs_plugins_message,
    plugin_without_list_message,
    prepare_message,
    profile_only_message,
    refactor_hints_message,
    stale_agent_command_message,
    survey_message,
    two_shapes_message,
)
from lictor.application.ports import ConfigInitializer, ProfileInitConfig
from lictor.application.verification_env import VerificationEnvSpec
from lictor.domain.controls import complexity_limit_message
from lictor.domain.errors import ConfigError
from lictor.domain.interface_proof import InterfaceImpostor
from lictor.domain.refactor_scope import code_paths_problem
from lictor.domain.rules import Impostors, MapLink, StubConstant

CONFIG_FILENAME = "lictor.json"
DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

_PROFILE_FALLBACK_KEYS = (
    "paths",
    "acceptance",
    "agent_tools",
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
    refactor_hints: str = ""
    code_paths: Sequence[str] = ()
    code_exclude: Sequence[str] = ()
    map_link: MapLink = "name"
    red_kinds: Sequence[str] = ()
    impostors: Impostors | None = None
    declared_impostors: bool = False
    interface_impostor: InterfaceImpostor | None = None
    # What a project needs before a run, and how to read what its controls see (ADR-0015).
    needs_plugins: dict[str, str] = field(default_factory=dict)
    survey_run: str | None = None
    ci_hooks: dict[str, str] = field(default_factory=dict)
    detect: Sequence[str] = ()


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


def project_root_of(start: Path) -> Path:
    """The project a command in `start` belongs to, as the skill reads it.

    The nearest directory with `lictor.json`, from `start` up to the root of its repository; with
    none, that root (where `lictor init` would detect the profile); outside a repository, `start`.
    A session opened in `src/` still speaks of its project.
    """
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / CONFIG_FILENAME).is_file():
            return directory
        if (directory / ".git").exists():
            return directory
    return start


def declared_profile(project_root: Path) -> str | None:
    """The profile `lictor.json` names, without its version, or None if there is no file.

    It reads that one key and nothing else, so a file that doesn't load still says its profile
    (the skill helps a person most when the configuration is broken). A file whose profile can't
    be read raises `ConfigError`, saying why.
    """
    path = project_root / CONFIG_FILENAME
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = f"`{CONFIG_FILENAME}` no se pudo leer ({exc})"
        raise ConfigError(msg) from exc
    name = parsed.get("profile") if isinstance(parsed, dict) else None
    if not isinstance(name, str) or not name.partition("@")[0]:
        msg = f"`{CONFIG_FILENAME}` no dice su perfil en «profile»"
        raise ConfigError(msg)
    return name.partition("@")[0]


class PackagedProfiles:
    """The profiles that come with Lictor, read once and only as far as the skill asks.

    A profile's `<name>.skill.md`, beside its definition, stays out of `config_fingerprint`: it
    tells a person how to prepare a project, and verification never reads it.
    """

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self._directory = _resolve_profiles_dir(profiles_dir)
        self._profiles: list[Profile] | None = None

    def _all(self) -> list[Profile]:
        if self._profiles is None:
            self._profiles = list_profiles(self._directory)
        return self._profiles

    def names(self) -> list[str]:
        return [profile.name for profile in self._all()]

    def skill_text(self, name: str) -> str | None:
        path = self._directory / f"{name}.skill.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def requested(self, name: str) -> str:
        base_name = name.partition("@")[0]
        if base_name not in self.names():
            msg = f"Perfil desconocido: '{name}'. Los de esta versión: {', '.join(self.names())}"
            raise ConfigError(msg)
        return load_profile(name, self._directory).name

    def detected(self, project_root: Path) -> str | None:
        for profile in self._all():
            if _detects(profile, project_root):
                return profile.name
        return None


def load_profile(name: str, profiles_dir: Path | None = None) -> Profile:
    """Carga un perfil por su nombre o por '<nombre>@<versión>'."""
    base_name, _, requested_version = name.partition("@")
    for profile in list_profiles(profiles_dir):
        if profile.name == base_name:
            if requested_version in profile.retired_versions:
                current = f"{profile.name}@{profile.version}"
                why = profile.retired_versions[requested_version]
                msg = f"El perfil {name} ya no existe: usa {current}, que {why} (ver el CHANGELOG)"
                raise ConfigError(msg)
            if requested_version and profile.version != requested_version:
                msg = (
                    f"La versión solicitada para el perfil '{base_name}' "
                    f"('{requested_version}') no coincide con '{profile.version}'"
                )
                raise ConfigError(msg)
            return profile
    msg = f"Perfil desconocido: '{name}'"
    raise ConfigError(msg)


def _detects(profile: Profile, project_root: Path) -> bool:
    """A profile without markers is never detected: `all` of nothing would match any project."""
    return bool(profile.detect) and all((project_root / m).exists() for m in profile.detect)


def detect_profile(project_dir: Path, profiles_dir: Path | None = None) -> Profile:
    """Detecta el perfil del proyecto comprobando sus archivos de detección."""
    for profile in list_profiles(profiles_dir):
        if _detects(profile, project_dir):
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
    """Construye una instancia de LictorConfig a partir de un perfil que declara su proyecto.

    Un perfil sin `paths` o sin `acceptance` no basta: lo que falta lo escribe la persona.
    """
    if profile.paths is None or profile.acceptance is None:
        raise ConfigError(missing_project_fields_message(profile, _missing_of(profile, {})))
    try:
        backend = BackendConfig(model=model) if model is not None else BackendConfig()
        return LictorConfig(
            version="0.1",
            profile=f"{profile.name}@{profile.version}",
            backend=backend,
            limits=LimitsConfig(),
            paths=profile.paths,
            acceptance=AcceptanceConfig.model_validate(
                profile.acceptance.model_dump(exclude={"driver_hints", "needs_plugins"})
            ),
            controls=list(profile.controls),
            agent_tools=list(profile.agent_tools),
            hints=render_hints(profile, project_root),
            gitignore=list(profile.gitignore),
        )
    except ValidationError as exc:
        msg = f"Configuración inválida al construir desde el perfil '{profile.name}': {exc}"
        raise ConfigError(msg) from exc


def _init_payload(profile_ref: str, model: str, hints: str) -> str:
    """What `init` writes: what the person decides. The rest comes from the profile on each load,
    so a fix to the profile reaches the project without editing its `lictor.json`."""
    written = {
        "version": "0.1",
        "profile": profile_ref,
        "backend": {"model": model},
        "hints": hints,
    }
    return json.dumps(written, indent=2, ensure_ascii=False) + "\n"


def init_config_file(
    project_dir: Path,
    *,
    profile_name: str | None = None,
    model: str | None = None,
    force: bool = False,
    profiles_dir: Path | None = None,
) -> ProfileInitConfig:
    """Genera y escribe lictor.json sin sobrescribir uno existente salvo force=True."""
    target = project_dir / CONFIG_FILENAME
    if target.exists() and not force:
        msg = f"El archivo '{target}' ya existe (usa --force para sobrescribir)"
        raise ConfigError(msg)
    init = ProfileConfigInitializer(profiles_dir).generate_config(project_dir, profile_name, model)
    try:
        target.write_text(init.config_json, encoding="utf-8")
    except OSError as exc:
        msg = f"No se pudo escribir '{target}': {exc}"
        raise ConfigError(msg) from exc
    return init


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
        chosen = model if model is not None else BackendConfig().model
        hints = render_hints(profile, project_root)
        reference = f"{profile.name}@{profile.version}"
        return ProfileInitConfig(
            profile_name=reference,
            gitignore_entries=list(profile.gitignore),
            config_json=_init_payload(reference, chosen, hints),
            model=chosen,
            hints=hints,
            to_write=tuple(_missing_of(profile, {})),
            status=profile.status,
        )


# What a project writes when its profile doesn't (ADR-0020), in the order a person is told.
_PROJECT_FIELDS: Final = ("paths", "acceptance", "code_paths")


def _missing_of(profile: Profile, written: Mapping[str, object]) -> list[str]:
    """The fields neither the profile nor `lictor.json` declares that a configuration needs."""
    declared = {
        "paths": profile.paths is not None,
        "acceptance": profile.acceptance is not None,
        "code_paths": bool(profile.code_paths),
    }
    return [name for name in _PROJECT_FIELDS if not declared[name] and not written.get(name)]


def missing_project_fields_message(profile: Profile, missing: Sequence[str]) -> str:
    """Tell the person everything their profile leaves to them and `lictor.json` lacks, at once."""
    what = {
        "paths": "`paths` (qué puede escribir el agente, dónde va el driver y qué está protegido)",
        "acceptance": "`acceptance` (la orden que ejecuta la aceptación y deja un JUnit en {out})",
        "code_paths": (
            "`code_paths` (las rutas de código, dentro de paths.writable; lo usa la puerta del "
            "refactor, L0-alcance)"
        ),
    }
    listed = [what[name] for name in missing]
    joined = listed[0] if len(listed) == 1 else ", ".join(listed[:-1]) + " y " + listed[-1]
    return (
        f"El perfil {profile.name} no sabe cómo es tu proyecto: escribe en lictor.json {joined}. "
        "La skill de Lictor te ayuda: /lictor en Claude Code."
    )


def _merge_profile_defaults(
    raw_data: dict[str, object],
    written: LictorFile,
    profiles_dir: Path | None,
    project_root: Path,
) -> dict[str, object]:
    profile = load_profile(written.profile, profiles_dir)
    missing = _missing_of(profile, raw_data)
    if missing:
        raise ConfigError(missing_project_fields_message(profile, missing))
    profile_dump = profile.model_dump(mode="json")
    inherited = profile_dump["acceptance"]
    if inherited is not None:
        # The driver's instructions are the profile's alone: `resolve_defences` reads them.
        del inherited["driver_hints"]
        del inherited["needs_plugins"]
    profile_dump["hints"] = render_hints(profile, project_root)
    merged = dict(raw_data)
    for key in _PROFILE_FALLBACK_KEYS:
        if key not in merged and profile_dump[key] is not None:
            merged[key] = profile_dump[key]
    merged["controls"] = _resolve_controls(profile_dump["controls"], written.controls)

    acc = merged.get("acceptance")
    if isinstance(acc, dict) and inherited is not None:
        merged["acceptance"] = {"failure_exit_codes": inherited["failure_exit_codes"], **acc}
    return merged


# The fields of a run of its own, which a reader doesn't have (ADR-0008, point 2).
# Fields of a run: a reader has none, so turning a control into one drops them (F83: agent_fix).
RUN_FIELDS = ("empty_exit_codes", "timeout_s", "when_exists", "limit", "agent_fix", "agent")


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
            f"{adds}, pero no dice qué ejecuta. Un control que ejecuta necesita level, report y "
            'run, y uno que lee la ejecución de otro, level, report: "coverage", reads y adds.'
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
    if "limit" in run_fields:
        # A limit is no field of a run: it only goes with a complexity report (ADR-0013).
        return complexity_limit_message(control_id, "coverage")
    if len(run_fields) == 1:
        what = f"{run_fields[0]} es de una ejecución y no se aplicaría. Quítalo, o escríbelo"
    else:
        what = (
            f"{_names(run_fields)} son de una ejecución y no se aplicarían. Quítalos, o escríbelos"
        )
    reader = f"El control '{control_id}' lee la ejecución de '{reads}' y no ejecuta nada"
    return f"{reader}: {what} en '{reads}'."


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
    _reject_profile_fields(parsed)
    known = _known_profile(parsed.get("profile"), profiles_dir)
    if known is not None:
        # Before validating: a field the profile declares says so, whatever else is missing.
        _reject_exclusive_fields(known, parsed)
    # Refused once the profile it asks for is known: a retired profile says more (F83).
    old_commands = parsed.pop("agent_commands", None) is not None
    try:
        written = LictorFile.model_validate(parsed)
    except ValidationError as exc:
        msg = f"Configuración inválida en '{config_path}': {_validation_text(exc)}"
        raise ConfigError(msg) from exc
    merged = _merge_profile_defaults(parsed, written, profiles_dir, config_path.parent)
    if old_commands:
        raise ConfigError(agent_commands_message())
    try:
        config = LictorConfig.model_validate(merged)
    except ValidationError as exc:
        msg = f"Configuración inválida en '{config_path}': {_validation_text(exc)}"
        raise ConfigError(msg) from exc
    _reject_unrunnable_agent_runs(config, written, config_path)
    _reject_stale_agent_commands(config, written, load_profile(config.profile, profiles_dir))
    return config


# What only the profile declares, each with the message that says where the project's own goes.
# The fields only the profile declares that have a sentence of their own: where the project's goes.
_OWN_MESSAGES: Final[Mapping[str, Callable[[], str]]] = {
    "acceptance.needs_plugins": needs_plugins_message,
    "survey": survey_message,
    "refactor_hints": refactor_hints_message,
}


# Every field of a profile a project never writes (ADR-0020): what the profile has and the file
# a person writes doesn't, taken from the models so that a new one is refused the same way.
_PROFILE_ONLY_FIELDS: Final = tuple(
    sorted(set(Profile.model_fields) - set(LictorFile.model_fields))
)
_PROFILE_ONLY_ACCEPTANCE: Final = tuple(
    sorted(set(ProfileAcceptance.model_fields) - set(AcceptanceConfig.model_fields))
)
_PROFILE_ONLY_VERIFICATION: Final = tuple(
    sorted(set(ProfileVerification.model_fields) - set(ProjectVerification.model_fields))
)


def _reject_profile_fields(parsed: dict[str, object]) -> None:
    """Refuse a field only the profile declares, even when it declares none, with its message.

    Those with a sentence of their own say it; any other says the common one, never pydantic's.
    """
    sections = (
        ("acceptance.", parsed.get("acceptance"), _PROFILE_ONLY_ACCEPTANCE),
        ("", parsed, _PROFILE_ONLY_FIELDS),
        ("verification.", parsed.get("verification"), _PROFILE_ONLY_VERIFICATION),
    )
    for prefix, written, keys in sections:
        found = [key for key in keys if isinstance(written, dict) and key in written]
        if found:
            name = f"{prefix}{found[0]}"
            own = _OWN_MESSAGES.get(name)
            raise ConfigError(own() if own is not None else profile_only_message(name))


def _known_profile(reference: object, profiles_dir: Path | None) -> Profile | None:
    """The profile `lictor.json` asks for, or None: one that doesn't load is told later, in turn."""
    if not isinstance(reference, str) or not reference:
        return None
    try:
        return load_profile(reference, profiles_dir)
    except ConfigError:
        return None


def _reject_exclusive_fields(profile: Profile, written: Mapping[str, object]) -> None:
    """Refuse what the project may write only where its profile declares nothing (ADR-0020).

    The profile's `prepare` is the contract of the hashed environment and its `driver_hints` the
    instructions of its oracle: replacing either changes what the profile guarantees. Excluding
    code from the profile's code paths would weaken `L0-alcance`.
    """
    if _written(written, "acceptance", "driver_hints") and _driver_hints_of(profile):
        raise ConfigError(driver_hints_message(profile.name))
    if _written(written, "verification", "prepare") and profile.verification.prepare:
        raise ConfigError(prepare_message(profile.name))
    if written.get("code_exclude") and profile.code_paths:
        raise ConfigError(code_exclude_message(profile.name))


def _written(written: Mapping[str, object], section: str, key: str) -> bool:
    """Tell whether `lictor.json` gives `section.key` a value that isn't empty."""
    part = written.get(section)
    return isinstance(part, dict) and bool(part.get(key))


def _driver_hints_of(profile: Profile) -> str:
    return profile.acceptance.driver_hints if profile.acceptance is not None else ""


def _union(profile: Sequence[str], project: Sequence[str]) -> tuple[str, ...]:
    """The profile's values, then the project's it lacks: adding only ever rejects more."""
    return tuple(dict.fromkeys([*profile, *project]))


def _reject_unrunnable_agent_runs(
    config: LictorConfig, written: LictorFile, config_path: Path
) -> None:
    """Refuse an `agent_run` or `agent_fix` the project writes and the agent couldn't run.

    One inherited from the profile under tools the project narrowed is only left untold
    (`domain_controls`): nobody asked for it there.
    """
    asked = {
        (change.id, name)
        for change in written.controls
        for name in _AGENT_COMMANDS
        if name in change.model_fields_set and getattr(change, name) is not None
    }
    problems = [
        problem
        for control in config.controls
        for name in _AGENT_COMMANDS
        if (control.id, name) in asked and not (name == "agent_run" and granted_check(control))
        for problem in agent_run_problems([_only(control, name)], config.agent_tools)
    ]
    if problems:
        msg = f"Configuración inválida en '{config_path}': " + "\n".join(problems)
        raise ConfigError(msg)


def _only(
    control: CommandControlConfig | ReaderControlConfig, name: str
) -> CommandControlConfig | ReaderControlConfig:
    """The control with only one of the agent's commands, to judge that one alone."""
    others = {other: None for other in _AGENT_COMMANDS if other != name and hasattr(control, other)}
    return control.model_copy(update=others)


def _changed_runs(config: LictorConfig, written: LictorFile) -> set[str]:
    """The controls whose command the project changed: their `run`, or a reader's `adds`.

    A reader also changes when the control it reads does: what its `agent_run` measures runs that.
    """
    changes = {change.id: change.model_fields_set for change in written.controls}
    ran = {cid for cid, fields in changes.items() if "run" in fields}
    readers = {
        c.id
        for c in config.controls
        if isinstance(c, ReaderControlConfig)
        and ({"adds", "reads"} & changes.get(c.id, set()) or c.reads in ran)
    }
    return ran | readers


def _reject_stale_agent_commands(
    config: LictorConfig, written: LictorFile, profile: Profile
) -> None:
    """Refuse an agent's command inherited from the profile by a control whose run changed (F83).

    The profile's `agent_run` or `agent_fix` was written for its own `run`: under another one it
    may check or fix something else, and the attempt it costs is how it shows. The fix is always to
    write it, or `null`, so it is an error and not a warning.
    """
    fields = {change.id: change.model_fields_set for change in written.controls}
    theirs = {control.id: control for control in profile.controls}
    problems = [
        stale_agent_command_message(cid, name, inherited)
        for cid in sorted(_changed_runs(config, written))
        if (base := theirs.get(cid)) is not None
        for name in _AGENT_COMMANDS
        if name not in fields.get(cid, set())
        and (inherited := getattr(base, name, None)) is not None
    ]
    if problems:
        raise ConfigError("\n".join(problems))


_AGENT_COMMANDS: Final = ("agent_run", "agent_fix")


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


def _reject_reserved_names(
    project: ProjectVerification, profile: ProfileVerification, oracle: ProfileOracle
) -> None:
    impostor = oracle.interface_impostor
    fixed_by_profile = {
        *(impostor.variables if impostor is not None else []),
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
    _reject_reserved_names(project, profile.verification, profile.oracle)
    if not profile.verification.plugins_command:
        for control in config.controls:
            if control.when_plugin is not None:
                raise ConfigError(plugin_without_list_message(control.id))
    code_paths = _union(profile.code_paths, config.code_paths)
    problem = code_paths_problem(code_paths, config.paths.writable)
    if problem is not None:
        raise ConfigError(problem)
    return Defences(
        verification=VerificationEnvSpec(
            prepare=profile.verification.prepare or project.prepare,
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
        suppressions=_union(profile.suppressions, config.suppressions),
        banned_patterns=_union(profile.banned_patterns, config.banned_patterns),
        forbidden_paths=_union(profile.forbidden_paths, config.forbidden_paths),
        driver_exempt_paths=profile.driver_exempt_paths,
        driver_hints=_driver_hints_of(profile) or config.acceptance.driver_hints,
        refactor_hints=profile.refactor_hints,
        code_paths=code_paths,
        code_exclude=tuple(config.code_exclude),
        map_link=profile.oracle.link,
        red_kinds=tuple(profile.oracle.red_kinds),
        impostors=_impostors(profile.oracle),
        declared_impostors=profile.oracle.declared_impostors,
        interface_impostor=_interface_impostor(profile.oracle),
        needs_plugins=dict(profile.acceptance.needs_plugins) if profile.acceptance else {},
        survey_run=profile.survey.run if profile.survey is not None else None,
        ci_hooks=dict(profile.survey.ci_hooks) if profile.survey is not None else {},
        detect=tuple(profile.detect),
    )


def _impostors(oracle: ProfileOracle) -> Impostors | None:
    imp = oracle.impostors
    if imp is None:
        return None
    return Impostors(
        imp.pattern, tuple(StubConstant(r.code, r.name, r.double) for r in imp.replacements)
    )


def _interface_impostor(oracle: ProfileOracle) -> InterfaceImpostor | None:
    face = oracle.interface_impostor
    if face is None:
        return None
    return InterfaceImpostor(face.variable, face.control, tuple(face.modes), face.gherkin)


def rule_files(config: LictorConfig, profiles_dir: Path | None = None) -> tuple[str, ...]:
    """The profile's rule files (F79). Only `doctor` and `lictor conventions` read them, so they
    stay out of the defences and of the fingerprint: a new name mustn't keep a run from resuming.
    """
    return tuple(load_profile(config.profile, profiles_dir).rule_files)


def write_reviewed_conventions(project_dir: Path, reviewed: Mapping[str, str]) -> None:
    """Write `conventions.reviewed` into `lictor.json` and leave every other key as it is.

    It is written in `init`'s form; the file is replaced at once, so a kill leaves one or the
    other. The file must load first: a broken one is the person's to fix, not to overwrite.
    """
    target = project_dir / CONFIG_FILENAME
    load_config(target)
    raw = json.loads(target.read_text(encoding="utf-8"))
    conventions = raw.get("conventions")
    written = dict(conventions) if isinstance(conventions, dict) else {}
    written["reviewed"] = dict(sorted(reviewed.items()))
    raw["conventions"] = written
    payload = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
    partial = target.with_name(f".{CONFIG_FILENAME}.tmp")
    try:
        partial.write_text(payload, encoding="utf-8")
        partial.replace(target)
    except OSError as exc:
        msg = f"No se pudo escribir '{target}': {exc}"
        raise ConfigError(msg) from exc


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
    # So are the blind spots a person accepts: only the reports read them (ADR-0015), and the
    # rule files the person reviewed: only `doctor` reads them. The rules themselves count (F79).
    project = config.model_dump(
        mode="json",
        exclude={"retention": True, "blind_spots": True, "conventions": {"reviewed"}},
    )
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
