"""poe2db gem-page parser: one entity per page, exact text, conservative modifiers (S9, S12).

The two committed fixtures are a trimmed excerpt of the captured Spark page and a structural
support-gem page. The parser is testable without the network: a `RawDocument` carries the page URL as
its locator, exactly as the HTTP fetcher returns it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from poe2_rag.ingestion.adapters.outbound.poe2db.gem_page_parser import (
    EmptyGemPageError,
    MissingPageUrlError,
    NotAGemPageError,
    Poe2dbGemPageParser,
    Poe2dbParseError,
    Poe2dbSourceTypeError,
)
from poe2_rag.ingestion.application import DocumentParser, RawDocument
from poe2_rag.shared.domain import (
    InMemoryPatchCatalog,
    ModifierKind,
    PatchVersion,
)

FIXTURES = Path(__file__).parents[4] / "fixtures" / "poe2db"
URL = "https://poe2db.tw/us/Spark"
SUPPORT_URL = "https://poe2db.tw/us/Pierce"
FETCHED_AT = datetime(2026, 11, 1, 12, 30, tzinfo=UTC)
CURRENT = PatchVersion.parse("0.1.1")


def build_parser() -> Poe2dbGemPageParser:
    catalog = InMemoryPatchCatalog(
        [PatchVersion.parse("0.1.0"), CURRENT], CURRENT
    )
    return Poe2dbGemPageParser(catalog)


def raw_document(
    name: str = "active_gem_spark.html",
    *,
    locator: str = URL,
    source_type: str = "poe2db",
    content: str | None = None,
) -> RawDocument:
    if content is None:
        content = (FIXTURES / name).read_text(encoding="utf-8")
    return RawDocument(
        locator=locator,
        content=content,
        fetched_at=FETCHED_AT,
        source_type=source_type,
    )


def by_section(chunks: tuple, section: str):
    return next(chunk for chunk in chunks if chunk.section == section)


def line_set(text: str) -> list[str]:
    return text.splitlines()


class TestActiveGem:
    def test_is_a_document_parser(self) -> None:
        assert isinstance(build_parser(), DocumentParser)

    def test_one_active_gem_entity_with_the_page_sections(self) -> None:
        chunks = build_parser().parse(raw_document())

        assert [chunk.section for chunk in chunks] == [
            "description",
            "properties",
            "explicit-modifiers",
            "levels",
        ]
        for chunk in chunks:
            assert chunk.entity.kind == "active_gem"
            assert chunk.entity.name == "Spark"
            assert chunk.entity.slug == "spark"
            assert chunk.validity.valid_from == CURRENT
            assert chunk.validity.valid_until is None
            assert chunk.source.type == "poe2db"
            assert chunk.source.url == URL
            assert chunk.source.captured_at == FETCHED_AT.date()

    def test_description_keeps_the_page_wording(self) -> None:
        description = by_section(build_parser().parse(raw_document()), "description")

        assert description.text == (
            "Launch a spray of sparking Projectiles that travel erratically along the ground "
            "until they hit an enemy or expire. Consumes a Cold Infusion if possible to fire many "
            "sparks in a circle."
        )

    def test_properties_keep_exact_numbers_and_tags(self) -> None:
        properties = by_section(build_parser().parse(raw_document()), "properties")

        assert line_set(properties.text) == [
            "Projectile, Lightning, Duration, Repeatable",
            "Tier: 1",
            "Level: (1—20)",
            "Cost: (5—56) Mana",
            "Cast Time: 0.70 sec",
            "Critical Hit Chance: 9.00%",
            "Projectile Speed: 10 metres per Second",
            "Requires: Level (1—90), (4—157) Int",
        ]
        assert properties.tags == ("Projectile", "Lightning", "Duration", "Repeatable")

    def test_explicit_modifiers_keep_ranges_and_join_br_as_a_space(self) -> None:
        modifiers = by_section(
            build_parser().parse(raw_document()), "explicit-modifiers"
        )

        assert line_set(modifiers.text) == [
            "Deals (1—11) to (10—216) Lightning Damage",
            "Fires (4—9) Projectiles",
            "Projectile duration is 2 seconds",
            "Projectiles fired at the same time can Hit the same target no more than once every "
            "0.66 seconds",
        ]

    def test_quality_modifiers_are_not_explicit_modifiers(self) -> None:
        modifiers = by_section(
            build_parser().parse(raw_document()), "explicit-modifiers"
        )

        assert "Additional Effects From Quality" not in modifiers.text
        assert "increased Projectile Speed" not in modifiers.text
        assert "Cold Damage" not in modifiers.text

    def test_ranges_do_not_become_structured_modifiers(self) -> None:
        """A range has two numbers; collapsing them into one `value` would falsify it (D2)."""
        modifiers = by_section(
            build_parser().parse(raw_document()), "explicit-modifiers"
        )

        assert modifiers.modifiers == ()

    def test_level_table_is_one_levels_section_with_exact_rows(self) -> None:
        levels = by_section(build_parser().parse(raw_document()), "levels")

        assert line_set(levels.text) == [
            "Level | Requires Level | Int | Mana | Base Damage | Deals 1 to 10 Lightning Damage "
            "| Fires 4 Projectiles | Deals 7 to 11 Cold Damage | Fires 12 Projectiles",
            "1 | 0 | 4 | 5 | 100%, 100%, 100%, 100% | 1, 10 | 4 | 7, 11 | 12",
            "20 | 90 | 157 | 56 | 100%, 100%, 100%, 100% | 13, 245 | 9 | 207, 310 | 16",
        ]

    def test_chunk_ids_follow_the_domain_identity(self) -> None:
        chunks = build_parser().parse(raw_document())

        assert [chunk.chunk_id for chunk in chunks] == [
            "poe2db:active_gem:spark@0.1.1#description",
            "poe2db:active_gem:spark@0.1.1#properties",
            "poe2db:active_gem:spark@0.1.1#explicit-modifiers",
            "poe2db:active_gem:spark@0.1.1#levels",
        ]


class TestSupportGem:
    def test_kind_comes_from_the_page_type_line(self) -> None:
        chunks = build_parser().parse(
            raw_document("support_gem_pierce.html", locator=SUPPORT_URL)
        )

        assert [chunk.section for chunk in chunks] == [
            "description",
            "properties",
            "implicit-modifiers",
            "explicit-modifiers",
            "levels",
        ]
        entity = chunks[0].entity
        assert entity.kind == "support_gem"
        assert entity.name == "Pierce"
        assert entity.slug == "pierce"

    def test_implicit_and_explicit_modifiers_are_separate_sections(self) -> None:
        chunks = build_parser().parse(
            raw_document("support_gem_pierce.html", locator=SUPPORT_URL)
        )

        implicit = by_section(chunks, "implicit-modifiers")
        explicit = by_section(chunks, "explicit-modifiers")
        assert implicit.text == "Supported Skills Pierce an additional Enemy"
        assert line_set(explicit.text) == [
            "Supported Skills deal 10% increased Projectile Damage",
            "Supported Skills Pierce 1 additional Enemy",
        ]
        assert "increased Projectile Damage" not in implicit.text

    def test_clean_percentage_modifier_becomes_a_structured_modifier(self) -> None:
        chunks = build_parser().parse(
            raw_document("support_gem_pierce.html", locator=SUPPORT_URL)
        )

        explicit = by_section(chunks, "explicit-modifiers")
        assert [
            (modifier.stat, modifier.value, modifier.kind)
            for modifier in explicit.modifiers
        ] == [("Projectile Damage", 10.0, ModifierKind.INCREASED)]


class TestModifierMapping:
    def test_maps_the_four_clean_shapes_and_leaves_ranges_as_text(self) -> None:
        content = """
        <div class="newItemPopup GemPopup">
          <div class="itemHeader">
            <div class="itemName"><span class="lc">Probe</span></div>
            <div class="itemName typeLine"><span class="lc">Spell</span></div>
          </div>
          <div class="content">
            <div class="Stats"><div class="property">Spell</div></div>
            <div class="hybridHeader gemTabs"><span class="ItemType">Base</span></div>
            <div class="Stats">
              <div class="explicitMod">10% increased Projectile Speed</div>
              <div class="explicitMod">20% more Damage</div>
              <div class="explicitMod">15% reduced Duration</div>
              <div class="explicitMod">+12 to maximum Mana</div>
              <div class="explicitMod">Adds 1 to 12 Lightning Damage</div>
            </div>
          </div>
        </div>
        """

        chunk = by_section(
            build_parser().parse(raw_document(content=content)), "explicit-modifiers"
        )

        assert [
            (modifier.stat, modifier.value, modifier.kind)
            for modifier in chunk.modifiers
        ] == [
            ("Projectile Speed", 10.0, ModifierKind.INCREASED),
            ("Damage", 20.0, ModifierKind.MORE),
            ("Duration", 15.0, ModifierKind.REDUCED),
            ("maximum Mana", 12.0, ModifierKind.FLAT),
        ]
        assert "Adds 1 to 12 Lightning Damage" in chunk.text


class TestRejections:
    def test_another_source_type_is_rejected(self) -> None:
        with pytest.raises(Poe2dbSourceTypeError):
            build_parser().parse(raw_document(source_type="patch_notes"))

    def test_blank_content_is_rejected(self) -> None:
        with pytest.raises(Poe2dbParseError):
            build_parser().parse(raw_document(content="   "))

    def test_a_non_gem_page_is_rejected(self) -> None:
        with pytest.raises(NotAGemPageError):
            build_parser().parse(
                raw_document(content="<html><body><h1>Items</h1></body></html>")
            )

    def test_a_gem_without_a_name_is_rejected(self) -> None:
        content = """
        <div class="GemPopup"><div class="itemHeader"></div><div class="content"></div></div>
        """

        with pytest.raises(NotAGemPageError):
            build_parser().parse(raw_document(content=content))

    def test_a_gem_page_without_usable_content_is_rejected(self) -> None:
        content = """
        <div class="GemPopup">
          <div class="itemHeader">
            <div class="itemName"><span class="lc">Empty</span></div>
            <div class="itemName typeLine"><span class="lc">Spell</span></div>
          </div>
          <div class="content"></div>
        </div>
        """

        with pytest.raises(EmptyGemPageError):
            build_parser().parse(raw_document(content=content))

    def test_a_locator_that_is_not_a_page_url_is_rejected(self) -> None:
        with pytest.raises(MissingPageUrlError):
            build_parser().parse(raw_document(locator="data/raw/spark.html"))
