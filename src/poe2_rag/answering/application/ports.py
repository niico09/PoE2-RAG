"""Outbound ports of the `AnswerQuestion` flow: one responsibility per protocol.

Every port is a `typing.Protocol`, so an adapter only has to match the method signatures and the
use case can run against the in-memory fakes of `tests/unit/answering/application/answering_fakes.py` (§8).
The validity filter lives inside the indexes, never here (§5.1 step 2, ADR-0003).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from poe2_rag.answering.application.errors import InvalidGeneratedAnswerError
from poe2_rag.answering.domain import Citation, RetrievedChunk
from poe2_rag.shared.domain import PatchVersion, is_blank


@runtime_checkable
class Embedder(Protocol):
    """Turn a text into the vector the dense index searches with."""

    def embed(self, text: str) -> tuple[float, ...]:
        """Return the embedding of `text`."""
        ...


@runtime_checkable
class LexicalIndex(Protocol):
    """Keyword search that filters by patch validity inside the index (D1)."""

    def search(self, text: str, version: PatchVersion, limit: int) -> Sequence[RetrievedChunk]:
        """Return up to `limit` chunks matching `text`, best first, with `scores.lexical` set.

        Only chunks valid at `version` may come back: filtering happens before fusion, so a stale
        chunk here is a broken contract and the use case fails loud instead of dropping it.
        """
        ...


@runtime_checkable
class VectorIndex(Protocol):
    """Dense search that filters by patch validity inside the index (D1, ADR-0003)."""

    def search(
        self, vector: Sequence[float], version: PatchVersion, limit: int
    ) -> Sequence[RetrievedChunk]:
        """Return up to `limit` chunks nearest to `vector`, best first, with `scores.dense` set.

        Only chunks valid at `version` may come back: filtering happens before fusion, so a stale
        chunk here is a broken contract and the use case fails loud instead of dropping it.
        """
        ...


@runtime_checkable
class Reranker(Protocol):
    """Reorder the fused candidates with a more expensive, query-aware model."""

    def rerank(
        self, text: str, candidates: Sequence[RetrievedChunk], limit: int
    ) -> Sequence[RetrievedChunk]:
        """Return up to `limit` of `candidates` ordered by relevance to `text`, best first.

        Every returned chunk keeps the scores it arrived with and gains `scores.rerank`.
        """
        ...


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    """Raw model output: text plus the citations it claims, not yet validated (ADR-0005).

    This type only guarantees that the model produced text and citation-shaped values.
    `Answer.build` is what checks those citations against the delivered chunks.
    """

    text: str
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        if is_blank(self.text):
            raise InvalidGeneratedAnswerError("text must be a non-blank string")
        if not isinstance(self.citations, tuple):
            raise InvalidGeneratedAnswerError(
                f"citations must be a tuple of Citation, got {self.citations!r}"
            )
        if not all(isinstance(citation, Citation) for citation in self.citations):
            raise InvalidGeneratedAnswerError(
                f"citations must be Citation values, got {self.citations!r}"
            )


@runtime_checkable
class LanguageModel(Protocol):
    """Generate an answer from the delivered context and name the chunks it relied on."""

    def generate(self, question: str, context: Sequence[RetrievedChunk]) -> GeneratedAnswer:
        """Answer `question` using only `context`, citing the chunks that back every claim."""
        ...
