import pytest

from poe2_rag.shared.domain import DomainError, EntityRef, InvalidEntityRefError


def test_key_combines_kind_and_slug() -> None:
    assert EntityRef("active_gem", "spark", "Spark").key == "active_gem:spark"


@pytest.mark.parametrize("slug", ["spark", "lightning-arrow", "gem_01"])
def test_accepts_valid_slugs(slug: str) -> None:
    assert EntityRef("active_gem", slug, "Name").slug == slug


@pytest.mark.parametrize("slug", ["", "Spark", "spark arrow", "spark:1", "ñandu", " spark"])
def test_rejects_invalid_slugs(slug: str) -> None:
    with pytest.raises(InvalidEntityRefError):
        EntityRef("active_gem", slug, "Name")


@pytest.mark.parametrize("kind", ["", "   "])
def test_rejects_blank_kind(kind: str) -> None:
    with pytest.raises(InvalidEntityRefError):
        EntityRef(kind, "spark", "Spark")


@pytest.mark.parametrize("name", ["", "   "])
def test_rejects_blank_name(name: str) -> None:
    with pytest.raises(InvalidEntityRefError):
        EntityRef("active_gem", "spark", name)


def test_error_is_a_domain_error() -> None:
    assert issubclass(InvalidEntityRefError, DomainError)


def test_is_immutable() -> None:
    ref = EntityRef("active_gem", "spark", "Spark")

    with pytest.raises(AttributeError):
        ref.slug = "other"  # type: ignore[misc]
