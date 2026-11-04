"""The catalog's release model: one known patch and the day it was released."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from poe2_rag.shared.domain import (
    DomainError,
    InvalidPatchCatalogError,
    PatchRelease,
    PatchVersion,
)


def v(raw: str) -> PatchVersion:
    return PatchVersion.parse(raw)


class TestValues:
    def test_holds_the_version_and_its_release_day(self) -> None:
        release = PatchRelease(v("0.1.1"), date(2025, 1, 17))

        assert release.version == v("0.1.1")
        assert release.released_on == date(2025, 1, 17)

    def test_is_frozen(self) -> None:
        release = PatchRelease(v("0.1.0"), date(2024, 12, 6))

        with pytest.raises(FrozenInstanceError):
            release.released_on = date(2025, 1, 1)  # type: ignore[misc]

    def test_two_releases_with_the_same_values_are_equal(self) -> None:
        assert PatchRelease(v("0.1.0"), date(2024, 12, 6)) == PatchRelease(
            v("0.1.0"), date(2024, 12, 6)
        )


class TestInvariants:
    @pytest.mark.parametrize("version", ["0.1.0", None, (0, 1, 0)])
    def test_rejects_a_non_version(self, version: object) -> None:
        with pytest.raises(InvalidPatchCatalogError, match="version"):
            PatchRelease(version, date(2025, 1, 17))  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "released_on",
        ["2025-01-17", datetime(2025, 1, 17, tzinfo=UTC), None, 20250117],
    )
    def test_rejects_a_value_that_is_not_a_date(self, released_on: object) -> None:
        with pytest.raises(InvalidPatchCatalogError, match="released_on"):
            PatchRelease(v("0.1.0"), released_on)  # type: ignore[arg-type]

    def test_rejects_a_datetime_even_though_it_is_a_date_instance(self) -> None:
        """A `datetime` is a `date` subclass, and mixing it with dates breaks comparisons."""
        with pytest.raises(InvalidPatchCatalogError, match="released_on"):
            PatchRelease(v("0.1.0"), datetime(2025, 1, 17, 12, 0, tzinfo=UTC))

    def test_violations_are_domain_errors(self) -> None:
        assert issubclass(InvalidPatchCatalogError, DomainError)
