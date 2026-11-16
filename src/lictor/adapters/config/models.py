import re
import shlex
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_validator,
    model_validator,
)
from pydantic.config import JsonDict

from lictor.domain.controls import CommandControl, Control, ReaderControl, reader_problems
from lictor.domain.stages import ATTEMPT, Stage

ControlLevel = Literal["L1", "L2", "L4", "L5"]
ReportFormat = Literal["exit-code", "junit", "mutation", "coverage"]
ControlMode = Literal["block", "measure"]
# When a control runs: in every attempt of a run, or only in `lictor check` (ADR-0007).
ProfileStatus = Literal["stable", "beta", "alpha"]
RedKind = Literal["assertion", "not_implemented", "error"]
MapLinkKind = Literal["name", "scenario"]
BackendKind = Literal["claude-cli", "fake"]
BackendAuth = Literal["subscription", "api_key"]

DEFAULT_MODEL = "claude-sonnet-5-5"

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


def split_command(command: str, owner: str) -> str:
    """Refuse a command `shlex` can't split, naming whose it is: run, it would only raise then."""
    try:
        shlex.split(command)
    except ValueError as exc:
        msg = f"El comando de {owner} no se puede trocear como en una shell ({exc}): {command}"
        raise ValueError(msg) from exc
    return command


def plugin_without_list_message(control_id: str) -> str:
    """Why a control can't need a plugin in a profile that can't list what the environment loads."""
    return (
        f"El control '{control_id}' declara when_plugin, pero su perfil no tiene "
        "verification.plugins_command: sin él nadie sabe qué plugins carga el entorno, y el "
        "control no puede depender de uno. Quita when_plugin."
    )


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


def driver_hints_message() -> str:
    """Why `lictor.json` can't declare the driver's instructions, and where the project's go."""
    return (
        "lictor.json no puede declarar acceptance.driver_hints: esas instrucciones son del "
        "perfil, que las mejora en cada versión, y una copia las tapa. Bórralo; lo propio del "
        "proyecto va en hints."
    )


class AcceptanceConfig(BaseModel):
    """Configuración de la suite de aceptación (L3) que decide el proyecto."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run: str = Field(min_length=1)
    timeout_s: int = Field(default=600, gt=0)
    failure_exit_codes: list[int] = Field(default=[1], min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_report(cls, data: object) -> object:
        """The acceptance always reads a JUnit report: a field of one value decides nothing."""
        if isinstance(data, dict) and "report" in data:
            raise ValueError(acceptance_report_message())
        return data

    @model_validator(mode="before")
    @classmethod
    def _reject_driver_hints(cls, data: object) -> object:
        """Only the profile's acceptance has them: in `lictor.json` they'd hide its fixes."""
        if (
            isinstance(data, dict)
            and "driver_hints" in data
            and "driver_hints" not in cls.model_fields
        ):
            raise ValueError(driver_hints_message())
        return data

    @model_validator(mode="after")
    def _validate_failure_exit_codes(self) -> Self:
        if 0 in self.failure_exit_codes:
            raise ValueError("acceptance.failure_exit_codes no puede incluir 0")
        return self

    @model_validator(mode="after")
    def _validate_junit_out(self) -> Self:
        split_command(self.run, "acceptance")
        if "{out}" not in self.run:
            msg = "La sección acceptance debe incluir '{out}' en run: ahí deja su informe JUnit"
            raise ValueError(msg)
        return self


class ProfileAcceptance(AcceptanceConfig):
    """The acceptance of a profile, with the instructions every driver of the profile gets."""

    driver_hints: str = ""


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


def acceptance_report_message() -> str:
    """Say why `acceptance.report` is refused: the oracle needs cases, which only JUnit gives."""
    return (
        "La aceptación siempre lee un informe JUnit, el único que da los casos que se emparejan "
        "con los escenarios: quita acceptance.report."
    )


def allow_empty_message(
    control_id: str, profile_codes: list[int] | None, reads: str | None = None
) -> str:
    """Say what replaces `allow_empty`, with the profile's own codes when they are known.

    A reader has no codes of its own: the codes of the control whose run it reads apply.
    """
    if reads is not None:
        where = (
            f". El control '{control_id}' del perfil lee la ejecución de '{reads}' y no tiene "
            f"empty_exit_codes: valen los de '{reads}'"
            + (f", que declara {profile_codes}" if profile_codes is not None else "")
        )
    elif profile_codes is not None:
        where = f"; el control '{control_id}' del perfil declara {profile_codes}"
    else:
        where = ""
    return (
        f"El control '{control_id}' usa 'allow_empty', que ya no existe: declara los códigos de "
        f"salida de una ejecución sin casos en 'empty_exit_codes'{where}. Una ejecución a medias "
        "no se podrá reanudar tras editar lictor.json: termínala antes o empieza otra con --new."
    )


def _reject_allow_empty_field(data: object) -> object:
    if isinstance(data, dict) and "allow_empty" in data:
        raise ValueError(allow_empty_message(str(data.get("id", "")), None))
    return data


def _check_min_score(control_id: str, report: str, min_score: float | None) -> None:
    if min_score is not None and report not in ("mutation", "coverage"):
        msg = (
            f"El control '{control_id}' declara min_score, que solo vale con report='mutation' "
            "o 'coverage'"
        )
        raise ValueError(msg)


class CommandControlConfig(BaseModel):
    """Un control que ejecuta su propio comando (`run`) y se juzga por lo que deja."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    level: ControlLevel
    run: str = Field(min_length=1)
    report: ReportFormat
    mode: ControlMode = "block"
    stage: Stage = ATTEMPT
    empty_exit_codes: list[int] = Field(default_factory=list)
    timeout_s: int = Field(default=600, gt=0)
    when_exists: str | None = None
    # A plugin the control needs, as the profile lists them: without it the control is skipped.
    when_plugin: str | None = Field(default=None, min_length=1)
    # The lowest score that passes, in percent; only a mutation or a coverage report has one.
    min_score: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def _reject_allow_empty(cls, data: object) -> object:
        return _reject_allow_empty_field(data)

    @model_validator(mode="after")
    def _validate_junit_out(self) -> Self:
        split_command(self.run, f"run del control '{self.id}'")
        if self.report != "exit-code" and "{out}" not in self.run:
            msg = f"El control '{self.id}' con report='{self.report}' debe incluir '{{out}}' en run"
            raise ValueError(msg)
        _check_min_score(self.id, self.report, self.min_score)
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


class ReaderControlConfig(BaseModel):
    """Un control que no ejecuta nada: puntúa la cobertura de la ejecución de otro (ADR-0008).

    `reads` nombra el control que ejecuta, con report "junit", cuya ejecución lee; `adds` son
    los argumentos que ese control recibe al final de su comando, con el `{out}` de este.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    level: ControlLevel
    reads: str = Field(min_length=1)
    adds: str = Field(min_length=1)
    report: Literal["coverage"]
    mode: ControlMode = "block"
    stage: Stage = ATTEMPT
    when_plugin: str | None = Field(default=None, min_length=1)
    min_score: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def _reject_allow_empty(cls, data: object) -> object:
        return _reject_allow_empty_field(data)

    @model_validator(mode="after")
    def _validate_adds_out(self) -> Self:
        split_command(self.adds, f"adds del control '{self.id}'")
        if "{out}" not in self.adds:
            msg = (
                f"El control '{self.id}' debe incluir '{{out}}' en adds: ahí deja la ejecución "
                f"de '{self.reads}' su informe de cobertura"
            )
            raise ValueError(msg)
        return self


def no_shape_message(control_id: str) -> str:
    """What a control that says neither what it runs nor what it reads is told."""
    return f"El control '{control_id}' no dice qué ejecuta: escribe run, o reads y adds"


def two_shapes_message(control_id: str) -> str:
    """What a control that both runs a command and reads another's run is told."""
    return (
        f"El control '{control_id}' escribe run y también reads o adds: un control ejecuta su "
        "comando (run) o lee la ejecución de otro (reads y adds), no las dos cosas. Quita uno."
    )


def _control_shape(data: object) -> str | None:
    if isinstance(data, dict):
        return "reads" if "reads" in data else "run" if "run" in data else None
    if isinstance(data, ReaderControlConfig):
        return "reads"
    return "run" if isinstance(data, CommandControlConfig) else None


def _require_one_shape(data: object) -> object:
    """Name the control when its shape can't be told: never the error of both branches."""
    if isinstance(data, dict):
        has_run, reads = "run" in data, "reads" in data or "adds" in data
        if has_run and reads:
            raise ValueError(two_shapes_message(str(data.get("id", ""))))
        if not has_run and not reads:
            raise ValueError(no_shape_message(str(data.get("id", ""))))
    return data


# A control runs a command or reads the run of another; the type makes them impossible to mix.
type ControlConfig = Annotated[
    Annotated[CommandControlConfig, Tag("run")] | Annotated[ReaderControlConfig, Tag("reads")],
    Discriminator(_control_shape),
    BeforeValidator(_require_one_shape),
]


def domain_control(control: CommandControlConfig | ReaderControlConfig) -> Control:
    """A resolved control as the domain sees it."""
    if isinstance(control, ReaderControlConfig):
        return ReaderControl(
            id=control.id,
            level=control.level,
            reads=control.reads,
            adds=control.adds,
            report=control.report,
            mode=control.mode,
            stage=control.stage,
            when_plugin=control.when_plugin,
            min_score=control.min_score,
        )
    return CommandControl(
        id=control.id,
        level=control.level,
        run=control.run,
        report=control.report,
        mode=control.mode,
        stage=control.stage,
        empty_exit_codes=tuple(control.empty_exit_codes),
        timeout_s=control.timeout_s,
        when_exists=control.when_exists,
        when_plugin=control.when_plugin,
        min_score=control.min_score,
    )


def _check_readers(controls: Sequence[CommandControlConfig | ReaderControlConfig]) -> None:
    """The rules of the pairing, for whoever builds a configuration or a profile (ADR-0008)."""
    problems = reader_problems([domain_control(control) for control in controls])
    if problems:
        raise ValueError("\n".join(problems))


def _no_default(schema: JsonDict) -> None:
    """A field that is optional to write but never null: its unwritten value is never read."""
    schema.pop("default", None)


ADDS_DESCRIPTION = (
    "Los argumentos que el control que nombra reads recibe al final de su comando, con el {out} "
    "de este control. Se escribe entero: lo que no escribas no se añade, así que conserva lo que "
    "el perfil pone (por ejemplo, el argumento que anula el umbral propio de la herramienta)."
)


class ControlChange(BaseModel):
    """Un control escrito en lictor.json: cambia, quita o añade uno sobre los del perfil.

    Con el `id` de un control del perfil, cambia solo los campos escritos, y `enabled: false` lo
    quita. Con otro `id`, añade un control: uno que ejecuta necesita `level`, `report` y `run`, y
    uno que lee la ejecución de otro, `level`, `report: "coverage"`, `reads` y `adds`. `run`,
    `reads` y `adds` se escriben o no, pero nunca con null.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    enabled: bool = True
    level: ControlLevel | None = None
    # Written is in `model_fields_set`: the default of these three is never read.
    run: str = Field(default="", min_length=1, json_schema_extra=_no_default)
    reads: str = Field(
        default="",
        min_length=1,
        json_schema_extra=_no_default,
        description="El id del control, con report junit, cuya ejecución lee este control.",
    )
    adds: str = Field(
        default="", min_length=1, json_schema_extra=_no_default, description=ADDS_DESCRIPTION
    )
    report: ReportFormat | None = None
    mode: ControlMode | None = None
    stage: Stage | None = None
    empty_exit_codes: list[int] | None = None
    timeout_s: int | None = Field(default=None, gt=0)
    when_exists: str | None = None
    when_plugin: str | None = Field(default=None, min_length=1)
    min_score: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def _reject_allow_empty(cls, data: object) -> object:
        return _reject_allow_empty_field(data)


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
    # Below this the reason and the first failed case no longer fit, and the agent retries blind.
    error_max_chars: int = Field(default=4000, ge=500)


class HintValue(BaseModel):
    """A value `hints` takes from a project file, as `{name}`, or its default if it can't be read.

    `key` is dotted through the tables of a TOML file: `project.requires-python`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str = Field(min_length=1)
    key: str = Field(min_length=1)
    default: str


class Profile(BaseModel):
    """Perfil de lenguaje y herramientas distribuido con Lictor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: ProfileStatus = "stable"
    detect: list[str] = Field(min_length=1)
    paths: PathsConfig
    acceptance: ProfileAcceptance
    controls: list[ControlConfig]
    suppressions: list[str] = Field(default_factory=list)
    banned_patterns: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    driver_exempt_paths: list[str] = Field(default_factory=list)
    verification: ProfileVerification = Field(default_factory=ProfileVerification)
    oracle: ProfileOracle = Field(default_factory=ProfileOracle)
    agent_tools: list[str] = Field(default_factory=list)
    agent_commands: list[str] = Field(default_factory=list)
    hints: str = ""
    # What `{name}` in `hints` stands for, read from the project when the hints are written.
    hint_values: dict[str, HintValue] = Field(default_factory=dict)
    gitignore: list[str] = Field(default_factory=list)

    @field_validator("controls")
    @classmethod
    def _check_unique_controls(cls, controls: list[ControlConfig]) -> list[ControlConfig]:
        return _validate_unique_control_ids(controls)

    @field_validator("banned_patterns")
    @classmethod
    def _check_regexes(cls, patterns: list[str]) -> list[str]:
        return _validate_regexes(patterns)

    @model_validator(mode="after")
    def _validate_plugins_can_be_listed(self) -> Self:
        if not self.verification.plugins_command:
            for control in self.controls:
                if control.when_plugin is not None:
                    raise ValueError(plugin_without_list_message(control.id))
        return self

    @model_validator(mode="after")
    def _validate_readers(self) -> Self:
        _check_readers(self.controls)
        return self


class RetentionConfig(BaseModel):
    """Cuánta evidencia de ejecuciones anteriores se conserva en .lictor/runs/."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Counting the run that starts: it is only read then, so it doesn't decide a resume.
    keep_runs: int = Field(default=10, ge=1)


class LictorConfig(BaseModel):
    """Lo que decide un proyecto (lictor.json); las defensas vienen de su perfil."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "0.1"
    profile: str = Field(min_length=1)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
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

    @model_validator(mode="after")
    def _validate_readers(self) -> Self:
        _check_readers(self.controls)
        return self


def domain_controls(config: LictorConfig) -> tuple[Control, ...]:
    """The resolved controls as the domain sees them, in the order they were declared.

    This is the only conversion: the run and `lictor check` get what it returns (ADR-0009).
    """
    return tuple(domain_control(control) for control in config.controls)


class LictorFile(BaseModel):
    """lictor.json tal como lo escribe la persona: lo que no escribe sale del perfil al cargar.

    Una clave escrita sustituye entera la del perfil, salvo `controls`, que son cambios por `id`
    sobre los del perfil (`ControlChange`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "0.1"
    profile: str = Field(min_length=1)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    paths: PathsConfig | None = None
    acceptance: AcceptanceConfig | None = None
    controls: list[ControlChange] = Field(default_factory=list)
    verification: ProjectVerification = Field(default_factory=ProjectVerification)
    agent_tools: list[str] | None = None
    agent_commands: list[str] | None = None
    hints: str | None = None
    gitignore: list[str] | None = None

    @field_validator("controls")
    @classmethod
    def _check_unique_changes(cls, controls: list[ControlChange]) -> list[ControlChange]:
        seen: set[str] = set()
        for change in controls:
            if change.id in seen:
                msg = f"El control '{change.id}' está escrito dos veces en controls"
                raise ValueError(msg)
            seen.add(change.id)
        return controls
