"""Exceptions raised when an answering-domain invariant is violated."""

from poe2_rag.shared.domain.errors import DomainError


class InvalidQueryError(DomainError):
    """Raised when a query has a blank text or a target version the catalog does not know."""


class InvalidStageScoresError(DomainError):
    """Raised when a stage score is neither `None` nor a finite int or float."""


class InvalidRetrievedChunkError(DomainError):
    """Raised when a retrieved chunk does not compose a `Chunk` with its `StageScores`."""


class InvalidRankingError(DomainError):
    """Raised when a fusion input is not a sequence of rankings of unique, non-blank chunk ids."""
