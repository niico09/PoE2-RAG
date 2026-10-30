"""Outbound ports of the ingestion flow: one responsibility per protocol.

Every port is a `typing.Protocol`, so an adapter only has to match the method signatures and the use
case can run against the in-memory fakes of `tests/unit/ingestion/application/ingestion_fakes.py`
(§8). The ports know nothing about HTML, files or the network: fetching, parsing and persistence are
three separate adapter concerns.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from poe2_rag.ingestion.application.errors import InvalidRawDocumentError
from poe2_rag.shared.domain import Chunk, is_blank


@dataclass(frozen=True, slots=True)
class RawDocument:
    """Content exactly as a source served it, before any parsing.

    `locator` identifies where the content came from: an absolute URL for an HTTP fetcher, a file
    path for a local snapshot. `source_type` names the kind of source, so a parser can decide
    whether it can handle the document. `fetched_at` is the moment the snapshot was captured, not
    the moment it was read: it becomes the citation date of every chunk parsed from this document.
    """

    locator: str
    content: str
    fetched_at: datetime
    source_type: str

    def __post_init__(self) -> None:
        if is_blank(self.locator):
            raise InvalidRawDocumentError("locator must be a non-blank string")
        if not isinstance(self.content, str):
            raise InvalidRawDocumentError(f"content must be a string, got {self.content!r}")
        if not isinstance(self.fetched_at, datetime):
            raise InvalidRawDocumentError(f"fetched_at must be a datetime, got {self.fetched_at!r}")
        if is_blank(self.source_type):
            raise InvalidRawDocumentError("source_type must be a non-blank string")


@runtime_checkable
class SourceFetcher(Protocol):
    """Obtain the raw content of one source, without interpreting it."""

    def fetch(self, locator: str) -> RawDocument:
        """Return the document `locator` points at, with the moment it was captured."""
        ...


@runtime_checkable
class DocumentParser(Protocol):
    """Turn a raw document into domain chunks, one implementation per source type."""

    def parse(self, raw: RawDocument) -> Sequence[Chunk]:
        """Return the chunks `raw` describes, in document order.

        A document this parser cannot handle raises a specific error instead of returning an
        empty result: an empty result would look like a source with nothing to ingest.
        """
        ...


@runtime_checkable
class ChunkRepository(Protocol):
    """Persist the processed corpus and read it back (§6)."""

    def save(self, chunks: Sequence[Chunk]) -> None:
        """Store `chunks`, upserting by `chunk_id`.

        A chunk whose id is already stored replaces the stored value; an id that is new is added.
        Chunks not mentioned by this call are left untouched.
        """
        ...

    def all(self) -> Sequence[Chunk]:
        """Return every stored chunk once, in insertion order."""
        ...
