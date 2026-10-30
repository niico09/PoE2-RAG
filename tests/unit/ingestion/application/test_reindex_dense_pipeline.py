"""`ReindexCorpus` composed with both real adapters: one snapshot, a lexical and a dense index.

The other suites prove each half in isolation: `test_reindex_corpus.py` over fakes, `test_bm25_index.py` and
`test_chroma_index.py` over synthetic chunks, and `test_reindex_lexical_pipeline.py` over the real corpus file.
None of them proves that one `execute` call leaves two indexes that describe the same corpus and answer the
same question, which is the promise §5.3 makes. This suite runs it with no fakes in the pipeline: JSON corpus
file, BM25 on disk, Chroma on disk, and a deterministic hashing embedder in place of the model so it stays
offline.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from poe2_rag.answering.adapters.outbound.dense.chroma_index import ChromaVectorIndex
from poe2_rag.answering.adapters.outbound.lexical.bm25_index import Bm25Index
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
DIMENSION = 16


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
        validity=Validity(parsed, None if valid_until is None else PatchVersion.parse(valid_until)),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
    )


class HashedBagOfWords:
    """Deterministic, normalized stand-in for the model: one bucket per hashed word."""

    model_id = "tests-hashed-bag-of-words"
    dimension = DIMENSION

    def embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimension
        for word in text.lower().split():
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=4).digest()
            vector[int.from_bytes(digest, "big") % self.dimension] += 1.0
        norm = sum(value * value for value in vector) ** 0.5
        return tuple(value / norm for value in vector) if norm else tuple(vector)

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.embed(text) for text in texts)

    def overflowing_indices(self, texts: Sequence[str]) -> tuple[int, ...]:
        return ()


def seeded_corpus(tmp_path: Path) -> tuple[JsonChunkRepository, tuple[Chunk, ...]]:
    """Persist a tiny corpus through the real repository, exactly as ingestion would."""
    repository = JsonChunkRepository(tmp_path / "data" / "processed")
    chunks = (
        make_chunk("chaining", text="Spark lightning chains between enemies."),
        make_chunk("cold-cone", text="Glacial Cascade releases a freezing cone."),
        make_chunk("spark-mana", text="Spark mana cost raised to twelve."),
    )
    repository.save(chunks)
    return repository, chunks


def reindexed(
    tmp_path: Path,
) -> tuple[ChromaVectorIndex, Bm25Index, HashedBagOfWords, tuple[Chunk, ...]]:
    """Rebuild both indexes from the persisted corpus, as `bootstrap.py` will."""
    repository, chunks = seeded_corpus(tmp_path)
    embedder = HashedBagOfWords()
    vector_index = ChromaVectorIndex(tmp_path / "data" / "index" / "chroma", embedder=embedder)
    lexical_index = Bm25Index(tmp_path / "data" / "index")
    report = ReindexCorpus(
        repository,
        lexical_index,
        document_embedder=embedder,
        vector_writer=vector_index,
    ).execute()

    assert report.chunk_count == len(chunks)
    return vector_index, lexical_index, embedder, chunks


class TestOneSnapshot:
    def test_both_indexes_hold_the_chunks_the_corpus_stored(self, tmp_path: Path) -> None:
        vector_index, lexical_index, embedder, chunks = reindexed(tmp_path)

        lexical = lexical_index.search("spark", TARGET, 10)
        dense = vector_index.search(embedder.embed("spark"), TARGET, 10)

        # BM25 drops a chunk no query term matches; the dense index always returns its nearest.
        assert {item.chunk.chunk_id for item in lexical} == {chunks[0].chunk_id, chunks[2].chunk_id}
        assert {item.chunk.chunk_id for item in dense} == {chunk.chunk_id for chunk in chunks}

    def test_both_indexes_return_the_same_chunk_object_the_corpus_stored(
        self, tmp_path: Path
    ) -> None:
        vector_index, lexical_index, embedder, chunks = reindexed(tmp_path)

        (lexical,) = lexical_index.search("glacial cascade", TARGET, 1)
        (dense,) = vector_index.search(embedder.embed("glacial cascade"), TARGET, 1)

        assert lexical.chunk == dense.chunk == chunks[1]

    def test_the_dense_index_scores_only_its_own_stage(self, tmp_path: Path) -> None:
        vector_index, _, embedder, _ = reindexed(tmp_path)

        (result,) = vector_index.search(embedder.embed("glacial cascade"), TARGET, 1)

        assert result.scores.dense is not None
        assert result.scores.lexical is None


class TestValidityAcrossThePipeline:
    def test_neither_index_returns_a_chunk_stale_at_the_target_patch(self, tmp_path: Path) -> None:
        repository, current = seeded_corpus(tmp_path)
        stale = make_chunk(
            "chaining-old",
            text="Spark lightning chains between enemies.",
            version="0.1.0",
            valid_until="0.2.0",
        )
        repository.save((stale,))
        embedder = HashedBagOfWords()
        vector_index = ChromaVectorIndex(tmp_path / "data" / "index" / "chroma", embedder=embedder)
        lexical_index = Bm25Index(tmp_path / "data" / "index")
        ReindexCorpus(
            repository,
            lexical_index,
            document_embedder=embedder,
            vector_writer=vector_index,
        ).execute()

        lexical = lexical_index.search("spark lightning chains", TARGET, 10)
        dense = vector_index.search(embedder.embed("spark lightning chains"), TARGET, 10)

        assert stale.chunk_id not in {item.chunk.chunk_id for item in lexical}
        assert stale.chunk_id not in {item.chunk.chunk_id for item in dense}
        assert {item.chunk.chunk_id for item in dense} == {chunk.chunk_id for chunk in current}
