"""Answering domain: query, retrieval fusion and cited answers, free of external dependencies."""

from poe2_rag.answering.domain.errors import (
    InvalidQueryError,
    InvalidRankingError,
    InvalidRetrievedChunkError,
    InvalidStageScoresError,
)
from poe2_rag.answering.domain.fusion import reciprocal_rank_fusion
from poe2_rag.answering.domain.query import Query
from poe2_rag.answering.domain.retrieval import RetrievedChunk, StageScores

__all__ = [
    "InvalidQueryError",
    "InvalidRankingError",
    "InvalidRetrievedChunkError",
    "InvalidStageScoresError",
    "Query",
    "RetrievedChunk",
    "StageScores",
    "reciprocal_rank_fusion",
]
