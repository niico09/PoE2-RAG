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


@runtime_checkable
class LexicalIndexWriter(Protocol):
    """Rebuild the lexical index from the whole processed corpus (the write side of §6).

    `LexicalIndex` in `answering/application/ports.py` stays the read port: search reads an index,
    rebuilding writes one, and only the composition root wires both halves to one adapter. Splitting
    them keeps the query flow from acquiring a write capability it must never use.
    """

    def rebuild(self, chunks: Sequence[Chunk]) -> None:
        """Replace the persisted index with exactly `chunks`, in one atomic step.

        The call is a full rebuild, not an upsert: a chunk the previous index stored and `chunks`
        does not is dropped. A failed rebuild leaves the previous index in place, so a reader never
        sees a truncated one. `chunks` must be non-empty: the caller owns the empty-corpus rule
        (`ReindexCorpus` rejects it before calling), and an empty sequence would persist an index
        that silently answers "no information".
        """
        ...


@runtime_checkable
class DocumentEmbedder(Protocol):
    """Turn corpus text into the vectors the dense index stores (ADR-0004).

    This is the batch half of the embedder: the query-side port, `Embedder.embed`, lives in
    `answering/application/ports.py` and takes one text. One adapter implements both, and whichever
    instruction prefix the model needs is applied on the query side only, never to documents.
    """

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Return one vector per text, in the same order and with the model's fixed dimension.

        The caller passes the whole corpus in one call; batching is the adapter's concern, because
        the batch size is a property of the model and the device, not of the corpus. A vector count
        that does not match the texts is a broken contract: `ReindexCorpus` refuses it before any
        writer runs, so a short batch never becomes a partially indexed corpus.
        """
        ...

    def overflowing_indices(self, texts: Sequence[str]) -> tuple[int, ...]:
        """Return the positions of the texts longer than the model's maximum sequence length.

        Models truncate their input silently, so the caller cannot learn from the vectors what it
        lost. Reporting the positions instead lets the reindex name the affected chunks while the
        corpus keeps serving them through the lexical index, which reads the full text (ADR-0004).
        """
        ...


@runtime_checkable
class VectorIndexWriter(Protocol):
    """Rebuild the dense index from the corpus and the vectors computed for it (§6).

    `VectorIndex` in `answering/application/ports.py` stays the read port, for the same reason the
    lexical pair is split. The adapter receives the vectors instead of computing them: the embedder
    decides how a document is embedded, and the index decides how a vector is stored and filtered.
    """

    def rebuild(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        """Replace the persisted index with `chunks` and their `vectors`, in one atomic step.

        `vectors[i]` is the embedding of `chunks[i]`: the two sequences are positional and must have
        the same length. Like the lexical rebuild it replaces the whole index instead of upserting,
        and a failed rebuild leaves the previous one in place. `chunks` must be non-empty: the
        caller owns the empty-corpus rule (`ReindexCorpus` rejects it before calling).
        """
        ...
