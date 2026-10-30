"""The cross-encoder reranker, without the model: ordering, tie-break, limit and preserved scores.

The real `BAAI/bge-reranker-base` is exercised by `tests/integration/test_reranker_integration.py`.
Here the scorer is injected, so the default suite stays offline and fast while still pinning every rule
the adapter owns: the scoring pairs it builds, the total order it returns, the budget it respects, and
the stage scores it must not touch.

A stub is enough because the adapter owns no model behaviour: the cross-encoder only has to return
numbers, and the ranking is the adapter's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from poe2_rag.answering.adapters.outbound.rerank import cross_encoder_reranker
from poe2_rag.answering.adapters.outbound.rerank.cross_encoder_reranker import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_DEVICE,
    DEFAULT_MODEL_NAME,
    BlankQueryError,
    CrossEncoderReranker,
    InvalidLimitError,
    RerankerConfigurationError,
    RerankerError,
    RerankScoringError,
)
from poe2_rag.answering.application import Reranker
from poe2_rag.answering.domain import RetrievedChunk, StageScores
from poe2_rag.shared.domain import Chunk, EntityRef, PatchVersion, SourceRef, Validity

THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)
TARGET = PatchVersion.parse("0.2.0")
QUERY = "how much damage does spark deal"

# Every chunk carries the score the previous stages gave it, so a rerank that loses them is visible.
EARLIER_SCORES = StageScores(lexical=0.5, dense=0.25, fused=0.125)


@dataclass(frozen=True)
class PredictCall:
    """One `predict` the adapter asked the scorer for, with the arguments it used."""

    pairs: tuple[tuple[str, str], ...]
    batch_size: int
    show_progress_bar: bool


class StubScorer:
    """Stands in for `CrossEncoder`: records every call and returns the scores it was given."""

    def __init__(self, scores: Any) -> None:
        self.calls: list[PredictCall] = []
        self._scores = scores

    def predict(
        self, inputs: list[tuple[str, str]], *, batch_size: int, show_progress_bar: bool
    ) -> Any:
        self.calls.append(PredictCall(tuple(inputs), batch_size, show_progress_bar))
        # Returned as stored: the real model hands back an `ndarray`, not a list, so the stub must
        # not quietly normalize the one shape the adapter has to accept.
        return self._scores


def make_retrieved(section: str, text: str) -> RetrievedChunk:
    """Build one delivered chunk whose `chunk_id` ends in `section`, so ids stay readable."""
    chunk = Chunk(
        entity=EntityRef("patch_notes", "0-2-0", "Patch 0.2.0"),
        validity=Validity(TARGET),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
    )
    return RetrievedChunk(chunk, EARLIER_SCORES)


ALPHA = make_retrieved("alpha", "Spark deals 10 damage.")
BETA = make_retrieved("beta", "Spark chains to two enemies.")
GAMMA = make_retrieved("gamma", "Glacial Cascade freezes enemies.")


def make_reranker(scores: Any, **kwargs: Any) -> tuple[CrossEncoderReranker, StubScorer]:
    """Build a reranker over a stub scorer and hand both back for assertions."""
    scorer = StubScorer(scores)
    return CrossEncoderReranker(scorer=scorer, **kwargs), scorer


def test_reranker_matches_the_port() -> None:
    reranker, _ = make_reranker([1.0])
    assert isinstance(reranker, Reranker)


def test_scoring_pairs_are_the_query_and_each_chunk_text_in_candidate_order() -> None:
    reranker, scorer = make_reranker([0.9, 0.1, 0.5])

    reranker.rerank(QUERY, (GAMMA, ALPHA, BETA), 3)

    assert [(call.pairs, call.batch_size, call.show_progress_bar) for call in scorer.calls] == [
        (
            (
                (QUERY, GAMMA.chunk.text),
                (QUERY, ALPHA.chunk.text),
                (QUERY, BETA.chunk.text),
            ),
            DEFAULT_BATCH_SIZE,
            False,
        )
    ]


def test_results_come_back_best_first_with_the_rerank_score_and_earlier_scores_kept() -> None:
    reranker, _ = make_reranker([0.1, 0.9, 0.5])

    reranked = reranker.rerank(QUERY, (ALPHA, BETA, GAMMA), 3)

    assert [item.chunk.chunk_id for item in reranked] == [
        BETA.chunk.chunk_id,
        GAMMA.chunk.chunk_id,
        ALPHA.chunk.chunk_id,
    ]
    assert [item.scores.rerank for item in reranked] == [0.9, 0.5, 0.1]
    assert [item.scores for item in reranked] == [
        StageScores(lexical=0.5, dense=0.25, fused=0.125, rerank=0.9),
        StageScores(lexical=0.5, dense=0.25, fused=0.125, rerank=0.5),
        StageScores(lexical=0.5, dense=0.25, fused=0.125, rerank=0.1),
    ]
    # The chunks themselves are the delivered ones, not copies rebuilt from names.
    assert [item.chunk for item in reranked] == [BETA.chunk, GAMMA.chunk, ALPHA.chunk]


def test_equal_scores_break_by_ascending_chunk_id() -> None:
    reranker, _ = make_reranker([0.5, 0.5, 0.5])

    reranked = reranker.rerank(QUERY, (GAMMA, BETA, ALPHA), 3)

    assert [item.chunk.chunk_id for item in reranked] == [
        ALPHA.chunk.chunk_id,
        BETA.chunk.chunk_id,
        GAMMA.chunk.chunk_id,
    ]


@pytest.mark.parametrize(("limit", "expected"), [(2, [1.0, 0.8]), (3, [1.0, 0.8, 0.2]), (9, [1.0, 0.8, 0.2])])
def test_limit_keeps_only_the_best_entries_and_never_invents_any(limit: int, expected: list[float]) -> None:
    reranker, _ = make_reranker([0.8, 1.0, 0.2])

    reranked = reranker.rerank(QUERY, (ALPHA, BETA, GAMMA), limit)

    assert [item.scores.rerank for item in reranked] == expected


def test_empty_candidates_return_empty_without_scoring() -> None:
    reranker, scorer = make_reranker([1.0])

    assert reranker.rerank(QUERY, (), 5) == ()
    assert scorer.calls == []


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_raises(limit: int) -> None:
    reranker, scorer = make_reranker([1.0])

    with pytest.raises(InvalidLimitError):
        reranker.rerank(QUERY, (ALPHA,), limit)
    assert scorer.calls == []


@pytest.mark.parametrize("limit", [None, 1.5, True, "3"])
def test_a_limit_that_is_not_an_int_raises(limit: object) -> None:
    reranker, _ = make_reranker([1.0])

    with pytest.raises(InvalidLimitError):
        reranker.rerank(QUERY, (ALPHA,), limit)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["", "   ", None])
def test_a_blank_query_raises(text: object) -> None:
    reranker, scorer = make_reranker([1.0])

    with pytest.raises(BlankQueryError):
        reranker.rerank(text, (ALPHA,), 1)  # type: ignore[arg-type]
    assert scorer.calls == []


def test_candidates_must_be_retrieved_chunks() -> None:
    reranker, _ = make_reranker([1.0])

    with pytest.raises(RerankerError):
        reranker.rerank(QUERY, ("alpha",), 1)  # type: ignore[arg-type]


def test_a_candidate_that_is_a_string_is_not_read_as_a_sequence_of_chunks() -> None:
    reranker, _ = make_reranker([1.0])

    with pytest.raises(RerankerError):
        reranker.rerank(QUERY, "alpha", 1)  # type: ignore[arg-type]


def test_a_scorer_returning_the_numpy_array_the_real_model_returns_is_accepted() -> None:
    """The real `CrossEncoder.predict` returns an `ndarray` of `float32`, not a `Sequence` of floats."""
    import numpy

    scores = numpy.array([0.1, 0.9, 0.5], dtype=numpy.float32)
    reranker, _ = make_reranker(scores)  # type: ignore[arg-type]

    reranked = reranker.rerank(QUERY, (ALPHA, BETA, GAMMA), 2)

    # `numpy.float32` is not a Python `float`: the adapter must convert, not reject, what the model
    # actually produces.
    assert [item.chunk.chunk_id for item in reranked] == [BETA.chunk.chunk_id, GAMMA.chunk.chunk_id]
    assert [item.scores.rerank for item in reranked] == [0.8999999761581421, 0.5]


def test_a_scorer_that_returns_the_wrong_number_of_scores_raises() -> None:
    reranker, _ = make_reranker([0.5, 0.5])

    with pytest.raises(RerankScoringError):
        reranker.rerank(QUERY, (ALPHA, BETA, GAMMA), 3)


@pytest.mark.parametrize("raw", [0.5, None, object()])
def test_a_scorer_that_returns_something_that_is_not_iterable_raises(raw: Any) -> None:
    reranker, _ = make_reranker(raw)

    with pytest.raises(RerankScoringError):
        reranker.rerank(QUERY, (ALPHA, BETA), 2)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, "0.5", True])
def test_a_score_that_is_not_a_finite_number_raises(bad: Any) -> None:
    reranker, _ = make_reranker([0.5, bad, 0.1])

    with pytest.raises(RerankScoringError):
        reranker.rerank(QUERY, (ALPHA, BETA, GAMMA), 3)


def test_the_model_is_loaded_once_on_first_use_and_never_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[tuple[str, str]] = []
    scorer = StubScorer([0.5])

    def fake_load(model_name: str, device: str) -> StubScorer:
        loads.append((model_name, device))
        return scorer

    monkeypatch.setattr(cross_encoder_reranker, "_load_scorer", fake_load)
    reranker = CrossEncoderReranker(model_name="custom/reranker", device="cuda")
    assert loads == []

    reranker.rerank(QUERY, (ALPHA,), 1)
    reranker.rerank(QUERY, (BETA,), 1)

    assert loads == [("custom/reranker", "cuda")]
    assert len(scorer.calls) == 2


def test_defaults_match_the_configured_model() -> None:
    reranker, _ = make_reranker([1.0])

    assert DEFAULT_MODEL_NAME == "BAAI/bge-reranker-base"
    assert (DEFAULT_DEVICE, DEFAULT_BATCH_SIZE) == ("cpu", 32)
    assert reranker.model_name == DEFAULT_MODEL_NAME


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model_name": ""},
        {"device": "  "},
        {"batch_size": 0},
        {"batch_size": -4},
        {"batch_size": 1.5},
    ],
)
def test_invalid_configuration_raises(kwargs: dict[str, Any]) -> None:
    with pytest.raises(RerankerConfigurationError):
        CrossEncoderReranker(**kwargs)
