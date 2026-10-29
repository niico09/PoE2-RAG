"""The read path orchestrated over fakes: retrieval, fusion, reranking and generation."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pytest
from fakes import (
    CallRecorder,
    FakeEmbedder,
    FakeLanguageModel,
    FakeLexicalIndex,
    FakeReranker,
    FakeVectorIndex,
)

from poe2_rag.answering.application import (
    AnswerQuestion,
    AnswerResult,
    GeneratedAnswer,
    RetrievalSettings,
    StaleChunkError,
)
from poe2_rag.answering.domain import (
    Answer,
    Citation,
    InvalidCitationError,
    InvalidQueryError,
    RetrievedChunk,
    StageScores,
)
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    InMemoryPatchCatalog,
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
    chunk: Chunk, *, lexical: float | None = None, dense: float | None = None
) -> RetrievedChunk:
    """A chunk as one index returns it, with that stage's score set."""
    return RetrievedChunk(chunk, StageScores(lexical=lexical, dense=dense))


def catalog() -> InMemoryPatchCatalog:
    return InMemoryPatchCatalog([CURRENT, NEXT], current=CURRENT)


def default_settings() -> RetrievalSettings:
    return RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=3)


@dataclass
class Pipeline:
    """One wired use case with its recorder, so a test can read every call it made."""

    use_case: AnswerQuestion
    recorder: CallRecorder
    embedder: FakeEmbedder


def pipeline(
    *,
    lexical: Sequence[RetrievedChunk] = (),
    dense: Sequence[RetrievedChunk] = (),
    order: Sequence[str] | None = None,
    returned: Sequence[RetrievedChunk] | None = None,
    answer: GeneratedAnswer | None = None,
    settings: RetrievalSettings | None = None,
) -> Pipeline:
    """Wire the use case against fresh fakes; `returned` makes the reranker break its limit."""
    recorder = CallRecorder()
    embedder = FakeEmbedder(recorder)
    use_case = AnswerQuestion(
        embedder=embedder,
        lexical_index=FakeLexicalIndex(recorder, lexical),
        vector_index=FakeVectorIndex(recorder, dense),
        reranker=FakeReranker(recorder, order=order, returned=returned),
        language_model=FakeLanguageModel(recorder, answer),
        catalog=catalog(),
        settings=settings or default_settings(),
    )
    return Pipeline(use_case, recorder, embedder)


def test_defaults_to_the_catalog_current_version() -> None:
    pipe = pipeline()

    result = pipe.use_case.execute(QUESTION)

    assert result.target_version == CURRENT
    assert pipe.recorder.arguments("lexical") == [(QUESTION, CURRENT, 10)]
    assert pipe.recorder.arguments("dense") == [(pipe.embedder.vector, CURRENT, 10)]


def test_uses_the_requested_version() -> None:
    pipe = pipeline()

    result = pipe.use_case.execute(QUESTION, NEXT)

    assert result.target_version == NEXT
    assert pipe.recorder.arguments("lexical") == [(QUESTION, NEXT, 10)]
    assert pipe.recorder.arguments("dense") == [(pipe.embedder.vector, NEXT, 10)]


def test_rejects_a_version_the_catalog_does_not_know() -> None:
    pipe = pipeline()

    with pytest.raises(InvalidQueryError, match="not a known patch"):
        pipe.use_case.execute(QUESTION, PatchVersion.parse("0.2.0"))

    assert pipe.recorder.calls == []


def test_searches_both_indexes_with_the_configured_limits() -> None:
    settings = RetrievalSettings(lexical_top_n=30, dense_top_n=20, rerank_top_m=5, final_top_k=2)
    pipe = pipeline(settings=settings)

    pipe.use_case.execute(QUESTION)

    assert pipe.recorder.arguments("embed") == [(QUESTION,)]
    assert pipe.recorder.arguments("lexical") == [(QUESTION, CURRENT, 30)]
    assert pipe.recorder.arguments("dense") == [(pipe.embedder.vector, CURRENT, 20)]


def test_a_chunk_from_only_one_ranking_survives_fusion() -> None:
    lexical_only = make_chunk("spark")
    dense_only = make_chunk("ice-shot")
    pipe = pipeline(
        lexical=[retrieved(lexical_only, lexical=1.6)],
        dense=[retrieved(dense_only, dense=0.9)],
    )

    result = pipe.use_case.execute(QUESTION)

    assert [item.chunk.chunk_id for item in result.delivered] == [
        dense_only.chunk_id,
        lexical_only.chunk_id,
    ]
    candidates = pipe.recorder.arguments("rerank")[0][1]
    assert [item.scores for item in candidates] == [
        StageScores(dense=0.9, fused=1 / 61),
        StageScores(lexical=1.6, fused=1 / 61),
    ]


def test_merges_the_scores_of_a_chunk_that_both_rankings_return() -> None:
    spark = make_chunk("spark")
    pipe = pipeline(
        lexical=[retrieved(spark, lexical=1.5)],
        dense=[retrieved(spark, dense=0.8)],
    )

    pipe.use_case.execute(QUESTION)

    candidates = pipe.recorder.arguments("rerank")[0][1]
    assert len(candidates) == 1
    assert candidates[0].chunk.chunk_id == spark.chunk_id
    assert candidates[0].scores == StageScores(lexical=1.5, dense=0.8, fused=2 / 61)


def test_reranks_only_the_top_rerank_top_m_fused_candidates() -> None:
    chunks = [make_chunk(slug) for slug in ("alpha", "beta", "gamma")]
    settings = RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=2, final_top_k=1)
    pipe = pipeline(
        lexical=[retrieved(chunk, lexical=3 - rank) for rank, chunk in enumerate(chunks)],
        settings=settings,
    )

    result = pipe.use_case.execute(QUESTION)

    candidates = pipe.recorder.arguments("rerank")[0][1]
    assert [item.chunk.chunk_id for item in candidates] == [chunks[0].chunk_id, chunks[1].chunk_id]
    assert [item.chunk.chunk_id for item in result.delivered] == [chunks[0].chunk_id]


def test_keeps_the_reranked_order_up_to_the_final_top_k() -> None:
    chunks = [make_chunk(slug) for slug in ("alpha", "beta", "gamma")]
    settings = RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=2)
    pipe = pipeline(
        lexical=[retrieved(chunk, lexical=3 - rank) for rank, chunk in enumerate(chunks)],
        order=[chunks[2].chunk_id, chunks[0].chunk_id, chunks[1].chunk_id],
        settings=settings,
    )

    result = pipe.use_case.execute(QUESTION)

    assert pipe.recorder.arguments("rerank")[0][2] == 2
    assert [item.chunk.chunk_id for item in result.delivered] == [
        chunks[2].chunk_id,
        chunks[0].chunk_id,
    ]
    assert all(item.scores.rerank is not None for item in result.delivered)


def test_keeps_the_final_top_k_even_when_the_reranker_returns_more() -> None:
    chunks = [make_chunk(slug) for slug in ("alpha", "beta", "gamma")]
    settings = RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=2)
    pipe = pipeline(
        lexical=[retrieved(chunk, lexical=3 - rank) for rank, chunk in enumerate(chunks)],
        returned=[retrieved(chunk) for chunk in chunks],
        settings=settings,
    )

    result = pipe.use_case.execute(QUESTION)

    assert [item.chunk.chunk_id for item in result.delivered] == [
        chunks[0].chunk_id,
        chunks[1].chunk_id,
    ]


def test_returns_the_validated_answer_with_the_delivered_context() -> None:
    spark = make_chunk("spark")
    answer = GeneratedAnswer("Spark costs 12 mana.", (Citation(spark.chunk_id, "12 mana"),))
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], answer=answer)

    result = pipe.use_case.execute(QUESTION)

    assert isinstance(result, AnswerResult)
    assert result.answer == Answer(
        "Spark costs 12 mana.", (Citation(spark.chunk_id, "12 mana"),), True
    )
    assert result.answer.supported is True
    assert [item.chunk.chunk_id for item in result.delivered] == [spark.chunk_id]
    assert pipe.recorder.arguments("generate") == [(QUESTION, (spark.chunk_id,))]


def test_a_citation_to_an_undelivered_chunk_raises_invalid_citation_error() -> None:
    spark = make_chunk("spark")
    ghost = make_chunk("ghost")
    answer = GeneratedAnswer("Ghost is the best gem.", (Citation(ghost.chunk_id, "Ghost text."),))
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], answer=answer)

    with pytest.raises(InvalidCitationError, match="which was not delivered"):
        pipe.use_case.execute(QUESTION)


def test_zero_citations_yield_an_unsupported_answer() -> None:
    spark = make_chunk("spark")
    answer = GeneratedAnswer("I could not find that.", ())
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], answer=answer)

    result = pipe.use_case.execute(QUESTION)

    assert result.answer.text == "I could not find that."
    assert result.answer.citations == ()
    assert result.answer.supported is False
    assert [item.chunk.chunk_id for item in result.delivered] == [spark.chunk_id]


def test_calls_the_ports_in_pipeline_order() -> None:
    spark = make_chunk("spark")
    answer = GeneratedAnswer("Spark costs 12 mana.", (Citation(spark.chunk_id, "12 mana"),))
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], answer=answer)

    pipe.use_case.execute(QUESTION)

    assert pipe.recorder.kinds() == ["embed", "lexical", "dense", "rerank", "generate"]


def test_a_stale_chunk_from_the_lexical_index_raises_stale_chunk_error() -> None:
    stale = make_chunk("spark", valid_from="0.1.1", valid_until="0.1.2")
    pipe = pipeline(lexical=[retrieved(stale, lexical=1.5)])

    with pytest.raises(StaleChunkError, match="lexical index"):
        pipe.use_case.execute(QUESTION, NEXT)

    assert pipe.recorder.kinds() == ["embed", "lexical"]


def test_a_stale_chunk_from_the_vector_index_raises_stale_chunk_error() -> None:
    stale = make_chunk("spark", valid_from="0.1.1", valid_until="0.1.2")
    pipe = pipeline(dense=[retrieved(stale, dense=0.9)])

    with pytest.raises(StaleChunkError, match="vector index"):
        pipe.use_case.execute(QUESTION, NEXT)

    assert pipe.recorder.kinds() == ["embed", "lexical", "dense"]


def test_a_stale_chunk_from_the_reranker_raises_stale_chunk_error() -> None:
    spark = make_chunk("spark")
    stale = make_chunk("ghost", valid_from="0.1.1", valid_until="0.1.2")
    pipe = pipeline(
        lexical=[retrieved(spark, lexical=1.5)],
        returned=[retrieved(stale, lexical=1.0)],
    )

    with pytest.raises(StaleChunkError, match="reranker"):
        pipe.use_case.execute(QUESTION, NEXT)

    assert "generate" not in pipe.recorder.kinds()


def test_empty_retrieval_skips_every_remaining_stage_and_is_unsupported() -> None:
    pipe = pipeline()

    result = pipe.use_case.execute(QUESTION)

    assert pipe.recorder.kinds() == ["embed", "lexical", "dense"]
    assert result.answer.text == "No information available for this question in the selected patch."
    assert result.answer.supported is False
    assert result.answer.citations == ()
    assert result.delivered == ()
    assert result.target_version == CURRENT


def test_an_empty_rerank_skips_the_model_and_is_unsupported() -> None:
    spark = make_chunk("spark")
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], returned=[])

    result = pipe.use_case.execute(QUESTION)

    assert pipe.recorder.kinds() == ["embed", "lexical", "dense", "rerank"]
    assert result.answer.supported is False
    assert result.delivered == ()
