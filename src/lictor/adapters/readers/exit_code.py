"""Exit-code result reader."""

from lictor.application.ports import ResultReader
from lictor.domain.model import ControlResult

_MAX_TAIL_LINES = 40
_PYTEST_NO_TESTS_EXIT_CODE = 5


class ExitCodeReader(ResultReader):
    """Interprets process exit code 0 as success, non-zero as failure."""

    def read(
        self,
        output: str,
        exit_code: int,
        duration_ms: int,
        allow_empty: bool = False,
    ) -> ControlResult:
        """Parse exit code and output."""
        if exit_code == 0 or (allow_empty and exit_code == _PYTEST_NO_TESTS_EXIT_CODE):
            return ControlResult(
                ok=True,
                duration_ms=duration_ms,
                error=None,
                test_cases=(),
            )

        lines = output.splitlines()
        tail = lines[-_MAX_TAIL_LINES:] if len(lines) > _MAX_TAIL_LINES else lines
        error_msg = (
            "\n".join(tail).strip() if tail else f"Command failed with exit code {exit_code}"
        )

        return ControlResult(
            ok=False,
            duration_ms=duration_ms,
            error=error_msg,
            test_cases=(),
        )
