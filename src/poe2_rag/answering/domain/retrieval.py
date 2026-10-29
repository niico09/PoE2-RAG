"""Chunks delivered by retrieval, with the score every pipeline stage gave them."""

from __future__ import annotations

import math
from dataclasses import dataclass

from poe2_rag.answering.domain.errors import (
    InvalidRetrievedChunkError,
    InvalidStageScoresError,
)
from poe2_rag.shared.domain.chunk import Chunk

_STAGES = ("lexical", "dense", "fused", "rerank")


@dataclass(frozen=True, slots=True)
class StageScores:
    """Score per retrieval stage; `None` means that stage did not score this chunk.

    Keeping every stage makes retrieval debuggable and evaluable (§4.2): a chunk that the reranker
    dropped but fusion ranked first is visible instead of lost.
    """

    lexical: float | None = None
    dense: float | None = None
    fused: float | None = None
    rerank: float | None = None

    def __post_init__(self) -> None:
        for stage in _STAGES:
            value = getattr(self, stage)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidStageScoresError(f"{stage} must be an int, a float or None, got {value!r}")
            if not math.isfinite(value):
                raise InvalidStageScoresError(f"{stage} must be finite, got {value!r}")


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk that reached the context window, paired with the scores that got it there."""

    chunk: Chunk
    scores: StageScores

    def __post_init__(self) -> None:
        if not isinstance(self.chunk, Chunk):
            raise InvalidRetrievedChunkError(f"chunk must be a Chunk, got {self.chunk!r}")
        if not isinstance(self.scores, StageScores):
            raise InvalidRetrievedChunkError(f"scores must be StageScores, got {self.scores!r}")
