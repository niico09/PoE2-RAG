"""The real `bge-base-en-v1.5` model over the real Chroma adapter, end to end.

Marked `integration`: it downloads several hundred megabytes on the first run and takes seconds on CPU,
so `addopts` excludes it and it runs only with `uv run pytest -q -m integration`. Everything the default
suite can prove without the model lives in `test_bge_embedder.py` and `test_chroma_index.py`; this suite
proves only what a stub cannot: the model produces 768-dimensional unit vectors, it counts tokens against
its own 512-token window, and its vectors rank an obviously relevant chunk above an unrelated one.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from poe2_rag.answering.adapters.outbound.dense.bge_embedder import (
    DEFAULT_DIMENSION,
    BgeEmbedder,
)
from poe2_rag.answering.adapters.outbound.dense.chroma_index import ChromaVectorIndex
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    PatchVersion,
    SourceRef,
    Validity,
)

pytestmark = pytest.mark.integration

TARGET = PatchVersion.parse("0.2.0")
THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)


def make_chunk(section: str, text: str) -> Chunk:
    """Build one patch-notes chunk with the real model's own vocabulary out of the loop."""
    return Chunk(
        entity=EntityRef("patch_notes", "0-2-0", "Patch 0.2.0"),
        validity=Validity(TARGET),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
    )


CORPUS = (
    make_chunk(
        "chaining",
        "Spark fires a bolt of lightning that chains between nearby enemies, "
        "jumping from one target to the next.",
    ),
    make_chunk(
        "cold-cone",
        "Glacial Cascade releases a wave of cold damage in a cone, "
        "freezing everything it touches.",
    ),
    make_chunk(
        "minion-count",
        "The Witch may raise a limited number of undead minions, "
        "and the limit is reduced by one while a temporary minion is active.",
    ),
)


@pytest.fixture(scope="module")
def embedder() -> BgeEmbedder:
    """The real model, loaded once for the whole module."""
    return BgeEmbedder()


class TestModel:
    def test_embeds_a_query_into_a_normalized_768_dimension_vector(
        self, embedder: BgeEmbedder
    ) -> None:
        vector = embedder.embed("which lightning skill jumps between enemies")

        assert len(vector) == DEFAULT_DIMENSION == 768
        assert pytest.approx(1.0, abs=1e-4) == sum(value * value for value in vector) ** 0.5

    def test_embeds_documents_into_normalized_vectors(self, embedder: BgeEmbedder) -> None:
        vectors = embedder.embed_documents([chunk.text for chunk in CORPUS])

        assert len(vectors) == len(CORPUS)
        for vector in vectors:
            assert len(vector) == DEFAULT_DIMENSION
            assert pytest.approx(1.0, abs=1e-4) == sum(value * value for value in vector) ** 0.5

    def test_accepts_a_chunk_inside_the_sequence_limit(self, embedder: BgeEmbedder) -> None:
        assert embedder.overflowing_indices(["Spark chains between enemies."]) == ()

    def test_reports_a_text_above_the_sequence_limit(self, embedder: BgeEmbedder) -> None:
        """512 tokens is the model's own window; a longer text is cut silently unless reported."""
        long_text = " ".join(["spark"] * 600)

        assert embedder.overflowing_indices(["Spark chains between enemies.", long_text]) == (1,)


class TestRetrieval:
    def test_ranks_the_relevant_chunk_above_an_unrelated_one(
        self, embedder: BgeEmbedder, tmp_path: Path
    ) -> None:
        index = ChromaVectorIndex(tmp_path / "data" / "index" / "chroma", embedder=embedder)
        index.rebuild(CORPUS, embedder.embed_documents([chunk.text for chunk in CORPUS]))

        results = index.search(
            embedder.embed("which skill makes lightning jump from foe to foe"), TARGET, 3
        )

        assert results[0].chunk == CORPUS[0]
        assert results[0].scores.dense is not None

    def test_answers_a_paraphrase_that_shares_no_keyword_with_the_chunk(
        self, embedder: BgeEmbedder, tmp_path: Path
    ) -> None:
        """Dense retrieval exists for the queries BM25 cannot answer; this is that query."""
        index = ChromaVectorIndex(tmp_path / "data" / "index" / "chroma", embedder=embedder)
        index.rebuild(CORPUS, embedder.embed_documents([chunk.text for chunk in CORPUS]))

        results = index.search(embedder.embed("how many companions can I have at once?"), TARGET, 3)

        assert results[0].chunk == CORPUS[2]
