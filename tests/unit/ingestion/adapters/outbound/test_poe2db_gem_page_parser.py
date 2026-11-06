"""poe2db gem-page parser: one entity per page, exact text, conservative modifiers (S9, S12).

The two committed fixtures are a trimmed excerpt of the captured Spark page and a structural
support-gem page. The parser is testable without the network: a `RawDocument` carries the page URL as
its locator, exactly as the HTTP fetcher returns it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from chunk_text import body, context_line

from poe2_rag.ingestion.adapters.outbound.poe2db.gem_page_parser import (
    DEFAULT_LEVEL_BLOCK_SIZE,
    EmptyGemPageError,
    InvalidLevelBlockSizeError,
    MissingPageUrlError,
    NaiveFetchedAtError,
    NotAGemPageError,
    Poe2dbGemPageParser,
    Poe2dbParseError,
    Poe2dbSourceTypeError,
)
from poe2_rag.ingestion.application import DocumentParser, RawDocument
from poe2_rag.shared.domain import (
    Chunk,
    InMemoryPatchCatalog,
    ModifierKind,
    PatchNotReleasedError,
    PatchRelease,
    PatchVersion,
)

FIXTURES = Path(__file__).parents[4] / "fixtures" / "poe2db"
URL = "https://poe2db.tw/us/Spark"
SUPPORT_URL = "https://poe2db.tw/us/Pierce"
FETCHED_AT = datetime(2026, 11, 1, 12, 30, tzinfo=UTC)
CURRENT = PatchVersion.parse("0.1.1")


def build_catalog() -> InMemoryPatchCatalog:
    return InMemoryPatchCatalog(
        [
            PatchRelease(PatchVersion.parse("0.1.0"), date(2024, 12, 6)),
            PatchRelease(CURRENT, date(2025, 1, 17)),
        ],
        CURRENT,
    )


def build_parser(level_block_size: int = DEFAULT_LEVEL_BLOCK_SIZE) -> Poe2dbGemPageParser:
    """Build the parser with the size the test asks for, defaulting to the module's own."""
    return Poe2dbGemPageParser(build_catalog(), level_block_size=level_block_size)


def build_production_parser() -> Poe2dbGemPageParser:
    """Build the parser the way `bootstrap.py` does, so the constructor default is the one tested."""
    return Poe2dbGemPageParser(build_catalog())


def catalog_with(*released: tuple[str, date]) -> InMemoryPatchCatalog:
    """Build a catalog from explicit release days, treating its last release as current."""
    releases = [PatchRelease(PatchVersion.parse(version), day) for version, day in released]
    return InMemoryPatchCatalog(releases, releases[-1].version)


def raw_document(
    name: str = "active_gem_spark.html",
    *,
    locator: str = URL,
    source_type: str = "poe2db",
    content: str | None = None,
    fetched_at: datetime | None = None,
) -> RawDocument:
    if content is None:
        content = (FIXTURES / name).read_text(encoding="utf-8")
    return RawDocument(
        locator=locator,
        content=content,
        fetched_at=FETCHED_AT if fetched_at is None else fetched_at,
        source_type=source_type,
    )


def by_section(chunks: tuple, section: str):
    return next(chunk for chunk in chunks if chunk.section == section)


def gem_page(content: str, *, name: str = "Probe", type_line: str = "Spell") -> str:
    """Wrap `content` in the header shape the real page renders."""
    return f"""
    <div class="newItemPopup GemPopup">
      <div class="itemHeader">
        <div class="itemName"><span class="lc">{name}</span></div>
        <div class="itemName typeLine"><span class="lc">{type_line}</span></div>
      </div>
      <div class="content">{content}</div>
    </div>
    """


def line_set(text: str) -> list[str]:
    return text.splitlines()


LEVEL_HEADER = ("Level", "Mana", "Damage")
STATS_STUB = """
<div class="Stats">
  <div class="property">Spell</div>
  <div class="secDescrText">A probe.</div>
</div>
"""
"""The smallest property block a page needs to be a gem page at all."""


def level_cells(count: int) -> list[tuple[str, str, str]]:
    """Return `count` level rows, level 1 first, with the values a real table renders."""
    return [
        (str(level), str(4 + level), f"{level * 10} to {level * 10 + 9}")
        for level in range(1, count + 1)
    ]


def level_table(cells: Sequence[Sequence[str]]) -> str:
    """Render a level table the way the page does: one header row and one row per level."""
    header = "".join(f"<th>{cell}</th>" for cell in LEVEL_HEADER)
    rows = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in cells
    )
    return f"<table><tr>{header}</tr>{rows}</table>"


def level_page(count: int) -> str:
    """Return a gem page whose level table has `count` data rows, levels 1..`count`."""
    return gem_page(STATS_STUB) + level_table(level_cells(count))


def level_chunks(chunks: tuple[Chunk, ...]) -> list[Chunk]:
    """Return the level blocks of a parsed page, in document order."""
    return [chunk for chunk in chunks if chunk.section.startswith(("levels-", "rows-"))]


def rendered_rows(cells: Sequence[Sequence[str]]) -> list[str]:
    """Return the row lines the parser must reproduce, exactly as the table renders them."""
    return [" | ".join(row) for row in cells]


class TestActiveGem:
    def test_is_a_document_parser(self) -> None:
        assert isinstance(build_parser(), DocumentParser)

    def test_one_active_gem_entity_with_the_page_sections(self) -> None:
        chunks = build_parser().parse(raw_document())

        assert [chunk.section for chunk in chunks] == [
            "description",
            "properties",
            "explicit-modifiers",
            "levels-01-20",
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

        assert context_line(description) == "Spark — Description"
        assert body(description) == (
            "Launch a spray of sparking Projectiles that travel erratically along the ground "
            "until they hit an enemy or expire. Consumes a Cold Infusion if possible to fire many "
            "sparks in a circle."
        )

    def test_properties_keep_exact_numbers_and_tags(self) -> None:
        properties = by_section(build_parser().parse(raw_document()), "properties")

        assert line_set(properties.text) == [
            "Spark — Properties",
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
            "Spark — Explicit Modifiers",
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

    def test_the_level_table_is_a_self_describing_block_of_its_rows(self) -> None:
        """The title and the header repeat the context the embedder and the reranker lack (S2)."""
        levels = by_section(build_parser().parse(raw_document()), "levels-01-20")

        assert line_set(levels.text) == [
            "Spark — levels 1–20",
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
            "poe2db:active_gem:spark@0.1.1#levels-01-20",
        ]


class TestChunkContextLines:
    """Every chunk names its gem and section in one line, not only the level blocks (S1-S2)."""

    def test_every_chunk_starts_with_its_context_line(self) -> None:
        chunks = build_parser().parse(raw_document())

        assert [context_line(chunk) for chunk in chunks] == [
            "Spark — Description",
            "Spark — Properties",
            "Spark — Explicit Modifiers",
            "Spark — levels 1–20",
        ]

    def test_the_body_after_the_line_is_the_page_text_unchanged(self) -> None:
        """The line is added, never substituted: the rest is the page's own rendering (S5)."""
        chunks = build_parser().parse(raw_document())

        assert body(by_section(chunks, "description")).startswith("Launch a spray of sparking")
        assert body(by_section(chunks, "properties")).endswith(
            "Requires: Level (1—90), (4—157) Int"
        )
        assert line_set(body(by_section(chunks, "levels-01-20")))[0].startswith(
            "Level | Requires Level"
        )

    def test_no_chunk_carries_a_second_context_line(self) -> None:
        chunks = build_parser().parse(raw_document())

        for chunk in chunks:
            line = context_line(chunk)
            assert line_set(chunk.text).count(line) == 1

    def test_every_level_block_carries_the_line_once(self) -> None:
        blocks = level_chunks(build_parser().parse(raw_document(content=level_page(40))))

        for block in blocks:
            lines = line_set(block.text)
            assert lines[0].startswith("Probe — levels ")
            assert sum(line.startswith("Probe — ") for line in lines) == 1


class TestLevelBlocks:
    """The per-level table is split into self-describing blocks no model window has to cut (S1-S3)."""

    def test_a_forty_row_table_becomes_four_blocks_of_ten_rows(self) -> None:
        """The parser is built without a size, so this pins the constructor default (S1)."""
        blocks = level_chunks(build_production_parser().parse(raw_document(content=level_page(40))))

        assert [block.section for block in blocks] == [
            "levels-01-10",
            "levels-11-20",
            "levels-21-30",
            "levels-31-40",
        ]

    def test_each_block_repeats_the_title_the_header_and_its_own_rows(self) -> None:
        blocks = level_chunks(build_parser().parse(raw_document(content=level_page(40))))

        assert len(blocks) == 4
        for index, block in enumerate(blocks):
            lines = line_set(block.text)
            first = index * 10 + 1
            assert lines[0] == f"Probe — levels {first}–{first + 9}"
            assert lines[1] == "Level | Mana | Damage"
            assert lines[2:] == rendered_rows(level_cells(40))[first - 1 : first + 9]
            assert len(lines) == 12

    def test_the_block_rows_are_the_original_rows_once_and_in_order(self) -> None:
        blocks = level_chunks(build_parser().parse(raw_document(content=level_page(40))))

        rows = [line for block in blocks for line in line_set(block.text)[2:]]

        assert rows == rendered_rows(level_cells(40))

    def test_a_table_that_is_not_a_multiple_has_a_shorter_last_block(self) -> None:
        blocks = level_chunks(build_parser().parse(raw_document(content=level_page(25))))

        assert [block.section for block in blocks] == [
            "levels-01-10",
            "levels-11-20",
            "levels-21-25",
        ]
        assert line_set(blocks[-1].text)[0] == "Probe — levels 21–25"
        assert line_set(blocks[-1].text)[2:] == rendered_rows(level_cells(25))[20:]

    def test_the_block_size_is_a_constructor_parameter(self) -> None:
        chunks = build_parser(level_block_size=7).parse(raw_document(content=level_page(40)))
        blocks = level_chunks(chunks)

        assert [block.section for block in blocks] == [
            "levels-01-07",
            "levels-08-14",
            "levels-15-21",
            "levels-22-28",
            "levels-29-35",
            "levels-36-40",
        ]
        assert len(line_set(blocks[-1].text)) == 2 + 5

    def test_the_block_ids_sort_in_table_order(self) -> None:
        """`Chunk.__post_init__` already rejects a non-slug section during `parse`."""
        blocks = level_chunks(build_parser().parse(raw_document(content=level_page(40))))
        sections = [block.section for block in blocks]

        assert sorted(sections) == sections

    def test_a_first_column_that_is_not_a_level_series_is_labelled_by_position(self) -> None:
        """A value the page never rendered must not become a level in the id or the title (S2)."""
        cells = [(f"{level}-{level + 20}", "5", "1 to 9") for level in range(1, 21)]
        content = gem_page(STATS_STUB) + level_table(cells)

        blocks = level_chunks(build_parser().parse(raw_document(content=content)))

        assert [block.section for block in blocks] == ["rows-01-10", "rows-11-20"]
        assert line_set(blocks[0].text)[0] == "Probe — rows 1–10"
        assert line_set(blocks[0].text)[2] == "1-21 | 5 | 1 to 9"
        assert all("levels" not in line_set(block.text)[0] for block in blocks)

    def test_repeated_level_values_do_not_give_two_blocks_one_id(self) -> None:
        """Repeated labels would collide as `levels-01-10` twice and break the corpus load."""
        cells = [(str(level % 10 + 1), "5", "1 to 9") for level in range(20)]
        content = gem_page(STATS_STUB) + level_table(cells)

        blocks = level_chunks(build_parser().parse(raw_document(content=content)))

        assert [block.section for block in blocks] == ["rows-01-10", "rows-11-20"]
        assert len({block.chunk_id for block in blocks}) == 2

    def test_a_level_cell_too_long_to_be_a_level_keeps_the_parser_error_contract(self) -> None:
        """A corrupt capture must not reach `int` and raise outside `Poe2dbParseError`."""
        cells = [("9" * 5000, "5", "1 to 9"), *level_cells(19)]
        content = gem_page(STATS_STUB) + level_table(cells)

        blocks = level_chunks(build_parser().parse(raw_document(content=content)))

        assert [block.section for block in blocks] == ["rows-01-10", "rows-11-20"]

    def test_a_table_without_data_rows_produces_no_levels_chunk(self) -> None:
        content = gem_page(STATS_STUB) + level_table(())

        chunks = build_parser().parse(raw_document(content=content))

        assert level_chunks(chunks) == []
        assert [chunk.section for chunk in chunks] == ["description", "properties"]

    @pytest.mark.parametrize("level_block_size", [0, -1, "10", True])
    def test_an_invalid_block_size_is_rejected(self, level_block_size: object) -> None:
        with pytest.raises(InvalidLevelBlockSizeError):
            build_parser(level_block_size=level_block_size)  # type: ignore[arg-type]


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
            "levels-01-20",
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
        assert context_line(implicit) == "Pierce — Implicit Modifiers"
        assert body(implicit) == "Supported Skills Pierce an additional Enemy"
        assert line_set(explicit.text) == [
            "Pierce — Explicit Modifiers",
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


class TestLayoutRobustness:
    def test_implicit_modifiers_are_not_duplicated_without_a_tab_header(self) -> None:
        """Without `gemTabs` the fallback can make one block serve as properties and modifiers."""
        content = gem_page(
            """
            <div class="Stats">
              <div class="property">Spell</div>
              <div class="implicitMod">+5 to maximum Life</div>
              <div class="explicitMod">10% increased Life</div>
            </div>
            """
        )

        chunks = build_parser().parse(raw_document(content=content))
        implicit = by_section(chunks, "implicit-modifiers")

        assert context_line(implicit) == "Probe — Implicit Modifiers"
        assert body(implicit) == "+5 to maximum Life"
        assert len(implicit.modifiers) == 1

    def test_a_separator_between_the_tab_header_and_its_stats_keeps_the_modifiers(self) -> None:
        content = gem_page(
            """
            <div class="Stats">
              <div class="property">Spell</div>
              <div class="secDescrText">A probe.</div>
            </div>
            <div class="hybridHeader gemTabs"><span class="ItemType">Base</span></div>
            <div class="separator"></div>
            <div class="Stats"><div class="explicitMod">10% increased Life</div></div>
            """
        )

        explicit = by_section(
            build_parser().parse(raw_document(content=content)), "explicit-modifiers"
        )

        assert context_line(explicit) == "Probe — Explicit Modifiers"
        assert body(explicit) == "10% increased Life"

    def test_a_wrapper_between_the_tab_header_and_its_stats_keeps_the_modifiers(self) -> None:
        content = gem_page(
            """
            <div class="Stats">
              <div class="property">Spell</div>
              <div class="secDescrText">A probe.</div>
            </div>
            <div class="hybridHeader gemTabs"><span class="ItemType">Base</span></div>
            <div class="wrapper"><div class="Stats"><div class="explicitMod">10% increased Life</div></div></div>
            """
        )

        explicit = by_section(
            build_parser().parse(raw_document(content=content)), "explicit-modifiers"
        )

        assert context_line(explicit) == "Probe — Explicit Modifiers"
        assert body(explicit) == "10% increased Life"

    def test_a_first_tab_that_is_not_base_does_not_contribute_modifiers(self) -> None:
        """The documented guarantee is that variant text is never attributed to the base gem."""
        content = gem_page(
            """
            <div class="Stats">
              <div class="property">Spell</div>
              <div class="secDescrText">A probe.</div>
            </div>
            <div class="hybridHeader gemTabs"><span class="ItemType">Cold-Infused</span></div>
            <div class="Stats"><div class="explicitMod">Deals 7 to 11 Cold Damage</div></div>
            """
        )

        chunks = build_parser().parse(raw_document(content=content))

        assert "explicit-modifiers" not in {chunk.section for chunk in chunks}
        assert all("Cold Damage" not in chunk.text for chunk in chunks)

    def test_a_level_table_before_the_gem_block_is_ignored(self) -> None:
        content = """
        <table>
          <tr><th>Level</th><th>Mana</th></tr>
          <tr><td>1</td><td>99</td></tr>
        </table>
        """ + gem_page(
            """
            <div class="Stats">
              <div class="property">Spell</div>
              <div class="secDescrText">A probe.</div>
            </div>
            """
        ) + """
        <table>
          <tr><th>Level</th><th>Mana</th></tr>
          <tr><td>20</td><td>56</td></tr>
        </table>
        """

        levels = by_section(build_parser().parse(raw_document(content=content)), "levels-20-20")

        assert "56" in levels.text
        assert "99" not in levels.text

    def test_empty_level_cells_keep_their_column(self) -> None:
        content = gem_page(
            """
            <div class="Stats">
              <div class="property">Spell</div>
              <div class="secDescrText">A probe.</div>
            </div>
            """
        ) + """
        <table>
          <tr><th>Level</th><th>Duration</th><th>Mana</th></tr>
          <tr><td>1</td><td></td><td>5</td></tr>
        </table>
        """

        levels = by_section(build_parser().parse(raw_document(content=content)), "levels-1-1")

        assert levels.text.splitlines()[-1] == "1 |  | 5"

    def test_a_single_tag_keeps_the_tag_tuple(self) -> None:
        content = gem_page(
            """
            <div class="Stats">
              <div class="property"><a class="GemTags">Attack</a></div>
              <div class="secDescrText">A probe.</div>
            </div>
            """
        )

        properties = by_section(
            build_parser().parse(raw_document(content=content)), "properties"
        )

        assert properties.tags == ("Attack",)
        assert context_line(properties) == "Probe — Properties"
        assert body(properties) == "Attack"

    def test_a_gem_page_with_only_a_level_table_is_rejected(self) -> None:
        content = gem_page("") + """
        <table>
          <tr><th>Level</th><th>Mana</th></tr>
          <tr><td>1</td><td>5</td></tr>
        </table>
        """

        with pytest.raises(EmptyGemPageError):
            build_parser().parse(raw_document(content=content))

class TestValidityFromTheCaptureDate:
    """`valid_from` is the patch current at the capture instant, never the configured current one."""

    def test_uses_the_patch_current_at_the_capture_instant(self) -> None:
        catalog = catalog_with(("0.1.0", date(2024, 12, 6)), ("0.1.1", date(2025, 1, 17)))
        parser = Poe2dbGemPageParser(catalog)

        chunks = parser.parse(raw_document(fetched_at=datetime(2024, 12, 10, 12, tzinfo=UTC)))

        assert {chunk.validity.valid_from for chunk in chunks} == {PatchVersion.parse("0.1.0")}

    def test_a_later_local_date_normalizes_to_the_previous_utc_day(self) -> None:
        catalog = catalog_with(("0.1.0", date(2024, 12, 6)), ("0.1.1", date(2025, 1, 17)))
        parser = Poe2dbGemPageParser(catalog)
        # 2025-01-17 00:30 at UTC+5 is still 2025-01-16 in UTC, the day before the release.
        captured = datetime(2025, 1, 17, 0, 30, tzinfo=timezone(timedelta(hours=5)))

        chunks = parser.parse(raw_document(fetched_at=captured))

        assert {chunk.validity.valid_from for chunk in chunks} == {PatchVersion.parse("0.1.0")}
        assert {chunk.source.captured_at for chunk in chunks} == {date(2025, 1, 16)}

    def test_an_earlier_local_date_normalizes_to_the_next_utc_day(self) -> None:
        catalog = catalog_with(("0.1.0", date(2024, 12, 6)), ("0.1.1", date(2025, 1, 17)))
        parser = Poe2dbGemPageParser(catalog)
        # 2025-01-16 23:30 at UTC-5 is 2025-01-17 04:30 in UTC, the release day itself.
        captured = datetime(2025, 1, 16, 23, 30, tzinfo=timezone(timedelta(hours=-5)))

        chunks = parser.parse(raw_document(fetched_at=captured))

        assert {chunk.validity.valid_from for chunk in chunks} == {PatchVersion.parse("0.1.1")}
        assert {chunk.source.captured_at for chunk in chunks} == {date(2025, 1, 17)}

    def test_a_capture_before_the_first_release_is_rejected(self) -> None:
        with pytest.raises(PatchNotReleasedError):
            build_parser().parse(raw_document(fetched_at=datetime(2024, 1, 1, tzinfo=UTC)))

    def test_rejects_a_naive_fetch_time(self) -> None:
        with pytest.raises(NaiveFetchedAtError, match="timezone"):
            build_parser().parse(raw_document(fetched_at=datetime(2026, 11, 1, 12, 30)))

    def test_a_naive_fetch_time_is_rejected_before_the_page_is_read(self) -> None:
        with pytest.raises(NaiveFetchedAtError):
            build_parser().parse(
                raw_document(content="not a gem page", fetched_at=datetime(2026, 11, 1, 12, 30))
            )
