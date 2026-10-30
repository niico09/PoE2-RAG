"""Cross-encoder reranker: score every (question, chunk) pair and keep the best ones (§5.1 step 5).

A bi-encoder compares two vectors that were built without seeing each other, so it cannot tell "10%
increased damage" from "10% reduced damage". A cross-encoder reads the question and the chunk in one
pass, which is what makes it the stage that fixes fine-grained relevance, and also why it is too
expensive to run over the whole corpus: it only sees the top-M that fusion already narrowed down.

The model is a download and a slow import, so nothing here touches it at construction time:
`model_name`, `device` and `batch_size` are configuration, and the model loads on the first `rerank`
call. That keeps the composition root cheap and lets the default test suite run offline against an
injected scorer.

Every returned chunk keeps the scores it arrived with and only gains `scores.rerank`, because the
whole pipeline is meant to be debuggable and evaluable per stage (§4.2): a chunk the reranker dropped
but fusion ranked first stays visible instead of lost. The order is total — score descending, then
`chunk_id` ascending — so two runs over the same candidates return the same ranking.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import replace
from typing import Any, Protocol, cast

from poe2_rag.answering.domain import RetrievedChunk
from poe2_rag.shared.domain import is_blank

DEFAULT_MODEL_NAME = "BAAI/bge-reranker-base"
DEFAULT_DEVICE = "cpu"
DEFAULT_BATCH_SIZE = 32


class RerankerError(ValueError):
    """Base class for caller-provided invalid arguments to the reranker."""


class RerankerConfigurationError(RerankerError):
    """Raised when the reranker is configured with an unusable value."""


class BlankQueryError(RerankerError):
    """Raised when the text to rerank against is blank or not a string."""


class InvalidLimitError(RerankerError):
    """Raised when the rerank limit is not a positive integer."""


class RerankScoringError(RuntimeError):
    """Raised when the scorer returns a number of scores or a score that cannot be ranked.

    Silent zipping or sorting of a bad score would reorder the context with values nobody checked,
    so the adapter stops instead.
    """


class _Scorer(Protocol):
    """The slice of `CrossEncoder` this adapter uses, so a stub can stand in for it."""

    def predict(
        self, inputs: list[tuple[str, str]], *, batch_size: int, show_progress_bar: bool
    ) -> Any:
        """Return one relevance score per (query, passage) pair, in order."""
        ...


def _load_scorer(model_name: str, device: str) -> _Scorer:
    """Build the real cross-encoder; imported here so importing this module never imports torch.

    `CrossEncoder` accepts every keyword `predict` declares, but its overloads are wider than the
    subset this adapter uses, so the structural match needs a cast.
    """
    from sentence_transformers import CrossEncoder

    return cast(_Scorer, CrossEncoder(model_name, device=device))


class CrossEncoderReranker:
    """Re-rank fused candidates with a local cross-encoder, best first, within the given budget."""

    __slots__ = ("_batch_size", "_device", "_model_name", "_scorer_instance")

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        *,
        device: str = DEFAULT_DEVICE,
        batch_size: int = DEFAULT_BATCH_SIZE,
        scorer: _Scorer | None = None,
    ) -> None:
        if is_blank(model_name):
            raise RerankerConfigurationError(
                f"model_name must be a non-blank string, got {model_name!r}"
            )
        if is_blank(device):
            raise RerankerConfigurationError(f"device must be a non-blank string, got {device!r}")
        if type(batch_size) is not int or batch_size <= 0:
            raise RerankerConfigurationError(
                f"batch_size must be a positive int, got {batch_size!r}"
            )
        self._model_name = model_name
        self._device = device
        self._batch_size = batch_size
        self._scorer_instance = scorer

    @property
    def model_name(self) -> str:
        """The model that produces the `scores.rerank` this adapter assigns."""
        return self._model_name

    def rerank(
        self, text: str, candidates: Sequence[RetrievedChunk], limit: int
    ) -> Sequence[RetrievedChunk]:
        """Return up to `limit` of `candidates`, ordered by cross-encoder score, best first.

        Every returned chunk keeps its earlier stage scores and gains `scores.rerank`. Equal scores
        break by ascending `chunk_id`, so the ranking is total. An empty candidate sequence returns
        empty without loading the model; a non-positive `limit` raises instead of returning nothing.
        """
        _require_text(text)
        _require_limit(limit)
        materialized = _require_candidates(candidates)
        if not materialized:
            return ()
        scores = self._score(text, materialized)
        ranked = sorted(
            range(len(materialized)),
            key=lambda index: (-scores[index], materialized[index].chunk.chunk_id),
        )
        return tuple(
            replace(materialized[index], scores=replace(materialized[index].scores, rerank=scores[index]))
            for index in ranked[:limit]
        )

    def _score(self, text: str, candidates: tuple[RetrievedChunk, ...]) -> list[float]:
        """Ask the scorer for one score per candidate and check what came back."""
        pairs = [(text, candidate.chunk.text) for candidate in candidates]
        raw = self._scorer().predict(pairs, batch_size=self._batch_size, show_progress_bar=False)
        return _require_scores(raw, len(pairs))

    def _scorer(self) -> _Scorer:
        """Return the scorer, loading the model once on first use."""
        if self._scorer_instance is None:
            self._scorer_instance = _load_scorer(self._model_name, self._device)
        return self._scorer_instance


def _require_text(text: object) -> None:
    """Reject a blank or non-string text before the model is loaded."""
    if is_blank(text):
        raise BlankQueryError(f"text must be a non-blank string, got {text!r}")


def _require_limit(limit: object) -> None:
    """Reject a limit the reranker cannot honour."""
    if type(limit) is not int or limit <= 0:
        raise InvalidLimitError(f"limit must be a positive int, got {limit!r}")


def _require_candidates(candidates: object) -> tuple[RetrievedChunk, ...]:
    """Materialize the candidates, rejecting anything a reranker cannot score.

    A bare string is rejected explicitly: iterating it would read characters as chunks.
    """
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise RerankerError(
            f"candidates must be a sequence of RetrievedChunk, got {candidates!r}"
        )
    materialized = tuple(candidates)
    for candidate in materialized:
        if not isinstance(candidate, RetrievedChunk):
            raise RerankerError(f"candidates must be RetrievedChunk, got {candidate!r}")
    return materialized


def _require_scores(raw: object, expected: int) -> list[float]:
    """Return one finite float per pair, so a malformed scorer cannot rank the context.

    The real `CrossEncoder.predict` returns an `ndarray` of `float32`, which is iterable but is not a
    `collections.abc.Sequence` and whose elements are not Python floats, so anything iterable is
    accepted and each value is converted with `float`, rather than checked against `int`/`float`.
    Strings and booleans are rejected before that conversion, because `float("0.5")` and
    `float(True)` would be accepted silently.
    """
    if isinstance(raw, (str, bytes)) or not hasattr(raw, "__iter__"):
        raise RerankScoringError(f"the scorer must return iterable scores, got {raw!r}")
    try:
        # `hasattr` narrows nothing for a type checker, so the iterable is asserted here: the
        # `list` call is what actually rejects a non-iterable, and its `TypeError` is translated.
        values = list(cast(Iterable[Any], raw))
    except TypeError as exc:
        raise RerankScoringError(f"the scorer must return iterable scores, got {raw!r}") from exc
    if len(values) != expected:
        raise RerankScoringError(
            f"the scorer returned {len(values)} scores for {expected} candidate pairs"
        )
    scores: list[float] = []
    for value in values:
        if isinstance(value, (bool, str, bytes)):
            raise RerankScoringError(f"a rerank score must be a number, got {value!r}")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RerankScoringError(f"a rerank score must be a number, got {value!r}") from exc
        if not math.isfinite(number):
            raise RerankScoringError(f"a rerank score must be finite, got {value!r}")
        scores.append(number)
    return scores
