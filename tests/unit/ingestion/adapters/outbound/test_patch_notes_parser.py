"""Official forum patch-notes parser: version detection, section splitting and text cleanup."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import (
    InvalidTokenBudgetError,
    MissingPatchNotesBodyError,
    PatchNotesParseError,
    PatchNotesParser,
    PatchVersionNotFoundError,
    PatchVersionUnparseableError,
    ThreadUrlNotFoundError,
    UnsupportedSourceTypeError,
    estimate_tokens,
)
from poe2_rag.ingestion.application import DocumentParser, RawDocument
from poe2_rag.shared.domain import EntityRef, PatchVersion, SourceRef, Validity

FIXTURES = Path(__file__).parents[4] / "fixtures" / "patch_notes"
CAPTURED_AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_raw(
    name: str, locator: str | None = None, source_type: str = "patch_notes"
) -> RawDocument:
    return RawDocument(
        locator=locator if locator is not None else f"data/raw/{name}",
        content=fixture(name),
        fetched_at=CAPTURED_AT,
        source_type=source_type,
    )


class TestProtocol:
    def test_parser_is_a_document_parser(self) -> None:
        assert isinstance(PatchNotesParser(), DocumentParser)


class TestVersionDetection:
    def test_detects_valid_version_from_title(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        version = PatchVersion.parse("0.2.0")
        for chunk in chunks:
            assert chunk.entity == EntityRef("patch_notes", "0-2-0", "Patch 0.2.0")
            assert chunk.validity == Validity(version)

    def test_missing_version_raises(self) -> None:
        with pytest.raises(PatchVersionNotFoundError):
            PatchNotesParser().parse(make_raw("no_version_thread.html"))

    def test_finds_the_patch_version_after_another_number(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("title_with_game_name.html"))

        assert chunks[0].validity.valid_from == PatchVersion.parse("0.5.0")
        assert chunks[0].entity.slug == "0-5-0"

    def test_malformed_two_component_version_raises(self) -> None:
        with pytest.raises(PatchVersionUnparseableError) as exc_info:
            PatchNotesParser().parse(make_raw("malformed_two_component.html"))

        assert "0.2" in str(exc_info.value)

    def test_an_unparseable_version_names_the_version_token_not_another_number(self) -> None:
        """A still-unknown format is a specific error; `Path of Exile 2` must not shadow it."""
        content = fixture("suffixed_version.html").replace(
            "0.5.5e Patch Notes", "Path of Exile 2 - 0.5.5E Patch Notes"
        )
        raw = RawDocument(
            locator="data/raw/uppercase_hotfix.html",
            content=content,
            fetched_at=CAPTURED_AT,
            source_type="patch_notes",
        )

        with pytest.raises(PatchVersionUnparseableError) as exc_info:
            PatchNotesParser().parse(raw)

        assert "0.5.5E" in str(exc_info.value)
        assert "'2'" not in str(exc_info.value)

    def test_a_version_glued_to_a_word_is_not_read_as_a_hotfix(self) -> None:
        """`0.5.5hotfix` must not become `0.5.5h`: a word's first letter is not a hotfix (S5)."""
        content = fixture("suffixed_version.html").replace(
            "0.5.5e Patch Notes", "0.5.5hotfix Patch Notes"
        )
        raw = RawDocument(
            locator="data/raw/glued_hotfix.html",
            content=content,
            fetched_at=CAPTURED_AT,
            source_type="patch_notes",
        )

        with pytest.raises(PatchVersionUnparseableError) as exc_info:
            PatchNotesParser().parse(raw)

        assert "0.5.5hotfix" in str(exc_info.value)

    def test_a_hotfix_thread_is_an_entity_of_its_own(self) -> None:
        """Each forum thread is its own delta, so `0.5.5e` must not supersede `0.5.5`'s notes."""
        chunks = PatchNotesParser().parse(make_raw("suffixed_version.html"))

        assert chunks[0].entity.key == "patch_notes:0-5-5e"
        assert chunks[0].chunk_id.startswith("patch_notes:patch_notes:0-5-5e@0.5.5e#")

    def test_a_base_version_thread_keeps_the_plain_entity_key(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        assert {chunk.entity.key for chunk in chunks} == {"patch_notes:0-2-0"}

    def test_a_suffixed_version_parses_as_a_hotfix(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("suffixed_version.html"))

        assert chunks[0].validity.valid_from == PatchVersion.parse("0.5.5e")
        assert chunks[0].chunk_id.endswith("@0.5.5e#notes")


class TestThreadUrl:
    def test_builds_url_from_post_reply_link(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        for chunk in chunks:
            assert chunk.source == SourceRef(
                "patch_notes",
                "https://www.pathofexile.com/forum/view-thread/7654321",
                date(2026, 10, 29),
            )

    def test_builds_url_from_view_thread_link_when_no_post_reply(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("view_thread_fallback.html"))

        assert (
            chunks[0].source.url
            == "https://www.pathofexile.com/forum/view-thread/9876543"
        )

    def test_absolute_locator_wins_over_document_links(self) -> None:
        locator = "https://www.pathofexile.com/forum/view-thread/9999999"
        chunks = PatchNotesParser().parse(
            make_raw("full_020_thread.html", locator=locator)
        )

        for chunk in chunks:
            assert chunk.source.url == locator

    def test_missing_thread_link_raises(self) -> None:
        with pytest.raises(ThreadUrlNotFoundError):
            PatchNotesParser().parse(make_raw("no_thread_link.html"))


class TestBodyExtraction:
    def test_community_replies_are_excluded(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        for chunk in chunks:
            assert "community reply" not in chunk.text.lower()

    def test_later_staff_posts_are_not_notes(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("two_staff_posts_thread.html"))

        assert [chunk.section for chunk in chunks] == ["general-changes"]
        assert "First post change." in chunks[0].text
        assert "Staff reply about a skill" not in chunks[0].text

    def test_no_staff_post_raises(self) -> None:
        with pytest.raises(MissingPatchNotesBodyError):
            PatchNotesParser().parse(make_raw("community_only_thread.html"))


class TestSectionSplitting:
    def test_one_chunk_per_top_level_section(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        assert [chunk.section for chunk in chunks] == [
            "overview",
            "general-changes",
            "skill-changes",
        ]

    def test_chunk_ids(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        assert [chunk.chunk_id for chunk in chunks] == [
            "patch_notes:patch_notes:0-2-0@0.2.0#overview",
            "patch_notes:patch_notes:0-2-0@0.2.0#general-changes",
            "patch_notes:patch_notes:0-2-0@0.2.0#skill-changes",
        ]

    def test_preamble_becomes_overview(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        overview = chunks[0]
        assert overview.section == "overview"
        assert overview.text == "This patch introduces major changes."

    def test_repeated_title_heading_is_dropped(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        for chunk in chunks:
            assert "0.2.0 Patch Notes" not in chunk.text
            assert chunk.section != "0-2-0-patch-notes"

    def test_title_heading_after_an_intro_is_not_a_section(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("title_not_first_thread.html"))

        assert [chunk.section for chunk in chunks] == ["overview", "general-changes"]
        assert chunks[0].text == "Intro before the title heading."

    def test_duplicate_sections_are_merged(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("duplicate_sections_thread.html"))

        assert [chunk.section for chunk in chunks] == [
            "overview",
            "general-changes",
            "fireball-buffs",
        ]
        by_section = {chunk.section: chunk.text for chunk in chunks}
        assert by_section["overview"] == "Intro paragraph.\nOverview body."
        assert by_section["general-changes"] == (
            "- First general change.\n- Second general change."
        )
        assert by_section["fireball-buffs"] == (
            "Fireball buff detail.\nMore fireball buff detail."
        )

    def test_empty_headings_are_skipped(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("empty_heading_thread.html"))

        assert [chunk.section for chunk in chunks] == ["general-changes"]
        assert chunks[0].text == "First change.\nSecond change.\nThird change."

    def test_a_document_with_only_a_title_raises(self) -> None:
        with pytest.raises(PatchNotesParseError) as exc_info:
            PatchNotesParser().parse(make_raw("title_only_thread.html"))

        assert type(exc_info.value) is PatchNotesParseError

    def test_nested_heading_stays_inside_section(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        sections = {chunk.section: chunk.text for chunk in chunks}
        assert "specific-skill" not in sections
        assert "Specific Skill" in sections["skill-changes"]
        assert "Some detail about a specific skill." in sections["skill-changes"]

    def test_hotfix_without_headings_becomes_one_notes_chunk(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("hotfix_011_thread.html"))

        assert len(chunks) == 1
        chunk = chunks[0]
        assert chunk.section == "notes"
        assert chunk.text == (
            "Fixed a crash when opening the map.\n- Fixed bug A.\n- Fixed bug B."
        )


class TestTextRendering:
    def test_renders_tables_and_nested_blocks_as_lines(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("table_and_blocks_thread.html"))

        assert chunks[0].text == (
            "Skill | Before | After\n"
            "Fireball | 10 | 15\n"
            "Ice Nova | 20 | 25\n"
            "First paragraph.\n"
            "Second paragraph.\n"
            "- Cost 10\n"
            "Effect: none\n"
            "Fireball is a skill."
        )

    def test_keeps_bullet_lines_and_strips_inline_markup(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        general = next(chunk for chunk in chunks if chunk.section == "general-changes")
        assert "- Increased life for all monsters." in general.text
        assert "- Reduced mana costs for low-level gems." in general.text
        assert "- See the full manifesto for details." in general.text
        assert "- Except for endgame gems." in general.text.splitlines()
        assert "low-level gems. Except" not in general.text
        assert "<strong>" not in general.text
        assert "<em>" not in general.text
        assert "<a" not in general.text


class TestSourceType:
    def test_rejects_a_document_from_another_source(self) -> None:
        raw = make_raw("full_020_thread.html", source_type="poe2db")

        with pytest.raises(UnsupportedSourceTypeError) as exc_info:
            PatchNotesParser().parse(raw)

        assert "poe2db" in str(exc_info.value)


class TestErrors:
    def test_empty_document_raises_base_error(self) -> None:
        with pytest.raises(PatchNotesParseError) as exc_info:
            PatchNotesParser().parse(make_raw("empty.html"))

        assert type(exc_info.value) is PatchNotesParseError

    def test_garbage_html_raises_base_error(self) -> None:
        with pytest.raises(PatchNotesParseError):
            PatchNotesParser().parse(make_raw("garbage.html"))

    def test_parse_errors_are_environment_failures(self) -> None:
        assert issubclass(PatchNotesParseError, RuntimeError)
        assert issubclass(PatchVersionNotFoundError, PatchNotesParseError)
        assert issubclass(PatchVersionUnparseableError, PatchNotesParseError)
        assert issubclass(MissingPatchNotesBodyError, PatchNotesParseError)
        assert issubclass(ThreadUrlNotFoundError, PatchNotesParseError)
        assert issubclass(UnsupportedSourceTypeError, PatchNotesParseError)


class TestTitleFallback:
    """The title is not always an `h1`: a Content Update thread only has the document title."""

    def test_a_content_update_thread_falls_back_to_the_open_graph_title(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("content_update_thread.html"))

        assert {chunk.entity.key for chunk in chunks} == {"patch_notes:0-5-5"}
        assert {chunk.validity.valid_from for chunk in chunks} == {
            PatchVersion.parse("0.5.5")
        }
        assert any("Start fresh in a new economy" in chunk.text for chunk in chunks)

    def test_the_open_graph_title_wins_over_the_title_element(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("og_title_only_thread.html"))

        assert {chunk.entity.key for chunk in chunks} == {"patch_notes:0-1-1"}

    def test_the_title_element_is_the_last_resort(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("title_element_only_thread.html"))

        assert {chunk.entity.key for chunk in chunks} == {"patch_notes:0-1-2"}

    def test_the_top_bar_heading_wins_over_the_open_graph_title(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("h1_precedes_meta_thread.html"))

        assert {chunk.entity.key for chunk in chunks} == {"patch_notes:0-1-0"}

    def test_a_page_without_any_title_raises(self) -> None:
        raw = RawDocument(
            locator="data/raw/untitled.html",
            content="<html><body><p>nothing</p></body></html>",
            fetched_at=CAPTURED_AT,
            source_type="patch_notes",
        )

        with pytest.raises(PatchVersionNotFoundError) as exc_info:
            PatchNotesParser().parse(raw)

        assert "no thread title found" in str(exc_info.value)


class TestNewsPostBody:
    """Content Update threads use `tr.newsPost > td > div.content`, not `tr.staff`."""

    def test_the_news_post_body_is_the_first_staff_post_body(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("content_update_thread.html"))

        text = "\n".join(chunk.text for chunk in chunks)
        assert "Start fresh in a new economy" in text
        assert "Report Account" not in text

    def test_a_thread_without_any_post_still_raises(self) -> None:
        with pytest.raises(MissingPatchNotesBodyError):
            PatchNotesParser().parse(make_raw("community_only_thread.html"))

    def test_the_earliest_post_wins_across_both_layouts(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("mixed_layout_thread.html"))

        text = "\n".join(chunk.text for chunk in chunks)
        assert "OP BODY" in text
        assert "REPLY BODY" not in text

    def test_a_news_post_body_in_a_later_cell_is_found(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("news_post_second_cell_thread.html"))

        text = "\n".join(chunk.text for chunk in chunks)
        assert "REAL BODY" in text
        assert "LATER BODY" not in text

    def test_a_staff_body_comes_from_the_content_cell_only(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("staff_body_anchor_thread.html"))

        text = "\n".join(chunk.text for chunk in chunks)
        assert "REAL BODY" in text
        assert "NOT THE BODY" not in text

    def test_a_staff_row_without_a_content_cell_is_not_a_post(self) -> None:
        raw = RawDocument(
            locator="data/raw/staff_without_content_cell.html",
            content=(
                "<html><head><title>0.9.1 Patch Notes</title></head><body>"
                "<table><tr class='staff'><td>"
                "<div class='content'><h3>0.9.1 Patch Notes</h3><p>WRONG BODY</p></div>"
                "</td></tr></table>"
                "<a href='/forum/post-reply/1414141/quote/1'>Quote</a></body></html>"
            ),
            fetched_at=CAPTURED_AT,
            source_type="patch_notes",
        )

        with pytest.raises(MissingPatchNotesBodyError):
            PatchNotesParser().parse(raw)


class TestLayoutWrappers:
    """Only the forum's layout boxes are unwrapped; a heading nested in content stays text."""

    def test_a_list_holding_a_nested_heading_is_not_split(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("nested_heading_list_thread.html"))

        assert [chunk.section for chunk in chunks] == ["notes"]
        assert chunks[0].text.split("\n") == [
            "- alpha",
            "- beta",
            "Nested Heading",
            "- gamma",
        ]

    def test_a_box_holding_a_nested_heading_is_not_split(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("nested_box_heading_thread.html"))

        assert [chunk.section for chunk in chunks] == ["notes"]
        assert "Hidden text inside the box." in chunks[0].text


class TestBannerHeading:
    """A lone leading heading banners the thread; the sections live one level below it."""

    def test_a_content_update_thread_splits_below_its_banner(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("content_update_thread.html"))

        assert [chunk.section for chunk in chunks] == [
            "the-forbidden-rites-event-league",
            "endgame-changes",
        ]

    def test_the_banner_heading_is_not_a_section(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("content_update_thread.html"))

        assert "Content Update 0.5.5" not in "\n".join(chunk.text for chunk in chunks)

    def test_the_banner_stylesheet_never_reaches_the_notes(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("content_update_thread.html"))

        assert ".newsPost li" not in "\n".join(chunk.text for chunk in chunks)

    def test_a_page_without_a_banner_keeps_the_shallowest_split(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("full_020_thread.html"))

        assert [chunk.section for chunk in chunks] == [
            "overview",
            "general-changes",
            "skill-changes",
        ]


class TestNavigationSections:
    """"Table of Contents" and "Report Forum Post" are page furniture, not notes."""

    def test_the_table_of_contents_is_dropped(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("navigation_headings_thread.html"))

        assert "table-of-contents" not in [chunk.section for chunk in chunks]
        assert not any(
            "- Real Changes" in chunk.text for chunk in chunks
        ), "the table-of-contents links became notes text"

    def test_the_report_forum_post_heading_is_dropped(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("navigation_headings_thread.html"))

        assert "report-forum-post" not in [chunk.section for chunk in chunks]
        assert "Report Account" not in "\n".join(chunk.text for chunk in chunks)

    def test_the_section_after_a_navigation_heading_is_kept(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("navigation_headings_thread.html"))

        assert [chunk.section for chunk in chunks] == ["real-changes", "extra-changes"]


class TestTokenBudgetBlocks:
    """A section wider than the budget becomes consecutive, self-describing blocks."""

    def test_an_oversized_section_becomes_consecutive_blocks(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("oversized_section_thread.html"))

        assert len(chunks) > 1
        assert [chunk.section for chunk in chunks] == [
            f"trial-of-chaos-improvements-{index:02d}"
            for index in range(1, len(chunks) + 1)
        ]

    def test_every_block_repeats_its_context_line(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("oversized_section_thread.html"))
        total = len(chunks)

        assert [chunk.text.split("\n")[0] for chunk in chunks] == [
            f"Patch 0.5.5 — Trial of Chaos Improvements (part {part}/{total})"
            for part in range(1, total + 1)
        ]

    def test_the_blocks_union_the_section_text_without_loss_or_duplication(self) -> None:
        whole = PatchNotesParser(max_block_tokens=100_000).parse(
            make_raw("oversized_section_thread.html")
        )
        blocks = PatchNotesParser().parse(make_raw("oversized_section_thread.html"))

        (section,) = whole
        assert section.section == "trial-of-chaos-improvements"
        bodies = ["\n".join(chunk.text.split("\n")[1:]) for chunk in blocks]
        assert "\n".join(bodies) == section.text

    def test_every_block_stays_inside_the_budget(self) -> None:
        budget = 240
        chunks = PatchNotesParser(max_block_tokens=budget).parse(
            make_raw("oversized_section_thread.html")
        )

        assert len(chunks) > 2
        assert all(estimate_tokens(chunk.text) <= budget for chunk in chunks)

    def test_a_section_that_fits_keeps_its_id(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("content_update_thread.html"))

        assert [chunk.section for chunk in chunks] == [
            "the-forbidden-rites-event-league",
            "endgame-changes",
        ]

    def test_the_no_heading_case_blocks_under_notes(self) -> None:
        chunks = PatchNotesParser().parse(make_raw("notes_only_thread.html"))
        total = len(chunks)

        assert total > 1
        assert [chunk.section for chunk in chunks] == [
            f"notes-{index:02d}" for index in range(1, total + 1)
        ]
        assert [chunk.text.split("\n")[0] for chunk in chunks] == [
            f"Patch 0.5.5c — Notes (part {part}/{total})" for part in range(1, total + 1)
        ]

    def test_the_budget_is_a_constructor_parameter(self) -> None:
        default = PatchNotesParser().parse(make_raw("oversized_section_thread.html"))
        tight = PatchNotesParser(max_block_tokens=40).parse(
            make_raw("oversized_section_thread.html")
        )
        width = len(str(len(tight)))

        assert len(tight) > len(default)
        assert [chunk.section for chunk in tight] == [
            f"trial-of-chaos-improvements-{index:0{width}d}"
            for index in range(1, len(tight) + 1)
        ]

    def test_an_invalid_budget_raises_a_specific_error(self) -> None:
        with pytest.raises(InvalidTokenBudgetError):
            PatchNotesParser(max_block_tokens=0)

        with pytest.raises(InvalidTokenBudgetError):
            PatchNotesParser(max_block_tokens=-3)

    def test_an_invalid_budget_is_a_configuration_error(self) -> None:
        assert issubclass(InvalidTokenBudgetError, ValueError)
        assert not issubclass(InvalidTokenBudgetError, PatchNotesParseError)
