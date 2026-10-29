"""Orchestrate the read path end to end: question in, validated cited answer out (§5.1)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from poe2_rag.answering.application.errors import StaleChunkError
from poe2_rag.answering.application.ports import (
    Embedder,
    LanguageModel,
    LexicalIndex,
    Reranker,
    VectorIndex,
)
from poe2_rag.answering.application.settings import RetrievalSettings
from poe2_rag.answering.domain import (
    Answer,
    Query,
    RetrievedChunk,
    StageScores,
    reciprocal_rank_fusion,
)
from poe2_rag.shared.domain import Chunk, PatchCatalog, PatchVersion

NO_INFORMATION_TEXT = "No information available for this question in the selected patch."


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """The validated answer plus the patch it was answered for and the context that backed it."""

    answer: Answer
    target_version: PatchVersion
    delivered: tuple[RetrievedChunk, ...]


class AnswerQuestion:
    """Answer a question for one patch: retrieve, fuse, rerank, generate, validate citations."""

    __slots__ = (
        "_catalog",
        "_embedder",
        "_language_model",
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
        language_model: LanguageModel,
        catalog: PatchCatalog,
        settings: RetrievalSettings,
    ) -> None:
        self._embedder = embedder
        self._lexical_index = lexical_index
        self._vector_index = vector_index
        self._reranker = reranker
        self._language_model = language_model
        self._catalog = catalog
        self._settings = settings

    def execute(self, text: str, version: PatchVersion | None = None) -> AnswerResult:
        """Answer `text` for `version`, or for the catalog's current patch when it is omitted.

        `Query.for_catalog` resolves the version, so an unknown patch raises `InvalidQueryError`
        before any port is called. Retrieval runs sequentially in the MVP. Validity is enforced
        before fusion: a chunk any stage returns that is not valid at the target patch raises
        `StaleChunkError` instead of being dropped, because filtering after fusion would leave
        holes in the top-K (D1, §5.1 step 3). With no candidates, or with a reranker that returns
        none, the model is never called and the answer is the fixed, unsupported no-information
        text. `Answer.build` validates the citations: a citation to an undelivered chunk raises
        `InvalidCitationError` instead of being dropped (ADR-0005).
        """
        query = Query.for_catalog(text, self._catalog, version)
        vector = self._embedder.embed(query.text)
        lexical = tuple(
            self._lexical_index.search(
                query.text, query.target_version, self._settings.lexical_top_n
            )
        )
        _require_valid(lexical, query.target_version, "lexical index")
        dense = tuple(
            self._vector_index.search(vector, query.target_version, self._settings.dense_top_n)
        )
        _require_valid(dense, query.target_version, "vector index")
        candidates = self._fuse(lexical, dense)
        if not candidates:
            return self._unsupported(query.target_version)
        reranked = tuple(
            self._reranker.rerank(query.text, candidates, self._settings.final_top_k)
        )
        _require_valid(reranked, query.target_version, "reranker")
        delivered = reranked[: self._settings.final_top_k]
        if not delivered:
            return self._unsupported(query.target_version)
        generated = self._language_model.generate(query.text, delivered)
        answer = Answer.build(generated.text, generated.citations, delivered)
        return AnswerResult(answer, query.target_version, delivered)

    def _unsupported(self, version: PatchVersion) -> AnswerResult:
        """Return the fixed unsupported answer for a run that delivered nothing (§5.1 step 6)."""
        return AnswerResult(Answer(NO_INFORMATION_TEXT, (), False), version, ())

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
        # A chunk id is derived from the chunk itself, so both indexes describe the same chunk for
        # the same id; the first view (lexical) supplies the text sent to the model.
        chunks: dict[str, Chunk] = {}
        for item in (*lexical, *dense):
            chunks.setdefault(item.chunk.chunk_id, item.chunk)
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
