"""Clock adapter implementations: SystemClock and FixedClock."""

import secrets
from datetime import UTC, datetime


class SystemClock:
    """Real system clock providing UTC timestamps and run ID generation."""

    def now(self) -> datetime:
        """Return current timezone-aware UTC datetime."""
        return datetime.now(UTC)

    def now_iso(self) -> str:
        """Return current time formatted as ISO 8601 UTC string."""
        return self.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    def generate_run_id(self) -> str:
        """Generate a run ID with format YYYYMMDD-HHMMSS-<4 hex chars>."""
        timestamp = self.now().strftime("%Y%m%d-%H%M%S")
        suffix = secrets.token_hex(2)
        return f"{timestamp}-{suffix}"


class FixedClock:
    """Deterministic clock double for tests and reproducible runs."""

    def __init__(
        self,
        fixed_dt: datetime | None = None,
        fixed_run_id: str | None = None,
    ) -> None:
        self._dt = fixed_dt or datetime(2026, 10, 30, 0, 0, 0, tzinfo=UTC)
        self._fixed_run_id = fixed_run_id

    def now(self) -> datetime:
        """Return fixed timezone-aware datetime."""
        return self._dt

    def now_iso(self) -> str:
        """Return fixed time formatted as ISO 8601 UTC string."""
        return self._dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def generate_run_id(self) -> str:
        """Return fixed run ID or generate one using the fixed datetime."""
        if self._fixed_run_id is not None:
            return self._fixed_run_id
        timestamp = self._dt.strftime("%Y%m%d-%H%M%S")
        return f"{timestamp}-beef"
