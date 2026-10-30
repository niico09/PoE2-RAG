"""Rebuild the persisted indexes from the processed corpus: the tail of the write path (§5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from poe2_rag.ingestion.application.errors import EmptyCorpusError
from poe2_rag.ingestion.application.ports import ChunkRepository, LexicalIndexWriter


def _utc_now() -> datetime:
    """Return the current moment in UTC, so every report is comparable and timezone-aware."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ReindexReport:
    """What one `ReindexCorpus.execute` run rebuilt.

    `chunk_count` is the size of the corpus the run read, so a report whose count equals the corpus
    size proves the index was rebuilt from all of it. `rebuilt_at` is the moment the run finished,
    injected by the composition root as a clock so a test can pin it instead of waiting.
    """

    chunk_count: int
    rebuilt_at: datetime


class ReindexCorpus:
    """Read the whole processed corpus and rebuild every index that serves queries from it."""

    __slots__ = ("_clock", "_lexical_writer", "_repository")

    def __init__(
        self,
        repository: ChunkRepository,
        lexical_writer: LexicalIndexWriter,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._lexical_writer = lexical_writer
        self._clock = clock

    def execute(self) -> ReindexReport:
        """Rebuild the lexical index from every stored chunk and report the run.

        The corpus is read once, into an immutable snapshot, and handed to each writer in full: an
        index is a projection of the corpus, so it may never be rebuilt from a slice of it. An empty
        corpus raises `EmptyCorpusError` before any writer runs (D1): an index built from nothing
        would answer "no information" for every question, and replacing a good index with it would
        turn a broken reindex into a silent regression. A writer that fails propagates its error, so
        the run never reports an index it did not rebuild.

        Seam for the next task: the vector writer rebuilds from this same `chunks` snapshot right
        after the lexical one. Both indexes must describe one corpus, so the snapshot is taken here
        once rather than letting each writer read the repository and disagree with the other.
        """
        chunks = tuple(self._repository.all())
        if not chunks:
            raise EmptyCorpusError("cannot reindex an empty corpus: no chunk to index")
        self._lexical_writer.rebuild(chunks)
        return ReindexReport(chunk_count=len(chunks), rebuilt_at=self._clock())
