"""Adapter-specific behaviour of the system clock."""

from __future__ import annotations

from datetime import UTC, timedelta

from poe2_rag.evaluation.adapters.outbound import SystemClock


class TestNow:
    def test_now_returns_a_timezone_aware_utc_datetime(self) -> None:
        now = SystemClock().now()

        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)
        assert now.tzinfo is UTC
