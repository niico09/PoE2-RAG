"""Contract every `VectorIndex` implementation must honour.

The port promises the same validity boundaries as `LexicalIndex`: only chunks valid at the target
patch come back, the best match comes first, and every result carries only its dense score. The
scenarios are reused from `test_lexical_index_contract` so the two indexes stay aligned.

This module uses the real Chroma adapter and a deterministic fake embedder; the contract itself is
about the port's behaviour, not the storage backend.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from hashing_embedder import HashingEmbedder
from test_lexical_index_contract import (
    VALIDITY_CASES,
    ValidityCase,
    chunk_ids,
    make_chunk,
)

from poe2_rag.answering.adapters.outbound.dense.chroma_index import ChromaVectorIndex
from poe2_rag.answering.application import VectorIndex
from poe2_rag.answering.domain import RetrievedChunk
from poe2_rag.shared.domain import Chunk, PatchVersion, Validity

TARGET = PatchVersion.parse("0.2.0")


BuildIndex = Callable[[Sequence[Chunk]], VectorIndex]


@pytest.fixture
def build_index(tmp_path: Path) -> BuildIndex:
    """Build a `ChromaVectorIndex` from a corpus, ready to search."""
    def _build(chunks: Sequence[Chunk]) -> VectorIndex:
        embedder = HashingEmbedder()
        index = ChromaVectorIndex(tmp_path, embedder=embedder)
        vectors = embedder.embed_documents([chunk.text for chunk in chunks])
        index.rebuild(chunks, vectors)
        return index

    return _build


class TestPortSurface:
    def test_builds_a_vector_index(self, build_index: BuildIndex) -> None:
        assert isinstance(build_index((make_chunk(),)), VectorIndex)


class TestValidityFilter:
    @pytest.mark.parametrize("case", VALIDITY_CASES, ids=[case.name for case in VALIDITY_CASES])
    def test_returns_exactly_the_chunks_valid_at_the_target_patch(
        self, build_index: BuildIndex, case: ValidityCase
    ) -> None:
        chunk = make_chunk(validity=case.validity)
        embedder = HashingEmbedder()
        index = build_index((chunk,))
        query_vector = embedder.embed(chunk.text)

        results = index.search(query_vector, PatchVersion.parse(case.target), 10)

        assert chunk_ids(results) == ([chunk.chunk_id] if case.included else [])

    def test_a_stale_chunk_never_appears_even_when_it_matches_best(
        self, build_index: BuildIndex
    ) -> None:
        stale = make_chunk(
            "stale",
            text="Spark spark spark spark damage.",
            validity=Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0")),
        )
        current = make_chunk(
            "current",
            text="Spark damage.",
            validity=Validity(PatchVersion.parse("0.2.0")),
        )
        embedder = HashingEmbedder()
        index = build_index((stale, current))
        query_vector = embedder.embed("Spark spark spark spark damage.")

        results = index.search(query_vector, TARGET, 10)

        assert chunk_ids(results) == [current.chunk_id]


class TestLimit:
    def test_returns_at_most_the_limit(self, build_index: BuildIndex) -> None:
        chunks = tuple(
            make_chunk(f"section-{index}", text="Spark fires damage.") for index in range(3)
        )
        embedder = HashingEmbedder()
        index = build_index(chunks)
        query_vector = embedder.embed("Spark fires damage.")

        results = index.search(query_vector, TARGET, 2)

        assert len(results) == 2

    def test_filters_before_it_applies_the_limit(self, build_index: BuildIndex) -> None:
        """A stale chunk must not consume a slot the caller asked for and could use."""
        stale = make_chunk(
            "stale",
            text="Spark spark spark spark damage.",
            validity=Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0")),
        )
        current = make_chunk(
            "current",
            text="Spark damage.",
            validity=Validity(PatchVersion.parse("0.2.0")),
        )
        embedder = HashingEmbedder()
        index = build_index((stale, current))
        query_vector = embedder.embed("Spark spark spark spark damage.")

        results = index.search(query_vector, TARGET, 1)

        assert chunk_ids(results) == [current.chunk_id]


class TestScoring:
    def test_every_result_carries_only_its_dense_score(
        self, build_index: BuildIndex
    ) -> None:
        chunk = make_chunk()
        embedder = HashingEmbedder()
        index = build_index((chunk,))
        query_vector = embedder.embed(chunk.text)

        (result,) = index.search(query_vector, TARGET, 1)

        assert result.scores.dense is not None
        assert result.scores.dense > 0
        assert result.scores.lexical is None
        assert result.scores.fused is None
        assert result.scores.rerank is None

    def test_orders_results_best_first(self, build_index: BuildIndex) -> None:
        chunks = (
            make_chunk("alpha", text="Spark spark fires lightning."),
            make_chunk("beta", text="Spark fires damage."),
            make_chunk("gamma", text="Lightning damage only."),
        )
        embedder = HashingEmbedder()
        index = build_index(chunks)
        query_vector = embedder.embed("spark lightning")

        results = index.search(query_vector, TARGET, 10)
        scores = [dense_score(item) for item in results]

        assert scores == sorted(scores, reverse=True)

    def test_returns_each_chunk_once(self, build_index: BuildIndex) -> None:
        chunks = (
            make_chunk("alpha", text="Spark fires lightning."),
            make_chunk("beta"),
        )
        embedder = HashingEmbedder()
        index = build_index(chunks)
        query_vector = embedder.embed("Spark fires lightning damage")

        ids = chunk_ids(index.search(query_vector, TARGET, 10))

        assert len(ids) == len(set(ids))

    def test_a_query_with_no_valid_chunk_returns_nothing(self, build_index: BuildIndex) -> None:
        chunk = make_chunk(
            validity=Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0"))
        )
        embedder = HashingEmbedder()
        index = build_index((chunk,))
        query_vector = embedder.embed(chunk.text)

        assert index.search(query_vector, TARGET, 10) == ()


def dense_score(item: RetrievedChunk) -> float:
    """Return the dense score a result must carry, or fail the contract loudly."""
    score = item.scores.dense
    assert score is not None, f"{item.chunk.chunk_id!r} came back without a dense score"
    return score
