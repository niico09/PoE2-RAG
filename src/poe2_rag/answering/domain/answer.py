"""Generated answer and the citations that back it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from poe2_rag.answering.domain.errors import (
    InvalidAnswerError,
    InvalidCitationError,
    InvalidRetrievedChunkError,
)
from poe2_rag.answering.domain.retrieval import RetrievedChunk
from poe2_rag.shared.domain import is_blank


@dataclass(frozen=True, slots=True)
class Citation:
    """A pointer to the chunk that backs part of the answer, plus the text it quotes (driver D3)."""

    chunk_id: str
    quoted_text: str

    def __post_init__(self) -> None:
        if is_blank(self.chunk_id):
            raise InvalidCitationError("chunk_id must be a non-blank string")
        if is_blank(self.quoted_text):
            raise InvalidCitationError("quoted_text must be a non-blank string")


@dataclass(frozen=True, slots=True)
class Answer:
    """Generated text with the citations that support it; `supported` is False when nothing backs it.

    Direct construction cannot check that a citation names a delivered chunk, because it has no
    delivered set to compare against. `build` is the validating entry point for anything a model
    produced; the constructor enforces the shape and the "no citations means no backing" rule.
    """

    text: str
    citations: tuple[Citation, ...]
    supported: bool

    def __post_init__(self) -> None:
        if is_blank(self.text):
            raise InvalidAnswerError("text must be a non-blank string")
        if not isinstance(self.citations, tuple):
            raise InvalidCitationError(f"citations must be a tuple of Citation, got {self.citations!r}")
        if not all(isinstance(citation, Citation) for citation in self.citations):
            raise InvalidCitationError(f"citations must be Citation values, got {self.citations!r}")
        if not isinstance(self.supported, bool):
            raise InvalidAnswerError(f"supported must be a bool, got {self.supported!r}")
        if self.supported and not self.citations:
            raise InvalidAnswerError("an answer without citations has no backing and cannot be supported")

    @classmethod
    def build(
        cls, text: str, citations: Sequence[Citation], delivered: Sequence[RetrievedChunk]
    ) -> Answer:
        """Build an answer only when every citation names a chunk that was actually delivered.

        The model names chunks by `chunk_id` (ADR-0005), so the domain re-checks that name: a
        citation to a chunk outside the delivered set is an unbacked claim and raises
        `InvalidCitationError`. With no citations the answer comes back unsupported, never as a
        silent success.
        """
        delivered_ids = _delivered_ids(delivered)
        accepted = _require_citations(citations)
        for citation in accepted:
            if citation.chunk_id not in delivered_ids:
                raise InvalidCitationError(
                    f"citation to {citation.chunk_id!r}, which was not delivered"
                )
        return cls(text, accepted, supported=bool(accepted))


def _delivered_ids(delivered: object) -> frozenset[str]:
    """Return the `chunk_id` of every delivered chunk, rejecting anything else."""
    if isinstance(delivered, (str, bytes)) or not isinstance(delivered, Sequence):
        raise InvalidRetrievedChunkError(
            f"delivered must be a sequence of RetrievedChunk, got {delivered!r}"
        )
    ids: set[str] = set()
    for item in delivered:
        if not isinstance(item, RetrievedChunk):
            raise InvalidRetrievedChunkError(f"delivered items must be RetrievedChunk, got {item!r}")
        ids.add(item.chunk.chunk_id)
    return frozenset(ids)


def _require_citations(citations: object) -> tuple[Citation, ...]:
    """Return the citations as a tuple, rejecting anything that is not a sequence of them."""
    if isinstance(citations, (str, bytes)) or not isinstance(citations, Sequence):
        raise InvalidCitationError(f"citations must be a sequence of Citation, got {citations!r}")
    for citation in citations:
        if not isinstance(citation, Citation):
            raise InvalidCitationError(f"citations must be Citation values, got {citation!r}")
    return tuple(citations)
