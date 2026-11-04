"""System clock adapter for evaluation runs."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Return the current UTC time."""

    def now(self) -> datetime:
        """Return the current instant as a timezone-aware UTC datetime."""
        return datetime.now(UTC)
