from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from poe2_rag.shared.domain import (
    DomainError,
    InMemoryPatchCatalog,
    InvalidPatchCatalogError,
    PatchCatalog,
    PatchNotReleasedError,
    PatchRelease,
    PatchVersion,
)

RELEASED_ON: dict[str, date] = {
    "0.1.0": date(2024, 12, 6),
    "0.1.1": date(2025, 1, 17),
    "0.1.2": date(2025, 2, 7),
    "0.2.0": date(2025, 3, 1),
}


def v(raw: str) -> PatchVersion:
    return PatchVersion.parse(raw)


def release(raw: str, day: date | None = None) -> PatchRelease:
    """Build one release for a fixture version, using its canonical day unless one is given."""
    return PatchRelease(v(raw), RELEASED_ON[raw] if day is None else day)


def catalog(*raw_versions: str, current: str = "0.1.1") -> InMemoryPatchCatalog:
    return InMemoryPatchCatalog([release(raw) for raw in raw_versions], v(current))


class TestPort:
    def test_in_memory_catalog_is_a_patch_catalog(self) -> None:
        assert isinstance(catalog("0.1.0", "0.1.1"), PatchCatalog)

    def test_error_is_a_domain_error(self) -> None:
        assert issubclass(InvalidPatchCatalogError, DomainError)

    def test_unscheduled_day_error_is_a_domain_error(self) -> None:
        assert issubclass(PatchNotReleasedError, DomainError)


class TestCurrent:
    def test_returns_the_declared_current_version(self) -> None:
        assert catalog("0.1.0", "0.1.1", "0.1.2", current="0.1.1").current() == v("0.1.1")

    def test_current_is_one_of_the_known_versions(self) -> None:
        patch_catalog = catalog("0.1.0", "0.1.1", current="0.1.0")

        assert patch_catalog.current() in patch_catalog.known()

    def test_is_the_configured_pointer_not_the_latest_release(self) -> None:
        assert catalog("0.1.0", "0.1.1", current="0.1.0").current() == v("0.1.0")


class TestKnown:
    def test_returns_every_version_ascending_whatever_the_input_order(self) -> None:
        patch_catalog = catalog("0.2.0", "0.1.0", "0.1.1")

        assert patch_catalog.known() == (v("0.1.0"), v("0.1.1"), v("0.2.0"))

    def test_returns_a_tuple(self) -> None:
        assert isinstance(catalog("0.1.0", current="0.1.0").known(), tuple)

    def test_accepts_any_iterable_of_releases(self) -> None:
        releases = (release(raw) for raw in ("0.1.1", "0.1.0"))

        assert InMemoryPatchCatalog(releases, v("0.1.1")).known() == (v("0.1.0"), v("0.1.1"))

    def test_does_not_reference_the_input_collection(self) -> None:
        releases = [release("0.1.0"), release("0.1.1")]
        patch_catalog = InMemoryPatchCatalog(releases, v("0.1.1"))

        releases.append(release("0.2.0"))

        assert patch_catalog.known() == (v("0.1.0"), v("0.1.1"))

    def test_single_version_catalog_is_its_own_current(self) -> None:
        patch_catalog = catalog("0.1.0", current="0.1.0")

        assert patch_catalog.known() == (v("0.1.0"),)


class TestContains:
    def test_true_for_a_known_version(self) -> None:
        assert catalog("0.1.0", "0.1.1").contains(v("0.1.0"))

    def test_false_for_an_unknown_version(self) -> None:
        assert not catalog("0.1.0", "0.1.1").contains(v("0.2.0"))

    def test_false_for_a_version_above_the_known_range(self) -> None:
        assert not catalog("0.1.0", current="0.1.0").contains(v("99.0.0"))

    @pytest.mark.parametrize("value", ["0.1.0", None, (0, 1, 0)])
    def test_rejects_a_non_version_argument(self, value: object) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            catalog("0.1.0").contains(value)  # type: ignore[arg-type]


class TestCurrentAt:
    def test_the_exact_release_day_returns_that_patch(self) -> None:
        patch_catalog = catalog("0.1.0", "0.1.1", "0.2.0")

        assert patch_catalog.current_at(date(2025, 1, 17)) == v("0.1.1")

    def test_the_day_before_the_next_release_returns_the_earlier_patch(self) -> None:
        patch_catalog = catalog("0.1.0", "0.1.1", "0.2.0")

        assert patch_catalog.current_at(date(2025, 2, 28)) == v("0.1.1")

    def test_a_day_after_the_last_release_returns_the_last_patch(self) -> None:
        patch_catalog = catalog("0.1.0", "0.1.1", "0.2.0")

        assert patch_catalog.current_at(date(2026, 11, 1)) == v("0.2.0")

    def test_the_first_release_day_returns_the_first_patch(self) -> None:
        assert catalog("0.1.0", "0.2.0", current="0.1.0").current_at(date(2024, 12, 6)) == v(
            "0.1.0"
        )

    def test_a_day_before_the_first_release_is_a_specific_error(self) -> None:
        with pytest.raises(PatchNotReleasedError, match=r"0\.1\.0.*2024-12-06"):
            catalog("0.1.0", "0.1.1").current_at(date(2024, 12, 5))

    def test_does_not_derive_the_answer_from_the_current_pointer(self) -> None:
        patch_catalog = catalog("0.1.0", "0.1.1", "0.2.0", current="0.1.1")

        assert patch_catalog.current_at(date(2025, 3, 1)) == v("0.2.0")
        assert patch_catalog.current() == v("0.1.1")

    @pytest.mark.parametrize(
        "value", ["2025-01-17", datetime(2025, 1, 17, tzinfo=UTC), None, 20250117]
    )
    def test_rejects_an_argument_that_is_not_a_date(self, value: object) -> None:
        with pytest.raises(InvalidPatchCatalogError, match="day"):
            catalog("0.1.0", current="0.1.0").current_at(value)  # type: ignore[arg-type]


class TestInvariants:
    def test_rejects_an_empty_catalog(self) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            InMemoryPatchCatalog([], v("0.1.0"))

    def test_rejects_a_duplicate_version(self) -> None:
        with pytest.raises(InvalidPatchCatalogError, match=r"0\.1\.0"):
            InMemoryPatchCatalog([release("0.1.0"), release("0.1.0")], v("0.1.0"))

    def test_rejects_a_current_version_that_is_not_known(self) -> None:
        with pytest.raises(InvalidPatchCatalogError, match=r"0\.2\.0"):
            InMemoryPatchCatalog([release("0.1.0")], v("0.2.0"))

    def test_rejects_a_patch_without_a_release_date(self) -> None:
        """A bare version is a known patch with no date, so the catalog cannot place it."""
        with pytest.raises(InvalidPatchCatalogError, match=r"PatchRelease"):
            InMemoryPatchCatalog([v("0.1.0")], v("0.1.0"))  # type: ignore[list-item]

    def test_rejects_a_non_release_in_the_release_list(self) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            InMemoryPatchCatalog(["0.1.0"], v("0.1.0"))  # type: ignore[list-item]

    def test_rejects_release_dates_shared_by_two_versions(self) -> None:
        with pytest.raises(InvalidPatchCatalogError, match=r"0\.1\.0.*0\.1\.1"):
            InMemoryPatchCatalog(
                [release("0.1.0", date(2025, 1, 17)), release("0.1.1", date(2025, 1, 17))],
                v("0.1.1"),
            )

    def test_rejects_release_dates_that_go_backwards(self) -> None:
        with pytest.raises(InvalidPatchCatalogError, match=r"0\.1\.0.*0\.1\.1"):
            InMemoryPatchCatalog(
                [release("0.1.0", date(2025, 2, 1)), release("0.1.1", date(2025, 1, 17))],
                v("0.1.1"),
            )

    def test_rejects_a_non_version_current(self) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            InMemoryPatchCatalog([release("0.1.0")], "0.1.0")  # type: ignore[arg-type]
