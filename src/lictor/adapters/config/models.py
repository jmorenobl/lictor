import re
from pathlib import PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ControlLevel = Literal["L1", "L2", "L4", "L5"]
ReportFormat = Literal["exit-code", "junit"]
ControlMode = Literal["block", "measure"]
ProfileStatus = Literal["stable", "beta", "alpha"]
RedKind = Literal["assertion", "not_implemented", "error"]
MapLinkKind = Literal["name", "scenario"]
BackendKind = Literal["claude-cli", "fake"]
BackendAuth = Literal["subscription", "api_key"]

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

_SHORT_MODEL_ALIASES = frozenset(
    {
        "opus",
        "sonnet",
        "haiku",
        "default",
        "latest",
        "claude",
        "claude-opus",
        "claude-sonnet",
        "claude-haiku",
    }
)


def _normalize_path(raw: str) -> PurePosixPath:
    cleaned = raw.strip().strip("/")
    return PurePosixPath(cleaned) if cleaned else PurePosixPath(".")


def _paths_overlap(path_a: str, path_b: str) -> bool:
    norm_a = _normalize_path(path_a)
    norm_b = _normalize_path(path_b)
    return norm_a == norm_b or norm_a.is_relative_to(norm_b) or norm_b.is_relative_to(norm_a)


def _validate_regexes(patterns: list[str]) -> list[str]:
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            msg = f"banned_patterns contiene una expresión inválida '{pattern}': {exc}"
            raise ValueError(msg) from exc
    return patterns


def _validate_unique_control_ids(controls: list["ControlConfig"]) -> list["ControlConfig"]:
    seen: set[str] = set()
    for ctrl in controls:
        if ctrl.id in seen:
            msg = f"Los id de los controles deben ser únicos; duplicado: '{ctrl.id}'"
            raise ValueError(msg)
        seen.add(ctrl.id)
    return controls


class PathsConfig(BaseModel):
    """Rutas escribibles, del driver y protegidas del proyecto."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    writable: list[str] = Field(min_length=1)
    driver: list[str] = Field(min_length=1)
    protected: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_no_writable_overlap(self) -> Self:
        for w_path in self.writable:
            for p_path in self.protected:
                if _paths_overlap(w_path, p_path):
                    msg = (
                        "paths.writable y paths.protected no pueden solaparse: "
                        f"'{w_path}' y '{p_path}'"
                    )
                    raise ValueError(msg)
            for d_path in self.driver:
                if _paths_overlap(w_path, d_path):
                    msg = (
                        "paths.writable y paths.driver no pueden solaparse: "
                        f"'{w_path}' y '{d_path}'"
                    )
                    raise ValueError(msg)
        return self


class AcceptanceConfig(BaseModel):
    """Configuración de la suite de aceptación (L3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run: str = Field(min_length=1)
    report: ReportFormat = "junit"
    driver_hints: str = ""
    timeout_s: int = Field(default=600, gt=0)
    failure_exit_codes: list[int] = Field(default=[1], min_length=1)

    @model_validator(mode="after")
    def _validate_failure_exit_codes(self) -> Self:
        if 0 in self.failure_exit_codes:
            raise ValueError("acceptance.failure_exit_codes no puede incluir 0")
        return self

    @model_validator(mode="after")
    def _validate_junit_out(self) -> Self:
        if self.report == "junit" and "{out}" not in self.run:
            msg = "La sección acceptance con report='junit' debe incluir '{out}' en run"
            raise ValueError(msg)
        return self


class ProfileVerification(BaseModel):
    """Lo que garantiza la verificación: solo lo declara el perfil y ningún proyecto lo cambia."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prepare: str = ""
    prepare_inherit: list[str] = Field(default_factory=list)
    prepare_ask: dict[str, str] = Field(default_factory=dict)
    prepare_home_files: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    inherit: list[str] = Field(default_factory=list)
    reserved: list[str] = Field(default_factory=list)
    plugin_variable: str = ""
    plugins: list[str] = Field(default_factory=list)
    plugins_command: str = ""
    files_dir_command: str = ""
    files: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_files_have_a_destination(self) -> Self:
        if self.files and not self.files_dir_command:
            raise ValueError("verification.files necesita files_dir_command")
        return self

    @model_validator(mode="after")
    def _validate_plugins_have_a_variable(self) -> Self:
        if (self.plugins or self.plugins_command) and not self.plugin_variable:
            raise ValueError("verification.plugins necesita plugin_variable")
        return self


class ImpostorConfig(BaseModel):
    """Lines that mark a stub, and the constants that replace them to prove a test asserts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: str = Field(min_length=1)
    replacements: list[str] = Field(min_length=1)

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, pattern: str) -> str:
        return _validate_regexes([pattern])[0]


class ProfileOracle(BaseModel):
    """What makes the sealed driver a valid oracle: only the profile declares it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    link: MapLinkKind = "name"
    red_kinds: list[RedKind] = Field(default_factory=list)
    impostors: ImpostorConfig | None = None


class ProjectVerification(BaseModel):
    """Lo que un proyecto añade a la verificación de su perfil, sin poder quitarle nada."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    env: dict[str, str] = Field(default_factory=dict)
    inherit: list[str] = Field(default_factory=list)
    prepare_inherit: list[str] = Field(default_factory=list)


class ControlConfig(BaseModel):
    """Control determinista de verificación."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    level: ControlLevel
    run: str = Field(min_length=1)
    report: ReportFormat
    mode: ControlMode = "block"
    allow_empty: bool = False
    timeout_s: int = Field(default=600, gt=0)
    when_exists: str | None = None

    @model_validator(mode="after")
    def _validate_junit_out(self) -> Self:
        if self.report == "junit" and "{out}" not in self.run:
            msg = f"El control '{self.id}' con report='junit' debe incluir '{{out}}' en run"
            raise ValueError(msg)
        return self

    @field_validator("when_exists")
    @classmethod
    def _validate_when_exists(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value.strip())
        if not value.strip() or path.is_absolute() or ".." in path.parts:
            msg = f"when_exists debe ser una ruta relativa dentro del proyecto: '{value}'"
            raise ValueError(msg)
        return path.as_posix()


class BackendConfig(BaseModel):
    """Configuración del backend de agente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: BackendKind = "claude-cli"
    model: str = DEFAULT_MODEL
    auth: BackendAuth = "subscription"
    max_turns: int = Field(default=40, gt=0)
    timeout_s: int = Field(default=1800, gt=0)

    @field_validator("model")
    @classmethod
    def _validate_full_model_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            msg = "backend.model no puede estar vacío"
            raise ValueError(msg)
        lowered = cleaned.lower()
        if lowered in _SHORT_MODEL_ALIASES or lowered.endswith("-latest"):
            msg = f"backend.model no admite alias cortos ('{value}'); usa el ID completo"
            raise ValueError(msg)
        if "-" not in cleaned or not any(ch.isdigit() for ch in cleaned):
            msg = f"backend.model debe ser un ID completo con versión ('{value}')"
            raise ValueError(msg)
        return cleaned


class LimitsConfig(BaseModel):
    """Límites de intentos y tamaño de error."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=3, gt=0)
    max_driver_attempts: int = Field(default=2, gt=0)
    error_max_chars: int = Field(default=4000, gt=0)


class Profile(BaseModel):
    """Perfil de lenguaje y herramientas distribuido con Lictor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: ProfileStatus = "stable"
    detect: list[str] = Field(min_length=1)
    paths: PathsConfig
    acceptance: AcceptanceConfig
    controls: list[ControlConfig]
    suppressions: list[str] = Field(default_factory=list)
    banned_patterns: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    verification: ProfileVerification = Field(default_factory=ProfileVerification)
    oracle: ProfileOracle = Field(default_factory=ProfileOracle)
    agent_tools: list[str] = Field(default_factory=list)
    agent_commands: list[str] = Field(default_factory=list)
    hints: str = ""
    gitignore: list[str] = Field(default_factory=list)

    @field_validator("controls")
    @classmethod
    def _check_unique_controls(cls, controls: list[ControlConfig]) -> list[ControlConfig]:
        return _validate_unique_control_ids(controls)

    @field_validator("banned_patterns")
    @classmethod
    def _check_regexes(cls, patterns: list[str]) -> list[str]:
        return _validate_regexes(patterns)


class LictorConfig(BaseModel):
    """Lo que decide un proyecto (lictor.json); las defensas vienen de su perfil."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "0.1"
    profile: str = Field(min_length=1)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    paths: PathsConfig
    acceptance: AcceptanceConfig
    controls: list[ControlConfig]
    verification: ProjectVerification = Field(default_factory=ProjectVerification)
    agent_tools: list[str] = Field(default_factory=list)
    agent_commands: list[str] = Field(default_factory=list)
    hints: str = ""
    gitignore: list[str] = Field(default_factory=list)

    @field_validator("controls")
    @classmethod
    def _check_unique_controls(cls, controls: list[ControlConfig]) -> list[ControlConfig]:
        return _validate_unique_control_ids(controls)
