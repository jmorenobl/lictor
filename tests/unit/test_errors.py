import pytest

from lictor.domain.errors import (
    ConfigError,
    DriverError,
    ExitCode,
    InternalLictorError,
    LictorError,
    PreconditionError,
    QuarantineError,
    RateLimitError,
    SealTamperedError,
    SpecConflictError,
    SpecError,
)


def test_exit_code_values() -> None:
    assert ExitCode.OK == 0
    assert ExitCode.INTERNAL_ERROR == 1
    assert ExitCode.PRECONDITION == 10
    assert ExitCode.SPEC_CONFLICT == 11
    assert ExitCode.QUARANTINED == 30
    assert ExitCode.RATE_LIMITED == 50


@pytest.mark.parametrize(
    ("exc_cls", "expected_code"),
    [
        (LictorError, 1),
        (InternalLictorError, 1),
        (PreconditionError, 10),
        (ConfigError, 10),
        (SpecError, 10),
        (DriverError, 10),
        (SealTamperedError, 10),
        (SpecConflictError, 11),
        (QuarantineError, 30),
        (RateLimitError, 50),
    ],
)
def test_error_hierarchy_and_exit_codes(
    exc_cls: type[LictorError],
    expected_code: int,
) -> None:
    err = exc_cls("algo falló")
    assert isinstance(err, LictorError)
    assert err.exit_code == expected_code
    assert err.message == "algo falló"
    assert str(err) == "algo falló"


def test_precondition_subclasses() -> None:
    for cls in (ConfigError, SpecError, DriverError, SealTamperedError):
        err = cls("detalle de precondición")
        assert isinstance(err, PreconditionError)
        assert err.exit_code == ExitCode.PRECONDITION

