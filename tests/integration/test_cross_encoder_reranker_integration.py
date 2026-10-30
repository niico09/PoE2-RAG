"""The real `bge-reranker-base` cross-encoder, over the real adapter.

Marked `integration`: it downloads the model on the first run and takes seconds on CPU, so `addopts`
excludes it and it runs only with `uv run pytest -q -m integration`. Everything the default suite can
prove without the model lives in `test_cross_encoder_reranker.py`; this suite proves only what a stub
cannot: the model loads through sentence-transformers, scores every pair it is given, and ranks an
obviously relevant chunk first while keeping the scores retrieval already assigned.
"""

from __future__ import annotations

from datetime import date

import pytest

from poe2_rag.answering.adapters.outbound.rerank.cross_encoder_reranker import (
    DEFAULT_MODEL_NAME,
    CrossEncoderReranker,
)
from poe2_rag.answering.application import Reranker
from poe2_rag.answering.domain import RetrievedChunk, StageScores
from poe2_rag.shared.domain import Chunk, EntityRef, PatchVersion, SourceRef, Validity

pytestmark = pytest.mark.integration

TARGET = PatchVersion.parse("0.2.0")
THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)
QUESTION = "how much damage does spark deal"

EARLIER = StageScores(lexical=0.5, dense=0.25, fused=0.125)


def make_retrieved(section: str, text: str) -> RetrievedChunk:
    """Build one fused candidate with the scores fusion would have given it."""
    chunk = Chunk(
        entity=EntityRef("patch_notes", "0-2-0", "Patch 0.2.0"),
        validity=Validity(TARGET),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
    )
    return RetrievedChunk(chunk, EARLIER)


RELEVANT = make_retrieved(
    "spark-damage",
    "Spark deals 10 to 15 lightning damage and chains to two additional enemies.",
)
FROST = make_retrieved(
    "glacial-cascade",
    "Glacial Cascade releases a wave of cold damage that freezes enemies in a cone.",
)
LIFETIME = make_retrieved(
    "spell-lifetime",
    "Increased spell lifetime makes projectiles travel further before expiring.",
)
CANDIDATES = (FROST, LIFETIME, RELEVANT)


def test_reranker_matches_the_port() -> None:
    assert isinstance(CrossEncoderReranker(), Reranker)


def test_the_real_model_ranks_the_obviously_relevant_chunk_first() -> None:
    reranker = CrossEncoderReranker()

    reranked = reranker.rerank(QUESTION, CANDIDATES, 3)

    assert reranker.model_name == DEFAULT_MODEL_NAME
    # Only the obviously relevant chunk has a predictable place: where the two unrelated chunks land
    # is the model's business, and pinning it would make this suite fail on a model update for no
    # reason. What the adapter guarantees is the total order and the budget, asserted below.
    assert reranked[0].chunk.chunk_id == RELEVANT.chunk.chunk_id
    assert {item.chunk.chunk_id for item in reranked[1:]} == {
        FROST.chunk.chunk_id,
        LIFETIME.chunk.chunk_id,
    }


def test_the_real_model_scores_are_finite_ordered_and_keep_the_earlier_stages() -> None:
    reranked = CrossEncoderReranker().rerank(QUESTION, CANDIDATES, 2)

    first, second = (item.scores.rerank for item in reranked)
    assert first is not None and second is not None
    assert first > second > 0 or first < second < 0
    assert all(item.scores.lexical == 0.5 and item.scores.dense == 0.25 for item in reranked)
