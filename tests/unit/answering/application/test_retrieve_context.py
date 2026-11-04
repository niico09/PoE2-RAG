"""Retrieval as its own use case: the pipeline `AnswerQuestion` delegates to (§5.1 steps 1-5).

The point of these cases is the extraction, not a second description of the whole read path: they pin
what `RetrieveContext` returns and the contract checks it performs before a single chunk reaches the
context window. `AnswerQuestion` keeps its own suite for generation and citation validation.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, dataclass
from datetime import date

import pytest
from answering_fakes import (
    CallRecorder,
    FakeEmbedder,
    FakeLexicalIndex,
    FakeReranker,
    FakeVectorIndex,
)

from poe2_rag.answering.application import (
    ChunkMismatchError,
    DuplicateChunkError,
    MissingScoreError,
    RetrievalResult,
    RetrievalSettings,
    RetrieveContext,
    StaleChunkError,
    UnretrievedChunkError,
)
from poe2_rag.answering.domain import (
    InvalidQueryError,
    RetrievedChunk,
    StageScores,
)
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    InMemoryPatchCatalog,
    PatchRelease,
    PatchVersion,
    SourceRef,
    Validity,
)

CURRENT = PatchVersion.parse("0.1.1")
NEXT = PatchVersion.parse("0.1.2")
QUESTION = "How much mana does Spark cost?"


def make_chunk(
    slug: str = "spark",
    *,
    valid_from: str = "0.1.1",
    valid_until: str | None = None,
) -> Chunk:
    """Build a chunk valid from `valid_from`, or up to `valid_until` when it is given."""
    return Chunk(
        entity=EntityRef("active_gem", slug, slug.title()),
        validity=Validity(
            PatchVersion.parse(valid_from),
            None if valid_until is None else PatchVersion.parse(valid_until),
        ),
        section="description",
        text=f"{slug.title()} text.",
        source=SourceRef("poe2db", f"https://poe2db.tw/us/{slug.title()}", date(2026, 10, 29)),
    )


def retrieved(
    chunk: Chunk,
    *,
    lexical: float | None = None,
    dense: float | None = None,
    rerank: float | None = None,
) -> RetrievedChunk:
    """A chunk as one stage returns it, with that stage's score set."""
    return RetrievedChunk(chunk, StageScores(lexical=lexical, dense=dense, rerank=rerank))


def catalog() -> InMemoryPatchCatalog:
    """A catalog whose current patch is `CURRENT`, with `NEXT` known but not current."""
    return InMemoryPatchCatalog(
        [PatchRelease(CURRENT, date(2025, 1, 17)), PatchRelease(NEXT, date(2025, 2, 7))],
        current=CURRENT,
    )


def default_settings() -> RetrievalSettings:
    """Four budgets small enough that a test can count what each stage received."""
    return RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=3)


@dataclass
class Retrieval:
    """One wired use case with its recorder, so a test can read every call it made."""

    use_case: RetrieveContext
    recorder: CallRecorder
    embedder: FakeEmbedder

    def run(self, version: PatchVersion | None = None) -> RetrievalResult:
        """Retrieve the shared question for `version`, recording every port call."""
        # pi-lens-ignore: python-sql-injection -- an injected use case port; this project has no SQL.
        return self.use_case.execute(QUESTION, version)


def retrieval(
    *,
    lexical: Sequence[RetrievedChunk] = (),
    dense: Sequence[RetrievedChunk] = (),
    order: Sequence[str] | None = None,
    returned: Sequence[RetrievedChunk] | None = None,
    settings: RetrievalSettings | None = None,
) -> Retrieval:
    """Wire the use case against fresh fakes; `returned` makes the reranker break its limit."""
    recorder = CallRecorder()
    embedder = FakeEmbedder(recorder)
    use_case = RetrieveContext(
        embedder=embedder,
        lexical_index=FakeLexicalIndex(recorder, lexical),
        vector_index=FakeVectorIndex(recorder, dense),
        reranker=FakeReranker(recorder, order=order, returned=returned),
        catalog=catalog(),
        settings=settings or default_settings(),
    )
    return Retrieval(use_case, recorder, embedder)


def test_returns_the_requested_version_and_the_delivered_chunks() -> None:
    """The result is what generation needs: the patch it was retrieved for and the context."""
    spark = make_chunk("spark")
    pipe = retrieval(lexical=[retrieved(spark, lexical=1.5)], dense=[retrieved(spark, dense=0.9)])

    result = pipe.run(NEXT)

    assert isinstance(result, RetrievalResult)
    assert result.target_version == NEXT
    assert [item.chunk.chunk_id for item in result.delivered] == [spark.chunk_id]


def test_defaults_to_the_catalog_current_version() -> None:
    """Without a version the catalog decides, exactly as it did inside `AnswerQuestion`."""
    pipe = retrieval()

    assert pipe.run().target_version == CURRENT


def test_rejects_a_version_the_catalog_does_not_know_before_any_port_call() -> None:
    """An unknown patch would retrieve nothing while looking valid, so it fails first."""
    pipe = retrieval()

    with pytest.raises(InvalidQueryError, match="not a known patch"):
        pipe.run(PatchVersion.parse("0.2.0"))

    assert pipe.recorder.calls == []


def test_calls_the_ports_in_pipeline_order() -> None:
    """Embed, lexical, dense, rerank: the order `AnswerQuestion` used before the extraction."""
    spark = make_chunk("spark")
    pipe = retrieval(lexical=[retrieved(spark, lexical=1.5)], dense=[retrieved(spark, dense=0.9)])

    pipe.run()

    assert pipe.recorder.kinds() == ["embed", "lexical", "dense", "rerank"]
    assert pipe.recorder.arguments("embed") == [(QUESTION,)]
    assert pipe.recorder.arguments("lexical") == [(QUESTION, CURRENT, 10)]
    assert pipe.recorder.arguments("dense") == [(pipe.embedder.vector, CURRENT, 10)]


def test_no_candidates_delivers_nothing_without_calling_the_reranker() -> None:
    """An empty retrieval stops before every remaining stage, and delivers an empty tuple."""
    pipe = retrieval()

    result = pipe.run()

    assert result.delivered == ()
    assert pipe.recorder.kinds() == ["embed", "lexical", "dense"]


def test_an_empty_rerank_delivers_nothing() -> None:
    """A reranker that returns nothing must not be able to invent context."""
    spark = make_chunk("spark")
    pipe = retrieval(lexical=[retrieved(spark, lexical=1.5)], returned=())

    assert pipe.run().delivered == ()


def test_keeps_the_reranked_order_up_to_the_final_top_k() -> None:
    """The delivered context is the reranker's order, cut at `final_top_k`."""
    first, second, third = make_chunk("spark"), make_chunk("ice-shot"), make_chunk("fireball")
    settings = RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=2)
    pipe = retrieval(
        lexical=[retrieved(first, lexical=1.0), retrieved(second, lexical=0.5)],
        dense=[retrieved(third, dense=0.9)],
        order=[third.chunk_id, second.chunk_id, first.chunk_id],
        settings=settings,
    )

    result = pipe.run()

    assert [item.chunk.chunk_id for item in result.delivered] == [third.chunk_id, second.chunk_id]
    assert [item.scores.rerank for item in result.delivered] == [1.0, 0.5]


@pytest.mark.parametrize("stage", ["lexical", "dense", "reranker"])
def test_a_stale_chunk_stops_the_run(stage: str) -> None:
    """D1: a chunk that is not valid at the target patch is an error, never a silent drop."""
    valid = retrieved(make_chunk("spark"), lexical=1.0, dense=0.9)
    stale = retrieved(make_chunk("fireball", valid_from="0.1.2"), lexical=1.0, dense=0.9, rerank=1.0)
    pipe = retrieval(
        lexical=[stale] if stage == "lexical" else [valid],
        dense=[stale] if stage == "dense" else [valid],
        # The reranker needs a candidate to reorder, so it receives the valid chunk and returns the
        # stale one: the case is the reranker breaking D1, not an empty pipeline.
        returned=[stale] if stage == "reranker" else None,
    )

    with pytest.raises(StaleChunkError, match="not valid at"):
        pipe.run()


def test_rejects_a_duplicate_chunk_from_an_index() -> None:
    """One ranking cannot name the same chunk twice: the fusion would count it twice."""
    spark = make_chunk("spark")
    pipe = retrieval(lexical=[retrieved(spark, lexical=1.0), retrieved(spark, lexical=0.5)])

    with pytest.raises(DuplicateChunkError, match="more than once"):
        pipe.run()


def test_rejects_a_chunk_an_index_returned_without_its_score() -> None:
    """A chunk without the score of the stage that produced it cannot be attributed (§8, D4)."""
    pipe = retrieval(lexical=[retrieved(make_chunk("spark"))])

    with pytest.raises(MissingScoreError, match="lexical"):
        pipe.run()


def test_rejects_a_reranked_chunk_that_no_index_retrieved() -> None:
    """A valid chunk nobody retrieved must not reach the context window (D3)."""
    retrieved_chunk = retrieved(make_chunk("spark"), lexical=1.0)
    foreign = retrieved(make_chunk("ice-shot"), rerank=0.9)
    pipe = retrieval(lexical=[retrieved_chunk], returned=[foreign])

    with pytest.raises(UnretrievedChunkError, match="was not retrieved"):
        pipe.run()


def test_the_result_is_an_immutable_value_object() -> None:
    """The delivered context is frozen, so no later stage can rewrite what was retrieved."""
    result = retrieval().run()

    with pytest.raises(FrozenInstanceError):
        result.delivered = ()  # type: ignore[misc]


def test_the_use_case_has_no_language_model_dependency() -> None:
    """S1: `RunEvaluation` depends on retrieval only, and retrieval must not need a model."""
    assert "language_model" not in inspect.signature(RetrieveContext).parameters


def test_a_chunk_from_only_one_ranking_survives_fusion_with_its_own_score() -> None:
    """RRF merges the two rankings and each chunk keeps the stage score that produced it (D4)."""
    lexical_only = make_chunk("spark")
    dense_only = make_chunk("ice-shot")
    pipe = retrieval(
        lexical=[retrieved(lexical_only, lexical=1.6)],
        dense=[retrieved(dense_only, dense=0.9)],
    )

    result = pipe.run()

    assert [item.chunk.chunk_id for item in result.delivered] == [
        dense_only.chunk_id,
        lexical_only.chunk_id,
    ]
    candidates = pipe.recorder.arguments("rerank")[0][1]
    assert [item.scores for item in candidates] == [
        StageScores(dense=0.9, fused=1 / 61),
        StageScores(lexical=1.6, fused=1 / 61),
    ]


def test_a_chunk_returned_by_both_rankings_keeps_both_scores_and_their_fusion() -> None:
    spark = make_chunk("spark")
    pipe = retrieval(lexical=[retrieved(spark, lexical=1.5)], dense=[retrieved(spark, dense=0.8)])

    pipe.run()

    candidates = pipe.recorder.arguments("rerank")[0][1]
    assert [item.scores for item in candidates] == [
        StageScores(lexical=1.5, dense=0.8, fused=2 / 61),
    ]


def test_the_fusion_budget_cuts_the_candidates_before_the_reranker() -> None:
    """`rerank_top_m` bounds what the cross-encoder sees, so the expensive stage stays bounded."""
    chunks = [make_chunk(slug) for slug in ("alpha", "beta", "gamma", "delta")]
    settings = RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=2, final_top_k=2)
    pipe = retrieval(
        lexical=[retrieved(chunk, lexical=4 - rank) for rank, chunk in enumerate(chunks)],
        settings=settings,
    )

    pipe.run()

    candidates = pipe.recorder.arguments("rerank")[0][1]
    assert [item.chunk.chunk_id for item in candidates] == [
        chunks[0].chunk_id,
        chunks[1].chunk_id,
    ]


def test_a_foreign_chunk_beyond_the_final_top_k_still_fails_loud() -> None:
    """The guard runs before the slice, so a chunk nobody retrieved cannot hide behind the budget."""
    kept = [make_chunk(slug) for slug in ("alpha", "beta")]
    foreign = retrieved(make_chunk("gamma"), rerank=1.0)
    settings = RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=2)
    pipe = retrieval(
        lexical=[retrieved(chunk, lexical=1.0) for chunk in kept],
        returned=[retrieved(chunk, rerank=1.0) for chunk in kept] + [foreign],
        settings=settings,
    )

    with pytest.raises(UnretrievedChunkError, match="was not retrieved"):
        pipe.run()


def test_a_stale_chunk_beyond_the_final_top_k_still_fails_loud() -> None:
    """D1: the budget cannot hide a stale chunk, because validity is checked before the slice."""
    kept = [make_chunk(slug) for slug in ("alpha", "beta")]
    stale = retrieved(make_chunk("gamma", valid_from="0.1.2"), rerank=0.5)
    settings = RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=2)
    pipe = retrieval(
        lexical=[retrieved(chunk, lexical=1.0) for chunk in kept],
        returned=[retrieved(chunk, rerank=1.0) for chunk in kept] + [stale],
        settings=settings,
    )

    with pytest.raises(StaleChunkError, match="not valid at"):
        pipe.run()


def test_rejects_a_duplicate_chunk_from_the_reranker() -> None:
    spark = make_chunk("spark")
    pipe = retrieval(
        lexical=[retrieved(spark, lexical=1.0)],
        returned=[retrieved(spark, rerank=1.0), retrieved(spark, rerank=0.5)],
    )

    with pytest.raises(DuplicateChunkError, match="more than once"):
        pipe.run()


def test_rejects_a_chunk_the_vector_index_returned_without_its_score() -> None:
    pipe = retrieval(dense=[retrieved(make_chunk("spark"))])

    with pytest.raises(MissingScoreError, match="dense"):
        pipe.run()


def test_rejects_a_reranked_chunk_without_its_score() -> None:
    spark = make_chunk("spark")
    scored_without_rerank = RetrievedChunk(spark, StageScores(lexical=1.0, fused=1 / 61))
    pipe = retrieval(lexical=[retrieved(spark, lexical=1.0)], returned=[scored_without_rerank])

    with pytest.raises(MissingScoreError, match="rerank"):
        pipe.run()


def test_rejects_two_index_views_that_disagree_about_the_same_id() -> None:
    """`chunk_id` does not cover `valid_until`: two views can share it and still be different chunks."""
    lexical_view = make_chunk("spark")
    dense_view = make_chunk("spark", valid_until="0.1.2")
    assert lexical_view.chunk_id == dense_view.chunk_id
    pipe = retrieval(
        lexical=[retrieved(lexical_view, lexical=1.0)],
        dense=[retrieved(dense_view, dense=0.9)],
    )

    with pytest.raises(ChunkMismatchError, match="disagree"):
        pipe.run()


def test_uses_the_configured_rrf_k_in_the_fusion() -> None:
    spark = make_chunk("spark")
    settings = RetrievalSettings(
        lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=3, rrf_k=10
    )
    pipe = retrieval(lexical=[retrieved(spark, lexical=1.0)], settings=settings)

    pipe.run()

    candidates = pipe.recorder.arguments("rerank")[0][1]
    assert candidates[0].scores.fused == 1 / 11
