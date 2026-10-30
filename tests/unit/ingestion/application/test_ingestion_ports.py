"""Shape of the ingestion ports and the raw document that crosses them."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from poe2_rag.ingestion.application import (
    ChunkRepository,
    DocumentParser,
    IngestionError,
    InvalidRawDocumentError,
    RawDocument,
    SourceFetcher,
)
from poe2_rag.shared.domain import Chunk

FETCHED_AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def make_raw(**overrides: object) -> RawDocument:
    defaults: dict[str, object] = {
        "locator": "data/raw/patch-notes-0.2.0.html",
        "content": "<html><body><h1>0.2.0 Patch Notes</h1></body></html>",
        "fetched_at": FETCHED_AT,
        "source_type": "patch_notes",
    }
    return RawDocument(**{**defaults, **overrides})  # type: ignore[arg-type]


class InlineFetcher:
    """Minimal stand-in that matches `SourceFetcher` without using the shared fakes."""

    def fetch(self, locator: str) -> RawDocument:
        return make_raw(locator=locator)


class InlineParser:
    """Minimal stand-in that matches `DocumentParser` without using the shared fakes."""

    def parse(self, raw: RawDocument) -> Sequence[Chunk]:
        return ()


class InlineRepository:
    """Minimal stand-in that matches `ChunkRepository` without using the shared fakes."""

    def save(self, chunks: Sequence[Chunk]) -> None:
        return None

    def all(self) -> Sequence[Chunk]:
        return ()


class TestRawDocument:
    def test_holds_the_four_documented_fields(self) -> None:
        raw = make_raw()

        assert raw.locator == "data/raw/patch-notes-0.2.0.html"
        assert raw.content.startswith("<html>")
        assert raw.fetched_at == FETCHED_AT
        assert raw.source_type == "patch_notes"

    def test_is_frozen(self) -> None:
        raw = make_raw()

        with pytest.raises(FrozenInstanceError):
            raw.locator = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("locator", ["", "   "])
    def test_rejects_a_blank_locator(self, locator: str) -> None:
        with pytest.raises(InvalidRawDocumentError):
            make_raw(locator=locator)

    @pytest.mark.parametrize("locator", [None, 1])
    def test_rejects_a_non_string_locator(self, locator: object) -> None:
        with pytest.raises(InvalidRawDocumentError):
            make_raw(locator=locator)

    @pytest.mark.parametrize("content", [None, b"<html></html>"])
    def test_rejects_non_string_content(self, content: object) -> None:
        with pytest.raises(InvalidRawDocumentError):
            make_raw(content=content)

    def test_accepts_empty_content_for_the_parser_to_reject(self) -> None:
        assert make_raw(content="").content == ""

    @pytest.mark.parametrize("fetched_at", ["2026-10-29", date(2026, 10, 29), None])
    def test_requires_a_datetime_fetch_time(self, fetched_at: object) -> None:
        with pytest.raises(InvalidRawDocumentError):
            make_raw(fetched_at=fetched_at)

    @pytest.mark.parametrize("source_type", ["", "   ", None, 1])
    def test_rejects_a_blank_or_non_string_source_type(self, source_type: object) -> None:
        with pytest.raises(InvalidRawDocumentError):
            make_raw(source_type=source_type)


class TestProtocols:
    def test_source_fetcher_conformance_is_checkable(self) -> None:
        assert isinstance(InlineFetcher(), SourceFetcher)

    def test_document_parser_conformance_is_checkable(self) -> None:
        assert isinstance(InlineParser(), DocumentParser)

    def test_chunk_repository_conformance_is_checkable(self) -> None:
        assert isinstance(InlineRepository(), ChunkRepository)

    def test_a_partial_implementation_is_not_a_port(self) -> None:
        class OnlySave:
            def save(self, chunks: Sequence[Chunk]) -> None:
                return None

        assert not isinstance(OnlySave(), ChunkRepository)


class TestErrors:
    def test_ingestion_error_is_an_application_failure(self) -> None:
        assert issubclass(IngestionError, ValueError)

    def test_invalid_raw_document_is_an_ingestion_error(self) -> None:
        assert issubclass(InvalidRawDocumentError, IngestionError)
