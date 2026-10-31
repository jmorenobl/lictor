from enum import IntEnum


class ExitCode(IntEnum):
    """Códigos de salida estables de Lictor v0.1."""

    OK = 0
    INTERNAL_ERROR = 1
    PRECONDITION = 10
    SPEC_CONFLICT = 11
    QUARANTINED = 30
    AGENT_UNAVAILABLE = 40
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


class SealTamperedError(PreconditionError):
    """La especificación o el driver sellado cambiaron fuera de Lictor (código 10)."""


class SpecConflictError(LictorError):
    """El agente declaró conflicto o imposibilidad en la especificación (código 11)."""

    exit_code: int = int(ExitCode.SPEC_CONFLICT)


class QuarantineError(LictorError):
    """Escenarios en cuarentena por intentos agotados (código 30)."""

    exit_code: int = int(ExitCode.QUARANTINED)


class RateLimitError(LictorError):
    """Límite de uso de la suscripción alcanzado (código 50)."""

    exit_code: int = int(ExitCode.RATE_LIMITED)
