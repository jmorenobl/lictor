from collections.abc import Sequence
from enum import IntEnum


class ExitCode(IntEnum):
    """Códigos de salida estables de Lictor v0.1."""

    OK = 0
    INTERNAL_ERROR = 1
    PRECONDITION = 10
    SPEC_CONFLICT = 11
    QUARANTINED = 30
    ENVIRONMENT_UNAVAILABLE = 40  # Could not run: fix the environment; no scenario is to blame
    RATE_LIMITED = 50


class LictorError(Exception):
    """Excepción base de Lictor con código de salida asociado."""

    exit_code: int = int(ExitCode.INTERNAL_ERROR)

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InternalLictorError(LictorError):
    """Error interno inesperado de Lictor (código 1)."""

    exit_code: int = int(ExitCode.INTERNAL_ERROR)


class PreconditionError(LictorError):
    """Precondición, spec, configuración o driver inválidos (código 10)."""

    exit_code: int = int(ExitCode.PRECONDITION)


class ConfigError(PreconditionError):
    """Configuración o perfil inválidos (código 10)."""


class SpecError(PreconditionError, ValueError):
    """Especificación Gherkin inválida (código 10)."""


class DriverError(PreconditionError):
    """Driver de aceptación inválido (código 10)."""


class AmbiguousMappingError(DriverError):
    """Un escenario sale en varios casos del informe (código 10).

    Con el driver fresco es un rechazo que su reintento arregla; con el driver sellado, casi
    siempre algo que solo una persona puede cambiar (un subtest de Go aparece cuando el código ya
    llega a él), y por eso lleva el escenario y los casos.
    """

    def __init__(self, scenario: str, cases: Sequence[str]) -> None:
        self.scenario = scenario
        self.cases = tuple(cases)
        super().__init__(ambiguous_mapping_message(scenario, self.cases))


def ambiguous_mapping_message(scenario: str, cases: Sequence[str]) -> str:
    """What a driver reads when one scenario maps to several cases, and how it fixes it."""
    return (
        f"El escenario '{scenario}' sale en {len(cases)} casos del informe: {list(cases)}. Cada "
        "escenario es un solo caso: un subtest o un test parametrizado sale como otro caso con "
        "el mismo ID. Recorre los casos dentro de un solo test, y que el mensaje de cada fallo "
        "diga cuál falló."
    )


class SealTamperedError(PreconditionError):
    """La especificación o el driver sellado cambiaron fuera de Lictor (código 10)."""


class CandidateError(PreconditionError):
    """El commit verificado ya no puede convertirse en verde (código 10)."""


class RunInProgressError(PreconditionError):
    """Otra ejecución de Lictor tiene el proyecto (código 10)."""


class EnvironmentUnavailableError(LictorError):
    """No se pudo preparar el entorno de verificación (código 40)."""

    exit_code: int = int(ExitCode.ENVIRONMENT_UNAVAILABLE)


class CleanupError(EnvironmentUnavailableError):
    """Lictor no pudo borrar algo suyo, ni devolviéndole el permiso de escritura (código 40)."""


class SpecConflictError(LictorError):
    """El agente declaró conflicto o imposibilidad en la especificación (código 11)."""

    exit_code: int = int(ExitCode.SPEC_CONFLICT)


class QuarantineError(LictorError):
    """Escenarios en cuarentena por intentos agotados (código 30)."""

    exit_code: int = int(ExitCode.QUARANTINED)


class RateLimitError(LictorError):
    """Límite de uso de la suscripción alcanzado (código 50)."""

    exit_code: int = int(ExitCode.RATE_LIMITED)
