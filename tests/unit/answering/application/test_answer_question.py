"""`AnswerQuestion` over its two collaborators: retrieval (delegated) and the model.

The retrieval contract itself — fusion, validity, ordering, budgets, per-stage scores — lives in
`test_retrieve_context.py`, next to the use case that owns it. This module keeps what only
`AnswerQuestion` does: hand the delivered context to the model, and validate the citations that come
back. A rule changed in retrieval must break one file, not two.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pytest
from answering_fakes import (
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
    RetrieveContext,
)
from poe2_rag.answering.domain import (
    Answer,
    Citation,
    InvalidCitationError,
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
    text: str | None = None,
) -> Chunk:
    """Build a chunk valid from `valid_from`, or up to `valid_until` when it is given."""
    return Chunk(
        entity=EntityRef("active_gem", slug, slug.title()),
        validity=Validity(
            PatchVersion.parse(valid_from),
            None if valid_until is None else PatchVersion.parse(valid_until),
        ),
        section="description",
        text=text or f"{slug.title()} text.",
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
    return InMemoryPatchCatalog(
        [
            PatchRelease(CURRENT, date(2025, 1, 17)),
            PatchRelease(NEXT, date(2025, 2, 7)),
        ],
        current=CURRENT,
    )


def default_settings() -> RetrievalSettings:
    return RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=3)


@dataclass
class Pipeline:
    """One wired use case, the retrieval it delegates to and the journal of every port call."""

    use_case: AnswerQuestion
    recorder: CallRecorder
    retrieval: RetrieveContext

    def run(self, version: PatchVersion | None = None) -> AnswerResult:
        """Answer the shared question for `version`, recording every port call."""
        # pi-lens-ignore: python-sql-injection -- an injected use case port; this project has no SQL.
        return self.use_case.execute(QUESTION, version)


def pipeline(
    *,
    lexical: Sequence[RetrievedChunk] = (),
    dense: Sequence[RetrievedChunk] = (),
    returned: Sequence[RetrievedChunk] | None = None,
    answer: GeneratedAnswer | None = None,
    settings: RetrievalSettings | None = None,
) -> Pipeline:
    """Wire the use case against fresh fakes; `returned` makes the reranker break its limit."""
    recorder = CallRecorder()
    retrieval = RetrieveContext(
        embedder=FakeEmbedder(recorder),
        lexical_index=FakeLexicalIndex(recorder, lexical),
        vector_index=FakeVectorIndex(recorder, dense),
        reranker=FakeReranker(recorder, returned=returned),
        catalog=catalog(),
        settings=settings or default_settings(),
    )
    use_case = AnswerQuestion(
        retrieve=retrieval,
        language_model=FakeLanguageModel(recorder, answer),
    )
    return Pipeline(use_case, recorder, retrieval)


def test_runs_the_injected_retrieval_then_the_model() -> None:
    """Retrieval is delegated, not reimplemented: the injected use case is the one that ran."""
    spark = make_chunk("spark")
    answer = GeneratedAnswer("Spark costs 12 mana.", (Citation(spark.chunk_id, "12 mana"),))
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], answer=answer)

    pipe.run()

    assert pipe.use_case._retrieve is pipe.retrieval
    assert pipe.recorder.kinds() == ["embed", "lexical", "dense", "rerank", "generate"]


def test_returns_the_validated_answer_with_the_delivered_context() -> None:
    spark = make_chunk("spark")
    answer = GeneratedAnswer("Spark costs 12 mana.", (Citation(spark.chunk_id, "12 mana"),))
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], answer=answer)

    result = pipe.run()

    assert isinstance(result, AnswerResult)
    assert result.answer == Answer(
        "Spark costs 12 mana.", (Citation(spark.chunk_id, "12 mana"),), True
    )
    assert result.answer.supported is True
    assert [item.chunk.chunk_id for item in result.delivered] == [spark.chunk_id]
    assert pipe.recorder.arguments("generate") == [(QUESTION, (spark.chunk_id,))]


def test_the_result_carries_the_patch_retrieval_resolved() -> None:
    """The answer is reported for the patch retrieval actually used, not for the argument."""
    pipe = pipeline()

    assert pipe.run(NEXT).target_version == NEXT
    assert pipe.run().target_version == CURRENT


def test_a_citation_to_an_undelivered_chunk_raises_invalid_citation_error() -> None:
    spark = make_chunk("spark")
    ghost = make_chunk("ghost")
    answer = GeneratedAnswer("Ghost is the best gem.", (Citation(ghost.chunk_id, "Ghost text."),))
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], answer=answer)

    with pytest.raises(InvalidCitationError, match="which was not delivered"):
        pipe.run()


def test_zero_citations_yield_an_unsupported_answer() -> None:
    spark = make_chunk("spark")
    answer = GeneratedAnswer("I could not find that.", ())
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], answer=answer)

    result = pipe.run()

    assert result.answer.text == "I could not find that."
    assert result.answer.citations == ()
    assert result.answer.supported is False
    assert [item.chunk.chunk_id for item in result.delivered] == [spark.chunk_id]


def test_empty_retrieval_skips_generation_and_returns_the_fixed_text() -> None:
    """Nothing delivered means nothing to cite, so the model is never called (§5.1 step 6)."""
    pipe = pipeline()

    result = pipe.run()

    assert pipe.recorder.kinds() == ["embed", "lexical", "dense"]
    assert result.answer.text == "No information available for this question in the selected patch."
    assert result.answer.supported is False
    assert result.answer.citations == ()
    assert result.delivered == ()
    assert result.target_version == CURRENT


def test_an_empty_rerank_skips_generation_and_returns_the_fixed_text() -> None:
    spark = make_chunk("spark")
    pipe = pipeline(lexical=[retrieved(spark, lexical=1.5)], returned=[])

    result = pipe.run()

    assert pipe.recorder.kinds() == ["embed", "lexical", "dense", "rerank"]
    assert result.answer.supported is False
    assert result.delivered == ()


def test_the_model_and_the_answer_check_only_see_the_final_top_k() -> None:
    """A citation to a chunk the budget cut must invalidate the answer, not be backed by it."""
    chunks = [make_chunk(slug) for slug in ("alpha", "beta", "gamma")]
    settings = RetrievalSettings(lexical_top_n=10, dense_top_n=10, rerank_top_m=5, final_top_k=2)
    answer = GeneratedAnswer(
        "Gamma is the answer.", (Citation(chunks[2].chunk_id, "Gamma text."),)
    )
    pipe = pipeline(
        lexical=[retrieved(chunk, lexical=3 - rank) for rank, chunk in enumerate(chunks)],
        returned=[retrieved(chunk, rerank=1.0) for chunk in chunks],
        answer=answer,
        settings=settings,
    )

    with pytest.raises(InvalidCitationError, match="which was not delivered"):
        pipe.run()

    assert pipe.recorder.arguments("generate") == [
        (QUESTION, (chunks[0].chunk_id, chunks[1].chunk_id))
    ]
