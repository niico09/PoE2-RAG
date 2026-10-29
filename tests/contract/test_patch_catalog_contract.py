"""Contract every `PatchCatalog` implementation must honour (architecture §8)."""

from collections.abc import Callable
from pathlib import Path

import pytest

from poe2_rag.shared.adapters.outbound.toml_patch_catalog import TomlPatchCatalog
from poe2_rag.shared.domain import (
    DomainError,
    InMemoryPatchCatalog,
    InvalidPatchCatalogError,
    PatchCatalog,
    PatchVersion,
)

BuildCatalog = Callable[[tuple[str, ...], str], PatchCatalog]

VERSIONS = ("0.1.0", "0.1.1", "0.2.0")
CURRENT = "0.1.1"


def v(raw: str) -> PatchVersion:
    return PatchVersion.parse(raw)


def catalog_toml(versions: tuple[str, ...], current: str) -> str:
    lines = [f'current = "{current}"', ""]
    for version in versions:
        lines += ["[[patches]]", f'version = "{version}"', "released_on = 2024-12-06", ""]
    return "\n".join(lines)


@pytest.fixture(params=["in-memory", "toml"])
def build_catalog(request: pytest.FixtureRequest, tmp_path: Path) -> BuildCatalog:
    """Build the same catalog through either implementation, so one suite proves both."""
    if request.param == "in-memory":
        return lambda versions, current: InMemoryPatchCatalog([v(item) for item in versions], v(current))

    def build(versions: tuple[str, ...], current: str) -> PatchCatalog:
        path = tmp_path / "patches.toml"
        path.write_text(catalog_toml(versions, current), encoding="utf-8")
        return TomlPatchCatalog(path)

    return build


class TestPortSurface:
    def test_is_a_patch_catalog(self, build_catalog: BuildCatalog) -> None:
        assert isinstance(build_catalog(VERSIONS, CURRENT), PatchCatalog)


class TestCurrent:
    def test_returns_the_declared_current_patch(self, build_catalog: BuildCatalog) -> None:
        assert build_catalog(VERSIONS, CURRENT).current() == v(CURRENT)

    def test_is_one_of_the_known_patches(self, build_catalog: BuildCatalog) -> None:
        patch_catalog = build_catalog(VERSIONS, CURRENT)

        assert patch_catalog.current() in patch_catalog.known()


class TestKnown:
    def test_returns_every_patch_ascending(self, build_catalog: BuildCatalog) -> None:
        patch_catalog = build_catalog(("0.2.0", "0.1.1", "0.1.0"), CURRENT)

        assert patch_catalog.known() == (v("0.1.0"), v("0.1.1"), v("0.2.0"))

    def test_orders_numerically_not_lexicographically(self, build_catalog: BuildCatalog) -> None:
        patch_catalog = build_catalog(("0.10.0", "0.2.0", "0.1.0"), "0.2.0")

        assert patch_catalog.known() == (v("0.1.0"), v("0.2.0"), v("0.10.0"))

    def test_returns_patch_versions(self, build_catalog: BuildCatalog) -> None:
        assert all(isinstance(version, PatchVersion) for version in build_catalog(VERSIONS, CURRENT).known())

    def test_returns_an_immutable_tuple(self, build_catalog: BuildCatalog) -> None:
        assert isinstance(build_catalog(VERSIONS, CURRENT).known(), tuple)

    def test_is_stable_across_calls(self, build_catalog: BuildCatalog) -> None:
        patch_catalog = build_catalog(VERSIONS, CURRENT)

        assert patch_catalog.known() == patch_catalog.known()

    def test_single_patch_catalog_lists_that_patch_only(self, build_catalog: BuildCatalog) -> None:
        patch_catalog = build_catalog(("0.1.0",), "0.1.0")

        assert patch_catalog.known() == (v("0.1.0"),)


class TestContains:
    def test_true_for_a_known_patch(self, build_catalog: BuildCatalog) -> None:
        assert build_catalog(VERSIONS, CURRENT).contains(v("0.1.0"))

    def test_false_for_an_unknown_patch(self, build_catalog: BuildCatalog) -> None:
        assert not build_catalog(VERSIONS, CURRENT).contains(v("0.3.0"))

    def test_false_above_and_below_the_known_range(self, build_catalog: BuildCatalog) -> None:
        patch_catalog = build_catalog(VERSIONS, CURRENT)

        assert not patch_catalog.contains(v("0.0.9"))
        assert not patch_catalog.contains(v("99.0.0"))


class TestInvariants:
    def test_rejects_an_empty_catalog(self, build_catalog: BuildCatalog) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            build_catalog((), CURRENT)

    def test_rejects_a_repeated_patch(self, build_catalog: BuildCatalog) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            build_catalog(("0.1.0", "0.1.0"), "0.1.0")

    def test_rejects_an_unknown_current_patch(self, build_catalog: BuildCatalog) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            build_catalog(("0.1.0",), "0.2.0")

    def test_reports_invariant_failures_as_domain_errors(self, build_catalog: BuildCatalog) -> None:
        with pytest.raises(DomainError):
            build_catalog(("0.1.0",), "0.2.0")
