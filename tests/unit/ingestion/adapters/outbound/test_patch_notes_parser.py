"""Official forum patch-notes parser: version detection, section splitting and text cleanup."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import (
    MissingPatchNotesBodyError,
    PatchNotesParseError,
    PatchNotesParser,
    PatchVersionNotFoundError,
    PatchVersionUnparseableError,
    ThreadUrlNotFoundError,
    UnsupportedSourceTypeError,
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

    def test_suffixed_version_raises(self) -> None:
        with pytest.raises(PatchVersionUnparseableError) as exc_info:
            PatchNotesParser().parse(make_raw("suffixed_version.html"))

        assert "0.5.5e" in str(exc_info.value)


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
