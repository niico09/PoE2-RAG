"""The reindex flow composed end to end: the JSON corpus file, `ReindexCorpus` and the BM25 index.

`test_reindex_corpus.py` proves the use case against fakes and `test_bm25_index.py` proves the adapter on
synthetic chunks. Neither proves that the two halves compose, nor that the chunk layout the repository
writes is the one the index reads back. This suite covers that seam with no fakes at all.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from poe2_rag.answering.adapters.outbound.lexical.bm25_index import FILE_NAME, Bm25Index
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)
from poe2_rag.ingestion.application import ReindexCorpus
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    PatchVersion,
    SourceRef,
    Validity,
)

THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)
TARGET = PatchVersion.parse("0.2.0")


def make_chunk(
    section: str,
    text: str,
    version: str = "0.2.0",
    *,
    valid_until: str | None = None,
) -> Chunk:
    """Build one chunk in the shape the ingestion parser produces."""
    parsed = PatchVersion.parse(version)
    slug = f"{parsed.major}-{parsed.minor}-{parsed.patch}"
    return Chunk(
        entity=EntityRef("patch_notes", slug, f"Patch {parsed}"),
        validity=Validity(
            parsed, None if valid_until is None else PatchVersion.parse(valid_until)
        ),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
    )


def seeded_corpus(tmp_path: Path) -> tuple[JsonChunkRepository, tuple[Chunk, ...]]:
    """Persist a tiny corpus through the real repository, exactly as ingestion would."""
    repository = JsonChunkRepository(tmp_path / "data" / "processed")
    chunks = (
        make_chunk(
            "balance-changes",
            text="Spark spark spark damage.",
            version="0.1.0",
            valid_until="0.2.0",
        ),
        make_chunk("skill-changes", text="Spark costs 12 mana."),
        make_chunk("bug-fixes", text="Fixed a crash."),
    )
    repository.save(chunks)
    return repository, chunks


def reindexed(tmp_path: Path) -> tuple[JsonChunkRepository, tuple[Chunk, ...], Path]:
    """Persist the corpus and rebuild the index from it, as the composition root will."""
    repository, chunks = seeded_corpus(tmp_path)
    index_directory = tmp_path / "data" / "index"
    ReindexCorpus(repository, Bm25Index(index_directory)).execute()
    return repository, chunks, index_directory


class TestComposition:
    def test_reindex_reports_the_whole_persisted_corpus(self, tmp_path: Path) -> None:
        repository, chunks = seeded_corpus(tmp_path)

        report = ReindexCorpus(repository, Bm25Index(tmp_path / "data" / "index")).execute()

        assert report.chunk_count == len(chunks)
        assert report.rebuilt_at.tzinfo is not None

    def test_a_fresh_reader_answers_from_the_reindexed_corpus(self, tmp_path: Path) -> None:
        """The reader is a new instance, as the composition root will build it."""
        _, chunks, index_directory = reindexed(tmp_path)

        (result,) = Bm25Index(index_directory).search("spark", TARGET, 10)

        assert result.chunk == chunks[1]
        assert result.scores.lexical is not None
        assert result.scores.lexical > 0

    def test_the_stale_chunk_is_not_searchable_at_the_target_patch(self, tmp_path: Path) -> None:
        _, _, index_directory = reindexed(tmp_path)

        results = Bm25Index(index_directory).search("spark", TARGET, 10)

        assert [item.chunk.section for item in results] == ["skill-changes"]


class TestEncodingAgreement:
    def test_the_index_stores_the_chunk_the_corpus_stores(self, tmp_path: Path) -> None:
        """Both files share one chunk layout, so a schema change cannot drift on one side only."""
        repository, _, index_directory = reindexed(tmp_path)

        corpus_chunks = json.loads(repository.path.read_text(encoding="utf-8"))["chunks"]
        index_document = json.loads(
            (index_directory / FILE_NAME).read_text(encoding="utf-8")
        )
        index_chunks: list[dict[str, Any]] = [
            entry["chunk"] for entry in index_document["documents"]
        ]

        assert {chunk["section"]: chunk for chunk in index_chunks} == {
            chunk["section"]: chunk for chunk in corpus_chunks
        }

    def test_the_corpus_round_trips_unchanged(self, tmp_path: Path) -> None:
        repository, chunks, _ = reindexed(tmp_path)

        assert repository.all() == chunks
