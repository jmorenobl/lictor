"""Exit-code result reader."""

from collections.abc import Collection

from lictor.application.ports import ResultReader
from lictor.domain.model import ControlResult

_MAX_TAIL_LINES = 40


class ExitCodeReader(ResultReader):
    """Interprets process exit code 0 as success, non-zero as failure."""

    def read(
        self,
        output: str,
        exit_code: int,
        duration_ms: int,
        empty_exit_codes: Collection[int] = (),
    ) -> ControlResult:
        """Parse exit code and output; a code the profile declares as an empty run also passes."""
        if exit_code == 0 or exit_code in empty_exit_codes:
            return ControlResult(
                ok=True,
                duration_ms=duration_ms,
                error=None,
                test_cases=(),
            )

        lines = output.splitlines()
        tail = lines[-_MAX_TAIL_LINES:] if len(lines) > _MAX_TAIL_LINES else lines
        error_msg = (
            "\n".join(tail).strip() if tail else f"El comando falló con el código {exit_code}"
        )

        return ControlResult(
            ok=False,
            duration_ms=duration_ms,
            error=error_msg,
            test_cases=(),
        )
