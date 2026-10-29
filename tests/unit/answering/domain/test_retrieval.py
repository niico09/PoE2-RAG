from datetime import date

import pytest

from poe2_rag.answering.domain import (
    InvalidRetrievedChunkError,
    InvalidStageScoresError,
    RetrievedChunk,
    StageScores,
)
from poe2_rag.shared.domain import (
    Chunk,
    DomainError,
    EntityRef,
    PatchVersion,
    SourceRef,
    Validity,
)


def make_chunk(section: str = "description") -> Chunk:
    return Chunk(
        entity=EntityRef("active_gem", "spark", "Spark"),
        validity=Validity(PatchVersion.parse("0.1.2")),
        section=section,
        text="Spark fires lightning projectiles that bounce off walls.",
        source=SourceRef("poe2db", "https://poe2db.tw/us/Spark", date(2026, 10, 29)),
    )


def test_errors_are_domain_errors() -> None:
    assert issubclass(InvalidStageScoresError, DomainError)
    assert issubclass(InvalidRetrievedChunkError, DomainError)


class TestStageScores:
    def test_every_stage_defaults_to_none(self) -> None:
        scores = StageScores()

        assert scores.lexical is None
        assert scores.dense is None
        assert scores.fused is None
        assert scores.rerank is None

    def test_keeps_every_stage_independently(self) -> None:
        scores = StageScores(lexical=12.5, dense=0.87, fused=0.032, rerank=-3.1)

        assert scores.lexical == 12.5
        assert scores.dense == 0.87
        assert scores.fused == 0.032
        assert scores.rerank == -3.1

    def test_accepts_a_partial_pipeline(self) -> None:
        scores = StageScores(lexical=4.0, fused=0.016)

        assert scores.dense is None
        assert scores.rerank is None

    def test_accepts_integers_and_zero(self) -> None:
        assert StageScores(lexical=0, dense=0.0).lexical == 0

    @pytest.mark.parametrize("stage", ["lexical", "dense", "fused", "rerank"])
    def test_rejects_a_non_numeric_score(self, stage: str) -> None:
        with pytest.raises(InvalidStageScoresError, match=stage):
            StageScores(**{stage: "high"})  # type: ignore[arg-type]

    @pytest.mark.parametrize("stage", ["lexical", "dense", "fused", "rerank"])
    def test_rejects_a_bool_score(self, stage: str) -> None:
        with pytest.raises(InvalidStageScoresError, match=stage):
            StageScores(**{stage: True})

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_a_non_finite_score(self, value: float) -> None:
        with pytest.raises(InvalidStageScoresError, match="lexical"):
            StageScores(lexical=value)


class TestRetrievedChunk:
    def test_keeps_chunk_and_scores(self) -> None:
        chunk = make_chunk()
        scores = StageScores(lexical=12.5)

        retrieved = RetrievedChunk(chunk, scores)

        assert retrieved.chunk is chunk
        assert retrieved.scores is scores

    def test_keeps_the_delivered_chunk_untouched(self) -> None:
        chunk = make_chunk()

        assert RetrievedChunk(chunk, StageScores()).chunk is chunk

    def test_rejects_a_chunk_of_the_wrong_type(self) -> None:
        with pytest.raises(InvalidRetrievedChunkError, match="chunk"):
            RetrievedChunk("active_gem:spark@0.1.2#description", StageScores())  # type: ignore[arg-type]

    def test_rejects_scores_of_the_wrong_type(self) -> None:
        with pytest.raises(InvalidRetrievedChunkError, match="scores"):
            RetrievedChunk(make_chunk(), 12.5)  # type: ignore[arg-type]

    def test_rejects_missing_scores(self) -> None:
        with pytest.raises(InvalidRetrievedChunkError, match="scores"):
            RetrievedChunk(make_chunk(), None)  # type: ignore[arg-type]
