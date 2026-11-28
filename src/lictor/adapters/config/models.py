import re
import shlex
from collections.abc import Sequence
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Annotated, Any, Final, Literal, Self

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

from lictor.domain.api import reference_name_problem
from lictor.domain.controls import (
    CommandControl,
    Control,
    ReaderControl,
    allowed_by,
    complexity_limit_message,
    reader_problems,
)
from lictor.domain.conventions import MAX_CONVENTIONS_CHARS, too_long_message
from lictor.domain.stages import ATTEMPT, Stage

ControlLevel = Literal["L1", "L2", "L4", "L5"]
ReportFormat = Literal[
    "exit-code", "junit", "mutation", "coverage", "complexity", "duplication", "api"
]
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


def driver_hints_message(profile: str) -> str:
    """Why `lictor.json` can't declare the driver's instructions when its profile does."""
    return (
        f"lictor.json no puede declarar acceptance.driver_hints: el perfil {profile} ya las "
        "declara, son las instrucciones de su oráculo y las mejora en cada versión, y una copia "
        "las tapa. Bórralo; lo propio del proyecto va en hints."
    )


def prepare_message(profile: str) -> str:
    """Why `lictor.json` can't declare how the environment is built when its profile does."""
    return (
        f"lictor.json no puede declarar verification.prepare: el perfil {profile} ya construye el "
        "entorno de verificación, y el hash de ese entorno es lo que vigila cada orden. "
        "Sustituirlo cambiaría lo que el perfil garantiza. Bórralo."
    )


def code_exclude_message(profile: str) -> str:
    """Why a project can't exclude code from paths its profile declares: it would weaken the
    refactor's gate (ADR-0010, amendment of 0.23.0)."""
    return (
        f"lictor.json no puede declarar code_exclude: el perfil {profile} ya dice dónde está el "
        "código, y excluir de ahí debilitaría la puerta del refactor, L0-alcance. Bórralo; a "
        "code_paths solo se le pueden añadir rutas."
    )


def profile_only_message(field: str, why: str = "", instead: str = "") -> str:
    """Why `lictor.json` can't declare a field only the profile declares, even when it declares
    none (ADR-0020): one sentence for all of them, with what it is and where the project's goes."""
    reason = f" ({why})" if why else ""
    rest = f"; {instead}" if instead else ""
    return (
        f"lictor.json no puede declarar {field}: solo lo declara el perfil{reason}, y ningún "
        f"proyecto lo escribe. Bórralo{rest}."
    )


def needs_plugins_message() -> str:
    """Why `lictor.json` can't declare what the acceptance needs: the profile's driver uses it."""
    return profile_only_message(
        "acceptance.needs_plugins", "es lo que necesita su driver, que lo usa"
    )


def agent_commands_message() -> str:
    """Why `lictor.json` can't list the agent's commands: they come from the controls (F83)."""
    return (
        "lictor.json no puede declarar agent_commands: las órdenes del agente salen de los "
        "controles que bloquean y de la aceptación. Bórralo; lo que la tuya cambie va en el "
        "agent_run o el agent_fix de su control."
    )


def stale_agent_command_message(control_id: str, field: str, inherited: str) -> str:
    """What a project is told when it changes a control's run and inherits its agent's command."""
    what = "arreglar" if field == "agent_fix" else "comprobar"
    return (
        f"El control '{control_id}' cambia lo que ejecuta y hereda {field} del perfil "
        f"(`{inherited}`), que puede no {what} lo mismo: escríbelo en lictor.json, o null si el "
        "agente no debe usarlo."
    )


def survey_message() -> str:
    """Why `lictor.json` can't declare the survey: it is the profile's reading of its tools."""
    return profile_only_message(
        "survey",
        "es cómo lee lo que ve cada control",
        "lo que la persona acepta que no se vea va en blind_spots.accepted",
    )


class AcceptanceConfig(BaseModel):
    """Configuración de la suite de aceptación (L3) que decide el proyecto."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run: str = Field(min_length=1)
    timeout_s: int = Field(default=600, gt=0)
    failure_exit_codes: list[int] = Field(default=[1], min_length=1)
    # The driver's instructions, only when the profile declares none (ADR-0020): `resolve_defences`
    # takes the profile's, and refuses these beside them.
    driver_hints: str = ""

    @model_validator(mode="before")
    @classmethod
    def _reject_report(cls, data: object) -> object:
        """The acceptance always reads a JUnit report: a field of one value decides nothing."""
        if isinstance(data, dict) and "report" in data:
            raise ValueError(acceptance_report_message())
        return data

    @model_validator(mode="before")
    @classmethod
    def _reject_needs_plugins(cls, data: object) -> object:
        """Only the profile's acceptance has them: its driver uses them."""
        if (
            isinstance(data, dict)
            and "needs_plugins" in data
            and "needs_plugins" not in cls.model_fields
        ):
            raise ValueError(needs_plugins_message())
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

    # The plugins its driver can't do without, each with how a person adds it (ADR-0015).
    needs_plugins: dict[str, str] = Field(default_factory=dict)


class SurveyConfig(BaseModel):
    """The profile's command that reads what each verification command sees (ADR-0015)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run: str = Field(min_length=1)
    # The command each known remote hook of pre-commit runs, by its id (F76): Lictor reads the
    # CI, and only the profile knows which hooks are its tools'.
    ci_hooks: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_placeholders(self) -> Self:
        split_command(self.run, "survey")
        for placeholder in ("{in}", "{out}"):
            if placeholder not in self.run:
                msg = f"survey.run debe incluir '{placeholder}'"
                raise ValueError(msg)
        return self


class BlindSpotsConfig(BaseModel):
    """What the person accepts that no control sees, only counted: paths, and «file: command»
    for a step of the CI."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: list[str] = Field(default_factory=list)


_SHA256 = re.compile(r"[0-9a-f]{64}")


class ConventionsConfig(BaseModel):
    """The project's rules of code, which the agents get (F79), and the rule files they came from.

    `reviewed` is the hash of each rule file's content as the person read it, written by
    `lictor conventions --reviewed`: only `doctor` reads it, so it stays out of the fingerprint.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = ""
    reviewed: dict[str, str] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def _check_length(cls, text: str) -> str:
        if len(text) > MAX_CONVENTIONS_CHARS:
            raise ValueError(too_long_message(len(text)))
        return text

    @field_validator("reviewed")
    @classmethod
    def _check_digests(cls, reviewed: dict[str, str]) -> dict[str, str]:
        for path, digest in reviewed.items():
            if not _SHA256.fullmatch(digest):
                msg = (
                    f"conventions.reviewed['{path}'] no es un sha256 en hexadecimal: lo escribe "
                    "`lictor conventions --reviewed`, no se escribe a mano"
                )
                raise ValueError(msg)
        return reviewed


class ApiConfig(BaseModel):
    """What the person accepts to break of the public API that existed (ADR-0017).

    Qualified names, as the report names them: a break of one of them, or of a name under it, is
    told apart and doesn't fail the control.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: list[str] = Field(default_factory=list)
    # Where the work will be integrated (`origin/main`): every run of it is compared with where it
    # left that branch, not with its own start (H4 of 0.20.0). Without it, the run's start.
    base: str | None = None

    @field_validator("base")
    @classmethod
    def _a_reference(cls, value: str | None) -> str | None:
        if value is not None and (problem := reference_name_problem(value)) is not None:
            raise ValueError(problem)
        return value


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


class StubConstantConfig(BaseModel):
    """One constant a stub returns in a run of the stubs' impostors (ADR-0012)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)  # what replaces the stub's line; may name the pattern's groups
    name: str = Field(min_length=1)  # how messages, measures and the report name it
    double: bool = False  # it answers what a test does with a result with another double


class ImpostorConfig(BaseModel):
    """Lines that mark a stub, and the constants that replace them to prove a test asserts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: str = Field(min_length=1)
    replacements: list[StubConstantConfig] = Field(min_length=1)

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, pattern: str) -> str:
        return _validate_regexes([pattern])[0]

    @model_validator(mode="after")
    def _check_replacements(self) -> Self:
        names = [r.name for r in self.replacements]
        if len(set(names)) != len(names):
            raise ValueError("oracle.impostors.replacements repite un nombre")
        if self.replacements[0].double:
            # The first one is the no-op the stubs keep when the interface judges a test.
            raise ValueError("la primera de oracle.impostors.replacements no puede ser un doble")
        return self


_MODE_NAME = r"^[a-z][a-z0-9_-]*$"


class InterfaceImpostorConfig(BaseModel):
    """How the profile's impostor of the interface is told what to do (ADR-0011).

    Each run of the acceptance gets `variable` with one mode: `control` wraps the interface and
    alters nothing, and each of `modes` alters what it returns to the driver. `gherkin` names the
    variable of the impostor of the Gherkin's value (ADR-0016), told the same modes: with it, a
    test the interface can't prove has each value of its Then changed in turn.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    variable: str = Field(pattern=r"^[A-Z_][A-Z0-9_]*$")
    gherkin: str | None = Field(default=None, pattern=r"^[A-Z_][A-Z0-9_]*$")
    # Each mode names a directory of evidence: `interfaz-<modo>`.
    control: str = Field(pattern=_MODE_NAME)
    modes: list[Annotated[str, Field(pattern=_MODE_NAME)]] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_distinct_modes(self) -> Self:
        names = [self.control, *self.modes]
        if len(set(names)) != len(names):
            raise ValueError("oracle.interface_impostor repite un modo")
        if self.gherkin == self.variable:
            raise ValueError("oracle.interface_impostor usa la misma variable para el Gherkin")
        return self

    @property
    def variables(self) -> list[str]:
        """The variables the impostors are told through, which belong to them alone."""
        return [self.variable, *([self.gherkin] if self.gherkin is not None else [])]


class ProfileOracle(BaseModel):
    """What makes the sealed driver a valid oracle: only the profile declares it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    link: MapLinkKind = "name"
    red_kinds: list[RedKind] = Field(default_factory=list)
    impostors: ImpostorConfig | None = None
    # The driver declares each stub's constants on its line, for a profile without a plugin: its
    # tests are judged by what they do with them, not by a failure's class (ADR-0021).
    declared_impostors: bool = False
    interface_impostor: InterfaceImpostorConfig | None = None


class ProjectVerification(BaseModel):
    """Lo que un proyecto añade a la verificación de su perfil, sin poder quitarle nada.

    `prepare` solo cuando el perfil no construye el entorno (ADR-0020): entonces lo construye el
    proyecto, con su hash.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    prepare: str = ""

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


LIMIT_DESCRIPTION = (
    "La complejidad ciclomática que ninguna función puede superar; solo, y siempre, con "
    "report='complexity' (ADR-0013)."
)

AGENT_RUN_DESCRIPTION = (
    "La orden con la que el agente comprueba o mide este control por su cuenta, tal como la "
    "ejecutaría. Sin ella, el agente comprueba un control que bloquea con su run sin el informe. "
    "Con min_score, el prompt le dice el mínimo y esta orden. La verificación nunca la ejecuta, "
    "y tiene que estar permitida por agent_tools. Quien cambia run la escribe también, o null."
)
AGENT_FIX_DESCRIPTION = (
    "La orden con la que el agente deja hecho lo que este control comprueba (un formateador), "
    "que se le da solo mientras el control bloquea en cada intento. La verificación nunca la "
    "ejecuta, y tiene que estar permitida por agent_tools. Quien cambia run la escribe también, "
    "o null."
)


DRIVER_DESCRIPTION = (
    "Con false, el commit del driver no pasa este control antes del sello (G3): para un control "
    "que un driver correcto no puede pasar, como un umbral propio de cobertura o el largo de línea "
    "contra la línea de un stub. Los intentos lo siguen pasando."
)


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
    driver: bool = Field(default=True, description=DRIVER_DESCRIPTION)
    when_exists: str | None = None
    # A plugin the control needs, as the profile lists them: without it the control is skipped.
    when_plugin: str | None = Field(default=None, min_length=1)
    # The lowest score that passes, in percent; only a mutation or a coverage report has one.
    min_score: float | None = Field(default=None, ge=0, le=100)
    agent_run: str | None = Field(default=None, min_length=1, description=AGENT_RUN_DESCRIPTION)
    agent_fix: str | None = Field(default=None, min_length=1, description=AGENT_FIX_DESCRIPTION)
    limit: int | None = Field(default=None, gt=0, description=LIMIT_DESCRIPTION)

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
        if (self.report == "complexity") != (self.limit is not None):
            raise ValueError(complexity_limit_message(self.id, self.report))
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
    driver: bool = Field(default=True, description=DRIVER_DESCRIPTION)
    when_plugin: str | None = Field(default=None, min_length=1)
    min_score: float | None = Field(default=None, ge=0, le=100)
    agent_run: str | None = Field(default=None, min_length=1, description=AGENT_RUN_DESCRIPTION)

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
        if "reads" in data or "adds" in data:
            return "reads"
        return "run" if "run" in data else None
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
            reads=control.reads, adds=control.adds, report=control.report, **_shared(control)
        )
    return CommandControl(
        run=control.run,
        report=control.report,
        empty_exit_codes=tuple(control.empty_exit_codes),
        timeout_s=control.timeout_s,
        when_exists=control.when_exists,
        agent_fix=control.agent_fix,
        limit=control.limit,
        **_shared(control),
    )


def _shared(control: CommandControlConfig | ReaderControlConfig) -> dict[str, Any]:
    """What both shapes of a control have, as the domain names it."""
    return {
        "id": control.id,
        "level": control.level,
        "mode": control.mode,
        "stage": control.stage,
        "when_plugin": control.when_plugin,
        "min_score": control.min_score,
        "agent_run": control.agent_run,
        "checks_driver": control.driver,
    }


def _check_readers(controls: Sequence[CommandControlConfig | ReaderControlConfig]) -> None:
    """The rules of the pairing, for whoever builds a configuration or a profile (ADR-0008)."""
    problems = reader_problems([domain_control(control) for control in controls])
    if problems:
        raise ValueError("\n".join(problems))


_AGENT_FIELDS: Final = ("agent_run", "agent_fix")


def agent_run_problems(
    controls: Sequence[CommandControlConfig | ReaderControlConfig], agent_tools: Sequence[str]
) -> list[str]:
    """The `agent_run` and `agent_fix` of each control the agent's tools wouldn't let it run.

    What the backend allows (`allowed_by`), so the prompt never names a command it denies (F55,
    F83).
    """
    return [
        f"El {name} del control '{c.id}' ({command}) no lo permite agent_tools: el agente "
        "no podría ejecutarlo. Escribe una orden que empiece como una de sus reglas Bash(...)."
        for c in controls
        for name in _AGENT_FIELDS
        if (command := getattr(c, name, None)) is not None and not allowed_by(command, agent_tools)
    ]


def _unrunnable(
    control: CommandControlConfig | ReaderControlConfig, agent_tools: Sequence[str]
) -> dict[str, None]:
    """The agent's commands of a control its tools don't allow, to drop them."""
    return {
        name: None
        for name in _AGENT_FIELDS
        if (command := getattr(control, name, None)) is not None
        and not allowed_by(command, agent_tools)
    }


def _no_default(schema: JsonDict) -> None:
    schema.pop("default", None)


def _unwritten(**constraints: Any) -> Any:
    """A field that is optional to write but never null: its unwritten value is never read.

    Pydantic doesn't validate a default, so the schema shows none, and the type has no None.
    """
    return Field(default=None, json_schema_extra=_no_default, **constraints)


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
    # Written is in `model_fields_set`: the unwritten value of these is never read, and none
    # takes null, which would mean nothing. Only the last three take it, to clear a value.
    level: ControlLevel = _unwritten()
    run: str = _unwritten(min_length=1)
    reads: str = _unwritten(
        min_length=1,
        description="El id del control, con report junit, cuya ejecución lee este control.",
    )
    adds: str = _unwritten(min_length=1, description=ADDS_DESCRIPTION)
    report: ReportFormat = _unwritten()
    mode: ControlMode = _unwritten()
    stage: Stage = _unwritten()
    empty_exit_codes: list[int] = _unwritten()
    timeout_s: int = _unwritten(gt=0)
    driver: bool = _unwritten(description=DRIVER_DESCRIPTION)
    when_exists: str | None = None
    when_plugin: str | None = Field(default=None, min_length=1)
    min_score: float | None = Field(default=None, ge=0, le=100)
    agent_run: str | None = Field(default=None, min_length=1, description=AGENT_RUN_DESCRIPTION)
    agent_fix: str | None = Field(default=None, min_length=1, description=AGENT_FIX_DESCRIPTION)
    limit: int | None = Field(default=None, gt=0, description=LIMIT_DESCRIPTION)

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
    # Versions that no longer exist, and what this one changes: a file that asks for one asked
    # for its rules, and getting others under the same name would change them unawares.
    retired_versions: dict[str, str] = Field(default_factory=dict)
    status: ProfileStatus = "stable"
    # The files that tell a project is of this profile. A profile without any is never detected.
    detect: list[str] = Field(default_factory=list)
    # A profile that knows no layout declares neither: the project writes both (ADR-0020).
    paths: PathsConfig | None = None
    acceptance: ProfileAcceptance | None = None
    controls: list[ControlConfig]
    suppressions: list[str] = Field(default_factory=list)
    banned_patterns: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    driver_exempt_paths: list[str] = Field(default_factory=list)
    verification: ProfileVerification = Field(default_factory=ProfileVerification)
    oracle: ProfileOracle = Field(default_factory=ProfileOracle)
    survey: SurveyConfig | None = None
    agent_tools: list[str] = Field(default_factory=list)
    hints: str = ""
    # The goals every refactor of the profile is told to reach (ADR-0010): only the profile's.
    refactor_hints: str = ""
    # Where the code is: a refactor may change only what its scenario changed there (F84).
    code_paths: list[str] = Field(default_factory=list)
    # The files where a project of this profile keeps rules for people and agents (F79), at any
    # depth: `doctor` tells the ones whose content the person hasn't reviewed into `conventions`.
    rule_files: list[str] = Field(default_factory=list)
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
    def _validate_retired_versions(self) -> Self:
        if self.version in self.retired_versions:
            msg = f"El perfil {self.name} retira su propia versión, {self.version}"
            raise ValueError(msg)
        return self

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

    @model_validator(mode="after")
    def _validate_needs_can_be_listed(self) -> Self:
        needs = self.acceptance.needs_plugins if self.acceptance is not None else {}
        if needs and not self.verification.plugins_command:
            msg = "acceptance.needs_plugins necesita verification.plugins_command para comprobarlo"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_impostors_have_red_kinds(self) -> Self:
        """Without the class of each failure rule C rejects right tests too: a profile without
        the plugin declares the driver's own constants instead (ADR-0020, ADR-0021)."""
        if self.oracle.impostors is not None and not self.oracle.red_kinds:
            raise ValueError("oracle.impostors necesita oracle.red_kinds")
        if self.oracle.impostors is not None and self.oracle.declared_impostors:
            raise ValueError(
                "oracle.impostors y oracle.declared_impostors son dos fuentes de las constantes de "
                "los stubs: un perfil declara una"
            )
        return self

    @model_validator(mode="after")
    def _validate_impostor_variable(self) -> Self:
        """The impostor's variable belongs to it alone: nothing else may fix or inherit it."""
        impostor = self.oracle.interface_impostor
        if impostor is None:
            return self
        verification = self.verification
        taken = {*verification.env, *verification.inherit, verification.plugin_variable}
        for variable in impostor.variables:
            if variable in taken:
                msg = f"oracle.interface_impostor usa {variable}, que fija verification"
                raise ValueError(msg)
            if not any(fnmatchcase(variable, p) for p in verification.reserved):
                msg = f"oracle.interface_impostor usa {variable}, que no está en reserved"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_agent_runs(self) -> Self:
        problems = agent_run_problems(self.controls, self.agent_tools)
        if problems:
            raise ValueError("\n".join(problems))
        return self


class RefactorConfig(BaseModel):
    """El paso de refactor tras cada escenario implementado (ADR-0010)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True


def refactor_hints_message() -> str:
    """Why `lictor.json` can't declare the refactor's goals, and where the project's go."""
    return profile_only_message(
        "refactor_hints",
        "son sus objetivos, que mejora en cada versión, y una copia los tapa",
        "lo propio del proyecto va en hints o en conventions.text",
    )


class RetentionConfig(BaseModel):
    """Cuánta evidencia de ejecuciones anteriores se conserva en .lictor/runs/."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Counting the run that starts: it is only read then, so it doesn't decide a resume.
    keep_runs: int = Field(default=10, ge=1)
    # The evidence `lictor retire` keeps in the checkout's .lictor/retired/, counting the new one.
    keep_retired: int = Field(default=10, ge=1)


class ProjectDefences(BaseModel):
    """What a project adds to its profile's defences (ADR-0020): each list joins the profile's.

    `code_exclude` only where the profile declares no code paths. `LictorFile` and `LictorConfig`
    both carry them, as written: `resolve_defences` joins them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    suppressions: list[str] = Field(default_factory=list)
    banned_patterns: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    code_paths: list[str] = Field(default_factory=list)
    code_exclude: list[str] = Field(default_factory=list)

    @field_validator("banned_patterns")
    @classmethod
    def _check_regexes(cls, patterns: list[str]) -> list[str]:
        return _validate_regexes(patterns)


class LictorConfig(ProjectDefences):
    """Lo que decide un proyecto (lictor.json); las defensas vienen de su perfil."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "0.1"
    profile: str = Field(min_length=1)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    refactor: RefactorConfig = Field(default_factory=RefactorConfig)
    blind_spots: BlindSpotsConfig = Field(default_factory=BlindSpotsConfig)
    conventions: ConventionsConfig = Field(default_factory=ConventionsConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    paths: PathsConfig
    acceptance: AcceptanceConfig
    controls: list[ControlConfig]
    verification: ProjectVerification = Field(default_factory=ProjectVerification)
    agent_tools: list[str] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def _validate_api_base(self) -> Self:
        """`api.base` is what the API control compares with: without one it decides nothing, and
        a reference that doesn't resolve would stop a run for it (M3 of 0.20.0)."""
        if self.api.base is not None and not any(c.report == "api" for c in self.controls):
            raise ValueError(
                f"api.base ({self.api.base!r}) es la base con la que compara el control de la "
                'API, y no hay ninguno activo: quita api.base, o activa el control con report "api"'
            )
        return self


def domain_controls(config: LictorConfig) -> tuple[Control, ...]:
    """The resolved controls as the domain sees them, in the order they were declared.

    This is the only conversion: the run and `lictor check` get what it returns (ADR-0009).
    An `agent_run` or `agent_fix` the agent's tools don't allow is dropped: it would name a command
    the backend denies. One the project writes is refused at load instead (`load_config`).
    """
    return tuple(
        domain_control(control.model_copy(update=_unrunnable(control, config.agent_tools)))
        for control in config.controls
    )


class LictorFile(ProjectDefences):
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
    refactor: RefactorConfig = Field(default_factory=RefactorConfig)
    blind_spots: BlindSpotsConfig = Field(default_factory=BlindSpotsConfig)
    conventions: ConventionsConfig = Field(default_factory=ConventionsConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    paths: PathsConfig | None = None
    acceptance: AcceptanceConfig | None = None
    controls: list[ControlChange] = Field(default_factory=list)
    verification: ProjectVerification = Field(default_factory=ProjectVerification)
    agent_tools: list[str] | None = None
    hints: str | None = None
    gitignore: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_survey(cls, data: object) -> object:
        if isinstance(data, dict) and "survey" in data:
            raise ValueError(survey_message())
        return data

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
