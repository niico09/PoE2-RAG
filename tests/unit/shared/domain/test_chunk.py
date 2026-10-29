from dataclasses import replace
from datetime import date

import pytest

from poe2_rag.shared.domain import (
    Chunk,
    DomainError,
    EntityRef,
    InvalidChunkError,
    InvalidValidityError,
    Modifier,
    ModifierKind,
    PatchVersion,
    SourceRef,
    Validity,
)


def make_chunk(**overrides: object) -> Chunk:
    defaults: dict[str, object] = {
        "entity": EntityRef("active_gem", "spark", "Spark"),
        "validity": Validity(PatchVersion.parse("0.1.2")),
        "section": "description",
        "text": "Spark fires lightning projectiles that bounce off walls.",
        "source": SourceRef("poe2db", "https://poe2db.tw/us/Spark", date(2026, 10, 29)),
    }
    return Chunk(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_chunk_id_combines_entity_version_and_section() -> None:
    assert make_chunk().chunk_id == "active_gem:spark@0.1.2#description"


def test_tags_and_modifiers_default_to_empty_tuples() -> None:
    chunk = make_chunk()

    assert chunk.tags == ()
    assert chunk.modifiers == ()


def test_keeps_tags_and_modifiers() -> None:
    modifier = Modifier("mana_cost", 12, ModifierKind.FLAT)

    chunk = make_chunk(tags=("Spell", "Lightning"), modifiers=(modifier,))

    assert chunk.tags == ("Spell", "Lightning")
    assert chunk.modifiers == (modifier,)


class TestIsValidAt:
    def test_delegates_to_validity(self) -> None:
        chunk = make_chunk(validity=Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0")))

        assert chunk.is_valid_at(PatchVersion.parse("0.1.0"))
        assert not chunk.is_valid_at(PatchVersion.parse("0.2.0"))

    @pytest.mark.parametrize("value", ["0.1.2", None, 1])
    def test_rejects_a_non_version_argument(self, value: object) -> None:
        with pytest.raises(InvalidValidityError):
            make_chunk().is_valid_at(value)  # type: ignore[arg-type]


class TestInvariants:
    @pytest.mark.parametrize("field", ["section", "text"])
    @pytest.mark.parametrize("value", ["", "   "])
    def test_rejects_blank_text_fields(self, field: str, value: str) -> None:
        with pytest.raises(InvalidChunkError):
            make_chunk(**{field: value})

    @pytest.mark.parametrize("section", [" description", "desc#x", "a@b", "a:b", "Description", "two words"])
    def test_rejects_non_slug_section(self, section: str) -> None:
        with pytest.raises(InvalidChunkError):
            make_chunk(section=section)

    @pytest.mark.parametrize("section", ["description", "skill-tree", "level_20"])
    def test_accepts_slug_section(self, section: str) -> None:
        assert make_chunk(section=section).section == section

    @pytest.mark.parametrize("field", ["entity", "validity", "source"])
    def test_rejects_wrong_component_types(self, field: str) -> None:
        with pytest.raises(InvalidChunkError):
            make_chunk(**{field: "not-a-value-object"})

    def test_rejects_list_tags(self) -> None:
        with pytest.raises(InvalidChunkError):
            make_chunk(tags=["Spell"])

    def test_rejects_blank_tag(self) -> None:
        with pytest.raises(InvalidChunkError):
            make_chunk(tags=("Spell", " "))

    def test_rejects_list_modifiers(self) -> None:
        with pytest.raises(InvalidChunkError):
            make_chunk(modifiers=[Modifier("damage", 1, ModifierKind.MORE)])

    def test_rejects_non_modifier_items(self) -> None:
        with pytest.raises(InvalidChunkError):
            make_chunk(modifiers=({"stat": "damage"},))

    def test_error_is_a_domain_error(self) -> None:
        assert issubclass(InvalidChunkError, DomainError)

    def test_is_immutable(self) -> None:
        chunk = make_chunk()

        with pytest.raises(AttributeError):
            chunk.text = "changed"  # type: ignore[misc]

    def test_replace_revalidates(self) -> None:
        with pytest.raises(InvalidChunkError):
            replace(make_chunk(), section="")
