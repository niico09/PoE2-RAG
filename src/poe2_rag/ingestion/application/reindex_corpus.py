"""Rebuild the persisted indexes from the processed corpus: the tail of the write path (§5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from poe2_rag.ingestion.application.errors import (
    EmbedderContractError,
    EmptyCorpusError,
)
from poe2_rag.ingestion.application.ports import (
    ChunkRepository,
    DocumentEmbedder,
    LexicalIndexWriter,
    VectorIndexWriter,
)
from poe2_rag.shared.domain import Chunk


def _utc_now() -> datetime:
    """Return the current moment in UTC, so every report is comparable and timezone-aware."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ReindexReport:
    """What one `ReindexCorpus.execute` run rebuilt.

    `chunk_count` is the size of the corpus the run read, so a report whose count equals the corpus
    size proves the indexes were rebuilt from all of it. `rebuilt_at` is the moment the run finished,
    injected by the composition root as a clock so a test can pin it instead of waiting.
    `truncated_chunk_ids` names the chunks whose text exceeded the embedding model's maximum sequence
    length: the embeddings saw a prefix of them, so the operator can tell which chunks the dense index
    can only reach through that prefix. The lexical index still reads their full text, which is why
    truncation is reported instead of failing the run (ADR-0004).
    """

    chunk_count: int
    rebuilt_at: datetime
    truncated_chunk_ids: tuple[str, ...] = ()


class ReindexCorpus:
    """Read the whole processed corpus and rebuild every index that serves queries from it."""

    __slots__ = ("_clock", "_document_embedder", "_lexical_writer", "_repository", "_vector_writer")

    def __init__(
        self,
        repository: ChunkRepository,
        lexical_writer: LexicalIndexWriter,
        *,
        document_embedder: DocumentEmbedder,
        vector_writer: VectorIndexWriter,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._lexical_writer = lexical_writer
        self._document_embedder = document_embedder
        self._vector_writer = vector_writer
        self._clock = clock

    def execute(self) -> ReindexReport:
        """Embed the corpus, rebuild both indexes from one snapshot and report the run.

        The corpus is read once, into an immutable snapshot, and handed to each writer in full: an
        index is a projection of the corpus, so it may never be rebuilt from a slice of it. An empty
        corpus raises `EmptyCorpusError` before the embedder or any writer runs (D1): an index built
        from nothing would answer "no information" for every question, and replacing a good index
        with it would turn a broken reindex into a silent regression.

        The embedding happens before the first write, so a model that cannot run leaves both persisted
        indexes untouched instead of leaving a rebuilt lexical index beside a stale dense one. A
        writer that fails still propagates its error after the writes that already happened: the two
        indexes are atomic one by one, not as a pair (§10).
        """
        chunks = tuple(self._repository.all())
        if not chunks:
            raise EmptyCorpusError("cannot reindex an empty corpus: no chunk to index")
        texts = tuple(chunk.text for chunk in chunks)
        vectors = self._document_embedder.embed_documents(texts)
        if len(vectors) != len(chunks):
            raise EmbedderContractError(
                f"the embedder returned {len(vectors)} vectors for {len(chunks)} chunk texts"
            )
        truncated = self._truncated_chunk_ids(chunks, texts)
        self._lexical_writer.rebuild(chunks)
        self._vector_writer.rebuild(chunks, vectors)
        return ReindexReport(
            chunk_count=len(chunks),
            rebuilt_at=self._clock(),
            truncated_chunk_ids=truncated,
        )

    def _truncated_chunk_ids(
        self, chunks: tuple[Chunk, ...], texts: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Map the embedder's overflow positions to chunk ids, once each and in corpus order.

        An index outside the batch is a broken contract: it would name a chunk that was never
        embedded, so the run stops before a writer turns it into a misleading report.
        """
        ids: list[str] = []
        seen: set[int] = set()
        for index in self._document_embedder.overflowing_indices(texts):
            if not 0 <= index < len(chunks):
                raise EmbedderContractError(
                    f"the embedder reported overflow at position {index}, "
                    f"outside the {len(chunks)} chunk texts it was given"
                )
            if index in seen:
                continue
            seen.add(index)
            ids.append(chunks[index].chunk_id)
        return tuple(ids)
