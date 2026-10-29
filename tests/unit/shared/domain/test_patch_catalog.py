import pytest

from poe2_rag.shared.domain import (
    DomainError,
    InMemoryPatchCatalog,
    InvalidPatchCatalogError,
    PatchCatalog,
    PatchVersion,
)


def v(raw: str) -> PatchVersion:
    return PatchVersion.parse(raw)


def catalog(*raw_versions: str, current: str = "0.1.1") -> InMemoryPatchCatalog:
    return InMemoryPatchCatalog([v(raw) for raw in raw_versions], v(current))


class TestPort:
    def test_in_memory_catalog_is_a_patch_catalog(self) -> None:
        assert isinstance(catalog("0.1.0", "0.1.1"), PatchCatalog)

    def test_error_is_a_domain_error(self) -> None:
        assert issubclass(InvalidPatchCatalogError, DomainError)


class TestCurrent:
    def test_returns_the_declared_current_version(self) -> None:
        assert catalog("0.1.0", "0.1.1", "0.1.2", current="0.1.1").current() == v("0.1.1")

    def test_current_is_one_of_the_known_versions(self) -> None:
        patch_catalog = catalog("0.1.0", "0.1.1", current="0.1.0")

        assert patch_catalog.current() in patch_catalog.known()


class TestKnown:
    def test_returns_every_version_ascending_whatever_the_input_order(self) -> None:
        patch_catalog = catalog("0.2.0", "0.1.0", "0.1.1")

        assert patch_catalog.known() == (v("0.1.0"), v("0.1.1"), v("0.2.0"))

    def test_returns_a_tuple(self) -> None:
        assert isinstance(catalog("0.1.0", current="0.1.0").known(), tuple)

    def test_accepts_any_iterable_of_versions(self) -> None:
        versions = (v(raw) for raw in ("0.1.1", "0.1.0"))

        assert InMemoryPatchCatalog(versions, v("0.1.1")).known() == (v("0.1.0"), v("0.1.1"))

    def test_does_not_reference_the_input_collection(self) -> None:
        versions = [v("0.1.0"), v("0.1.1")]
        patch_catalog = InMemoryPatchCatalog(versions, v("0.1.1"))

        versions.append(v("0.2.0"))

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


class TestInvariants:
    def test_rejects_an_empty_catalog(self) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            InMemoryPatchCatalog([], v("0.1.0"))

    def test_rejects_a_duplicate_version(self) -> None:
        with pytest.raises(InvalidPatchCatalogError, match=r"0\.1\.0"):
            InMemoryPatchCatalog([v("0.1.0"), v("0.1.0")], v("0.1.0"))

    def test_rejects_a_current_version_that_is_not_known(self) -> None:
        with pytest.raises(InvalidPatchCatalogError, match=r"0\.2\.0"):
            InMemoryPatchCatalog([v("0.1.0")], v("0.2.0"))

    def test_rejects_a_non_version_in_the_known_list(self) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            InMemoryPatchCatalog(["0.1.0"], v("0.1.0"))  # type: ignore[list-item]

    def test_rejects_a_non_version_current(self) -> None:
        with pytest.raises(InvalidPatchCatalogError):
            InMemoryPatchCatalog([v("0.1.0")], "0.1.0")  # type: ignore[arg-type]
