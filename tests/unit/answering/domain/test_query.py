import pytest

from poe2_rag.answering.domain import InvalidQueryError, Query
from poe2_rag.shared.domain import DomainError, InMemoryPatchCatalog, PatchVersion


def v(raw: str) -> PatchVersion:
    return PatchVersion.parse(raw)


def catalog(*raw_versions: str, current: str = "0.1.1") -> InMemoryPatchCatalog:
    return InMemoryPatchCatalog([v(raw) for raw in raw_versions], v(current))


def test_error_is_a_domain_error() -> None:
    assert issubclass(InvalidQueryError, DomainError)


class TestConstruction:
    def test_keeps_text_and_target_version(self) -> None:
        query = Query("How much mana does Spark cost?", v("0.1.2"))

        assert query.text == "How much mana does Spark cost?"
        assert query.target_version == v("0.1.2")

    def test_queries_with_the_same_values_are_equal(self) -> None:
        assert Query("spark", v("0.1.2")) == Query("spark", v("0.1.2"))

    def test_rejects_blank_text(self) -> None:
        with pytest.raises(InvalidQueryError, match="text"):
            Query("   ", v("0.1.2"))

    def test_rejects_non_string_text(self) -> None:
        with pytest.raises(InvalidQueryError, match="text"):
            Query(42, v("0.1.2"))  # type: ignore[arg-type]

    def test_rejects_a_target_version_that_is_not_a_patch_version(self) -> None:
        with pytest.raises(InvalidQueryError, match="target_version"):
            Query("spark", "0.1.2")  # type: ignore[arg-type]

    def test_rejects_a_missing_target_version(self) -> None:
        with pytest.raises(InvalidQueryError, match="target_version"):
            Query("spark", None)  # type: ignore[arg-type]


class TestForCatalog:
    def test_defaults_to_the_current_patch_of_the_catalog(self) -> None:
        query = Query.for_catalog("spark", catalog("0.1.0", "0.1.1", current="0.1.1"))

        assert query.target_version == v("0.1.1")

    def test_uses_an_explicit_version_the_catalog_knows(self) -> None:
        query = Query.for_catalog("spark", catalog("0.1.0", "0.1.1", "0.1.2"), v("0.1.0"))

        assert query.target_version == v("0.1.0")

    def test_keeps_the_text(self) -> None:
        assert Query.for_catalog("spark", catalog("0.1.1")).text == "spark"

    def test_rejects_a_version_the_catalog_does_not_know(self) -> None:
        with pytest.raises(InvalidQueryError, match="0.2.0"):
            Query.for_catalog("spark", catalog("0.1.0", "0.1.1"), v("0.2.0"))

    def test_rejects_a_non_patch_version_as_invalid_query(self) -> None:
        with pytest.raises(InvalidQueryError, match="version"):
            Query.for_catalog("spark", catalog("0.1.1"), "0.1.1")  # type: ignore[arg-type]

    def test_rejects_an_object_that_is_not_a_catalog(self) -> None:
        with pytest.raises(InvalidQueryError, match="catalog"):
            Query.for_catalog("spark", object())  # type: ignore[arg-type]

    def test_rejects_blank_text_without_touching_the_catalog(self) -> None:
        with pytest.raises(InvalidQueryError, match="text"):
            Query.for_catalog("  ", catalog("0.1.1"))
