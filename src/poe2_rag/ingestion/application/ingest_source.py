"""Orchestrate the write path end to end: one locator in, validated persisted chunks out (§5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from poe2_rag.ingestion.application.errors import (
    DuplicateChunkIdError,
    UnknownPatchError,
)
from poe2_rag.ingestion.application.ports import (
    ChunkRepository,
    DocumentParser,
    SourceFetcher,
)
from poe2_rag.shared.domain import Chunk, PatchCatalog


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What one `IngestSource.execute` run did with a document.

    `chunk_ids` are every id the document produced, in document order, whether new, updated or
    skipped. `skipped` counts the chunks already stored with an identical value, so a second run over
    an unchanged snapshot reports `skipped == len(chunk_ids)` and writes nothing.
    """

    locator: str
    chunk_ids: tuple[str, ...]
    skipped: int


class IngestSource:
    """Fetch one source, parse it into chunks, validate them and persist them."""

    __slots__ = ("_catalog", "_fetcher", "_parser", "_repository")

    def __init__(
        self,
        fetcher: SourceFetcher,
        parser: DocumentParser,
        repository: ChunkRepository,
        catalog: PatchCatalog,
    ) -> None:
        self._fetcher = fetcher
        self._parser = parser
        self._repository = repository
        self._catalog = catalog

    def execute(self, locator: str) -> IngestReport:
        """Ingest the document at `locator` and report the ids it produced.

        The order is fixed: fetch, parse, reject chunks whose `valid_from` is unknown to the catalog,
        reject duplicate `chunk_id` values, then upsert only what changed. Both rejections fail loud
        (D1): dropping an unknown or repeated chunk would persist a corpus that silently disagrees
        with its source. Nothing is written when no chunk survives; a document whose chunks are all
        already stored is a no-op that reports them as skipped.
        """
        raw = self._fetcher.fetch(locator)
        chunks = tuple(self._parser.parse(raw))
        _require_known_patches(chunks, self._catalog)
        _require_distinct_ids(chunks)
        if not chunks:
            return IngestReport(locator, (), 0)
        stored = {chunk.chunk_id: chunk for chunk in self._repository.all()}
        changed = tuple(chunk for chunk in chunks if stored.get(chunk.chunk_id) != chunk)
        if changed:
            self._repository.save(changed)
        return IngestReport(
            locator,
            tuple(chunk.chunk_id for chunk in chunks),
            len(chunks) - len(changed),
        )


def _require_known_patches(chunks: Sequence[Chunk], catalog: PatchCatalog) -> None:
    """Raise `UnknownPatchError` when a chunk starts at a patch the catalog does not contain."""
    for chunk in chunks:
        if not catalog.contains(chunk.validity.valid_from):
            raise UnknownPatchError(
                f"chunk {chunk.chunk_id!r} starts at {chunk.validity.valid_from}, "
                f"which is not a known patch"
            )


def _require_distinct_ids(chunks: Sequence[Chunk]) -> None:
    """Raise `DuplicateChunkIdError` when one document resolves two chunks to the same identity."""
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.chunk_id in seen:
            raise DuplicateChunkIdError(
                f"the document produced {chunk.chunk_id!r} more than once"
            )
        seen.add(chunk.chunk_id)
