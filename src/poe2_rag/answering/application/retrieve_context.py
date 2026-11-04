"""Retrieve the context a question needs, before any model is involved (§5.1 steps 1-5).

This is retrieval as its own use case: resolve the target patch, embed the question, search both
indexes, fuse the rankings and rerank the candidates, checking every stage result on the way. It is
the deterministic half of the read path, so it is also the half the evaluation measures (D4,
ADR-0005); nothing here knows about a language model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from poe2_rag.answering.application.errors import (
    ChunkMismatchError,
    DuplicateChunkError,
    MissingScoreError,
    StaleChunkError,
    UnretrievedChunkError,
)
from poe2_rag.answering.application.ports import (
    Embedder,
    LexicalIndex,
    Reranker,
    VectorIndex,
)
from poe2_rag.answering.application.settings import RetrievalSettings
from poe2_rag.answering.domain import (
    Query,
    RetrievedChunk,
    StageScores,
    reciprocal_rank_fusion,
)
from poe2_rag.shared.domain import Chunk, PatchCatalog, PatchVersion


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The patch retrieval answered for and the context it delivered, in the order generation uses."""

    target_version: PatchVersion
    delivered: tuple[RetrievedChunk, ...]


class RetrieveContext:
    """Resolve the target patch, search both indexes, fuse, rerank and validate every stage."""

    __slots__ = (
        "_catalog",
        "_embedder",
        "_lexical_index",
        "_reranker",
        "_settings",
        "_vector_index",
    )

    def __init__(
        self,
        *,
        embedder: Embedder,
        lexical_index: LexicalIndex,
        vector_index: VectorIndex,
        reranker: Reranker,
        catalog: PatchCatalog,
        settings: RetrievalSettings,
    ) -> None:
        self._embedder = embedder
        self._lexical_index = lexical_index
        self._vector_index = vector_index
        self._reranker = reranker
        self._catalog = catalog
        self._settings = settings

    @property
    def final_top_k(self) -> int:
        """Return how many chunks one retrieval delivers at most.

        The budget lives with the use case that applies it, so a caller measuring the retrieval (the
        evaluation, D4) reads the number that actually bounded the delivery instead of a copy.
        """
        return self._settings.final_top_k

    def execute(self, text: str, version: PatchVersion | None = None) -> RetrievalResult:
        """Retrieve the context for `text` at `version`, or at the catalog's current patch.

        `Query.for_catalog` resolves the version, so an unknown patch raises `InvalidQueryError`
        before any port is called. Retrieval runs sequentially in the MVP. Every stage result is
        checked before it is used: a chunk not valid at the target patch raises `StaleChunkError`
        instead of being dropped, because filtering after fusion would leave holes in the top-K (D1,
        §5.1 step 3), and a stage that duplicates a chunk, omits its score or returns a chunk nobody
        retrieved stops the run with an application error (D3, D4). With no candidates, or with a
        reranker that returns none, the delivered context is empty and no further stage runs.
        """
        query = Query.for_catalog(text, self._catalog, version)
        vector = self._embedder.embed(query.text)
        lexical = tuple(
            self._lexical_index.search(
                query.text, query.target_version, self._settings.lexical_top_n
            )
        )
        _require_index_result(lexical, query.target_version, "lexical", "lexical index")
        dense = tuple(
            self._vector_index.search(vector, query.target_version, self._settings.dense_top_n)
        )
        _require_index_result(dense, query.target_version, "dense", "vector index")
        candidates = self._fuse(lexical, dense)
        if not candidates:
            return RetrievalResult(query.target_version, ())
        reranked = tuple(
            self._reranker.rerank(query.text, candidates, self._settings.final_top_k)
        )
        _require_reranked(reranked, candidates, query.target_version)
        return RetrievalResult(query.target_version, reranked[: self._settings.final_top_k])

    def _fuse(
        self, lexical: tuple[RetrievedChunk, ...], dense: tuple[RetrievedChunk, ...]
    ) -> tuple[RetrievedChunk, ...]:
        """Merge both rankings with RRF and keep each chunk's own stage scores."""
        fused = reciprocal_rank_fusion(
            (
                tuple(item.chunk.chunk_id for item in lexical),
                tuple(item.chunk.chunk_id for item in dense),
            ),
            self._settings.rrf_k,
        )
        # chunk_id covers source, entity, valid_from and section, but not text or valid_until, so
        # equal ids do not prove that both index views match. Compare the chunks and refuse to pick
        # one silently: a difference means one index was rebuilt from a different corpus.
        chunks: dict[str, Chunk] = {}
        for item in (*lexical, *dense):
            view = chunks.setdefault(item.chunk.chunk_id, item.chunk)
            if view != item.chunk:
                raise ChunkMismatchError(
                    f"the lexical index and the vector index disagree about "
                    f"{item.chunk.chunk_id!r}"
                )
        lexical_scores = _stage_scores(lexical, "lexical")
        dense_scores = _stage_scores(dense, "dense")
        return tuple(
            RetrievedChunk(
                chunks[chunk_id],
                StageScores(
                    lexical=lexical_scores.get(chunk_id),
                    dense=dense_scores.get(chunk_id),
                    fused=score,
                ),
            )
            for chunk_id, score in fused[: self._settings.rerank_top_m]
        )


def _require_index_result(
    chunks: Sequence[RetrievedChunk],
    version: PatchVersion,
    stage: Literal["lexical", "dense"],
    source: str,
) -> None:
    """Validate what an index returned before anything is fused: valid, distinct and scored."""
    _require_valid(chunks, version, source)
    _require_distinct(chunks, source)
    _require_stage_score(chunks, stage, source)


def _require_reranked(
    reranked: Sequence[RetrievedChunk],
    candidates: Sequence[RetrievedChunk],
    version: PatchVersion,
) -> None:
    """The reranker may only reorder the candidates it received, scored and still valid.

    Validity comes first because a stale chunk is the D1 failure; membership follows, so a chunk
    nobody retrieved cannot enter the answer even when it happens to be valid (D3).
    """
    _require_valid(reranked, version, "reranker")
    _require_candidates(reranked, candidates)
    _require_distinct(reranked, "reranker")
    _require_stage_score(reranked, "rerank", "reranker")


def _require_valid(
    chunks: Sequence[RetrievedChunk], version: PatchVersion, source: str
) -> None:
    """Raise `StaleChunkError` when `source` returned a chunk that is not valid at `version`.

    Dropping the chunk here would satisfy the letter of D1 and still leave a hole in the top-K, so
    the run stops: the validity filter belongs inside the index, and a stale chunk means that
    contract is broken (§5.1 step 2, ADR-0003).
    """
    for item in chunks:
        if not item.chunk.is_valid_at(version):
            raise StaleChunkError(
                f"the {source} returned {item.chunk.chunk_id!r}, which is not valid at {version}"
            )


def _require_distinct(chunks: Sequence[RetrievedChunk], source: str) -> None:
    """Raise `DuplicateChunkError` when `source` returns the same chunk twice in one result."""
    seen: set[str] = set()
    for item in chunks:
        chunk_id = item.chunk.chunk_id
        if chunk_id in seen:
            raise DuplicateChunkError(f"the {source} returned {chunk_id!r} more than once")
        seen.add(chunk_id)


def _require_stage_score(
    chunks: Sequence[RetrievedChunk], stage: Literal["lexical", "dense", "rerank"], source: str
) -> None:
    """Raise `MissingScoreError` when a stage returns a chunk it did not score.

    The per-stage scores are what make the delivered context measurable (§8, D4); a chunk without
    the score of the stage that produced it cannot be attributed in the evaluation.
    """
    for item in chunks:
        if getattr(item.scores, stage) is None:
            raise MissingScoreError(
                f"the {source} returned {item.chunk.chunk_id!r} without its {stage} score"
            )


def _require_candidates(
    reranked: Sequence[RetrievedChunk], candidates: Sequence[RetrievedChunk]
) -> None:
    """Raise `UnretrievedChunkError` when the reranker returns a chunk it was not given."""
    allowed = {item.chunk.chunk_id for item in candidates}
    for item in reranked:
        if item.chunk.chunk_id not in allowed:
            raise UnretrievedChunkError(
                f"the reranker returned {item.chunk.chunk_id!r}, which was not retrieved"
            )


def _stage_scores(
    chunks: Sequence[RetrievedChunk], stage: Literal["lexical", "dense"]
) -> dict[str, float]:
    """Return `chunk_id -> score` for one stage, skipping chunks that stage did not score."""
    scores: dict[str, float] = {}
    for item in chunks:
        score = getattr(item.scores, stage)
        if score is not None:
            scores[item.chunk.chunk_id] = score
    return scores
