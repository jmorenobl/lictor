"""What a green of this run can be trusted for, computed from the resolved defences (ADR-0020).

The level answers one question: can a test that doesn't compare with its scenario end green? With
the profile's whole oracle (the stubs' impostors, the classes of red, the link to the scenario
and the impostor of the interface) it can't, for every test Lictor checked: that is `proved`.
With a stubs' impostor and less than that (the basic rule of a profile without a plugin, ADR-0021,
or rule C without the interface's), a test that doesn't read what the interface returns can't, but
one whose comparison is weaker than the scenario's can: that is `read`. Anything else is `basic`,
saying less, never more. What doesn't change
that answer (an environment without a hash, no suppressions, a control without its plugin) is a
gap: the gaps Lictor knows how to compute, each with a stable code and its sentence.

"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

LevelCode = Literal["basico", "resultado_leido", "resultado_probado"]

BASIC: Final[LevelCode] = "basico"
READ: Final[LevelCode] = "resultado_leido"
PROVED: Final[LevelCode] = "resultado_probado"

_LEVEL_NAMES: Final[Mapping[LevelCode, str]] = {
    BASIC: "básico",
    READ: "resultado leído",
    PROVED: "resultado probado",
}

# What the level itself leaves unchecked: one sentence each, never repeated as a gap.
_LEVEL_LIMITS: Final[Mapping[str, str]] = {
    BASIC: (
        "No se comprueba que el test compare nada: uno sin aserción falla en el rojo porque la "
        "interfaz no está implementada, y pasa después con cualquier código"
    ),
    READ: (
        "No se comprueba que el test compare con el valor del escenario: uno que copia lo que "
        "devuelve el código, o cuya comparación es más débil que la igualdad y las dos constantes "
        "rompen, pasa. Un test que no consta que llegue a un stub se acepta sin comprobar, y se "
        "dice"
    ),
    PROVED: (
        "No se comprueba que el test compare con el valor del escenario: un test que copia en "
        "el assert lo que devuelve el código pasa"
    ),
}

RED_WITHOUT_KIND: Final = "rojo_sin_clase"
LINK_BY_NAME: Final = "enlace_por_nombre"
MET_WITHOUT_PROOF: Final = "ya_cumplido_sin_prueba"
VALUE_UNWEIGHED: Final = "valor_sin_peso"
ENV_UNHASHED: Final = "entorno_sin_hash"
NO_SUPPRESSIONS: Final = "sin_supresiones"
NO_CONTROLS: Final = "sin_controles"
NO_SURVEY: Final = "sin_lectura_de_controles"
CONTROL_WITHOUT_PLUGIN: Final = "control_sin_plugin"
PLUGINS_UNCHECKED: Final = "plugins_sin_comprobar"

_GAP_TEXTS: Final[Mapping[str, str]] = {
    RED_WITHOUT_KIND: (
        "Cualquier fallo cuenta como rojo, también uno de compilación o de importación"
    ),
    LINK_BY_NAME: (
        "El test se empareja con su escenario por el nombre: no se comprueba que lo ejecute"
    ),
    MET_WITHOUT_PROOF: (
        "Un escenario que el código ya cumple no se prueba: el driver se rechaza hasta agotar sus "
        "intentos, y el run para para que lo registre una persona"
    ),
    VALUE_UNWEIGHED: "No se comprueba que cada valor del Gherkin llegue a una aserción",
    ENV_UNHASHED: (
        "El entorno de verificación no se construye ni se vigila: lo que se hereda (cachés, "
        "{env_dir}) puede cambiar entre órdenes"
    ),
    NO_SUPPRESSIONS: (
        "No se rechaza ninguna supresión: añade las de tu lenguaje en suppressions y "
        "banned_patterns"
    ),
    NO_CONTROLS: "Un verde solo pasó la aceptación: ningún control bloquea un intento",
    NO_SURVEY: (
        "No se sabe qué ve cada control: «Lo que los controles no ven» y «Cambios que ningún "
        "control ejecutó» no se calculan"
    ),
    PLUGINS_UNCHECKED: (
        "No se comprobó qué controles se saltan por falta de su plugin: el entorno no se construyó"
    ),
}


@dataclass(frozen=True)
class ProofDefences:
    """The resolved defences the level and its gaps are computed from.

    `plugin_controls` are the `attempt` controls that only run with a plugin, as `(id, plugin)`.
    """

    link: str
    red_kinds: bool
    stub_rule_c: bool  # the profile's constants, judged by rule C (ADR-0012)
    declared_stubs: bool  # the driver's constants, judged by the basic rule (ADR-0021)
    interface_impostor: bool
    gherkin: bool
    env_hashed: bool
    suppressions: bool
    blocking_controls: bool
    survey: bool
    plugin_controls: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Gap:
    """Something Lictor knows this run doesn't check. `control` and `plugin` only for a control
    that doesn't run without its plugin."""

    code: str
    control: str = ""
    plugin: str = ""


@dataclass(frozen=True)
class ProofLevel:
    level: LevelCode
    gaps: tuple[Gap, ...] = ()


def proof_level(defences: ProofDefences, installed: Collection[str] | None) -> ProofLevel:
    """The level of a run with these defences, and its gaps.

    `installed` is what the verification environment loads, or None when it wasn't built: then
    the controls that need a plugin can't be judged, and that is a gap of its own.
    """
    return ProofLevel(_level(defences), _gaps(defences, installed))


def _level(defences: ProofDefences) -> LevelCode:
    """Proved needs rule C: the basic rule doesn't know a red's class, whatever else is there."""
    proved = (
        defences.stub_rule_c
        and defences.red_kinds
        and defences.link == "scenario"
        and defences.interface_impostor
    )
    if proved:
        return PROVED
    return READ if defences.stub_rule_c or defences.declared_stubs else BASIC


def _gaps(defences: ProofDefences, installed: Collection[str] | None) -> tuple[Gap, ...]:
    flags = (
        (RED_WITHOUT_KIND, not defences.red_kinds),
        (LINK_BY_NAME, defences.link != "scenario"),
        (MET_WITHOUT_PROOF, not defences.interface_impostor),
        (VALUE_UNWEIGHED, defences.interface_impostor and not defences.gherkin),
        (ENV_UNHASHED, not defences.env_hashed),
        (NO_SUPPRESSIONS, not defences.suppressions),
        (NO_CONTROLS, not defences.blocking_controls),
        (NO_SURVEY, not defences.survey),
    )
    gaps = [Gap(code) for code, applies in flags if applies]
    if installed is None:
        if defences.plugin_controls:
            gaps.append(Gap(PLUGINS_UNCHECKED))
    else:
        gaps.extend(
            Gap(CONTROL_WITHOUT_PLUGIN, control, plugin)
            for control, plugin in defences.plugin_controls
            if plugin not in installed
        )
    return tuple(gaps)


def level_name(level: str) -> str:
    """How a person reads the level: «básico», «resultado leído», «resultado probado»."""
    return next((name for code, name in _LEVEL_NAMES.items() if code == level), level)


def level_limit(level: str) -> str:
    """What the level itself doesn't check."""
    return _LEVEL_LIMITS.get(level, "")


def gap_text(gap: Gap) -> str:
    """The sentence of a gap, from its code: a report read later says it as this version does."""
    if gap.code == CONTROL_WITHOUT_PLUGIN:
        return f"{gap.control} no corre: falta {gap.plugin}"
    return _GAP_TEXTS.get(gap.code, gap.code)


def unchecked_lines(proof: ProofLevel) -> tuple[str, ...]:
    """What a person is told this run doesn't check: the level's limit, then each gap."""
    return (level_limit(proof.level), *(gap_text(gap) for gap in proof.gaps))


def warning_lines(proof: ProofLevel) -> tuple[str, ...]:
    """What `doctor` and the start of a run warn of: the level's limit below proved, and every gap.

    The limit of the proved level is said in the report, not warned of: no profile closes it.
    """
    limit = (level_limit(proof.level),) if proof.level != PROVED else ()
    return (*limit, *(gap_text(gap) for gap in proof.gaps))


def status_line(proof: ProofLevel) -> str:
    """One line for `lictor status`."""
    name = level_name(proof.level)
    if not proof.gaps:
        return f"Nivel de prueba: {name}"
    count = len(proof.gaps)
    gaps = "1 hueco" if count == 1 else f"{count} huecos"
    return f"Nivel de prueba: {name} ({gaps}; ver report.md)"


def proof_record(proof: ProofLevel) -> dict[str, object]:
    """What `run.started` keeps: codes, never sentences, so an old journal reads with today's."""
    return {"level": proof.level, "gaps": [_gap_record(gap) for gap in proof.gaps]}


def _gap_record(gap: Gap) -> dict[str, str]:
    if gap.code == CONTROL_WITHOUT_PLUGIN:
        return {"code": gap.code, "control": gap.control, "plugin": gap.plugin}
    return {"code": gap.code}


def proof_from_record(record: object) -> ProofLevel | None:
    """The level a journal kept, or None if it kept none or not in this form."""
    if not isinstance(record, Mapping):
        return None
    level = _level_code(record.get("level"))
    gaps = record.get("gaps")
    if level is None or not isinstance(gaps, Sequence):
        return None
    read = [_gap_from(gap) for gap in gaps]
    if any(gap is None for gap in read):
        return None
    return ProofLevel(level, tuple(g for g in read if g is not None))


def _level_code(level: object) -> LevelCode | None:
    """A level as a journal kept it, or None if it is none of the codes."""
    return next((code for code in _LEVEL_NAMES if code == level), None)


def _gap_from(record: object) -> Gap | None:
    if not isinstance(record, Mapping) or not isinstance(code := record.get("code"), str):
        return None
    control = record.get("control", "")
    plugin = record.get("plugin", "")
    if not isinstance(control, str) or not isinstance(plugin, str):
        return None
    return Gap(code, control, plugin)
