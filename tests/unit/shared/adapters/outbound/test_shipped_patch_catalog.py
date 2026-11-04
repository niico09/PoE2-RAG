"""Pins the shipped config/patches.toml: it must load and date real snapshots correctly."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from poe2_rag.shared.adapters.outbound.toml_patch_catalog import TomlPatchCatalog
from poe2_rag.shared.domain import PatchVersion

SHIPPED_CATALOG = Path(__file__).parents[5] / "config" / "patches.toml"


@pytest.fixture(scope="module")
def catalog() -> TomlPatchCatalog:
    return TomlPatchCatalog(SHIPPED_CATALOG)


def v(raw: str) -> PatchVersion:
    return PatchVersion.parse(raw)


def test_the_shipped_catalog_loads_with_its_current_patch(catalog: TomlPatchCatalog) -> None:
    assert catalog.current() == v("0.5.5e")


def test_the_shipped_catalog_lists_every_hotfix_version_in_order(
    catalog: TomlPatchCatalog,
) -> None:
    known = catalog.known()

    assert len(known) == 63
    assert known[0] == v("0.1.0")
    assert known[-1] == v("0.5.5e")
    assert list(known) == sorted(known)
    assert known[:7] == tuple(
        v(raw) for raw in ("0.1.0", "0.1.0b", "0.1.0c", "0.1.0d", "0.1.0e", "0.1.0f", "0.1.1")
    )


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2024, 12, 6), "0.1.0"),
        (date(2024, 12, 8), "0.1.0"),
        (date(2024, 12, 9), "0.1.0b"),
        (date(2024, 12, 11), "0.1.0c"),
        (date(2025, 1, 15), "0.1.0f"),
        (date(2025, 1, 16), "0.1.1"),
        (date(2026, 9, 20), "0.5.5c"),
        (date(2026, 10, 4), "0.5.5d"),
        (date(2026, 10, 5), "0.5.5e"),
        (date(2026, 11, 4), "0.5.5e"),
    ],
)
def test_the_shipped_catalog_dates_a_snapshot_by_release_day(
    catalog: TomlPatchCatalog, day: date, expected: str
) -> None:
    assert catalog.current_at(day) == v(expected)
