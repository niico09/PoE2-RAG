"""Exceptions raised when an outbound port breaks its contract during a query.

None of these is a `DomainError`: the domain has no invariant about what an index, a reranker or a
language model returns, so a broken outbound contract is an application failure. They still derive
from `ValueError`, like the domain errors, so a caller can catch every malformed value once.
"""


class AnswerQuestionError(ValueError):
    """Base class for every failure the query application layer owns."""


class InvalidGeneratedAnswerError(AnswerQuestionError):
    """Raised when raw model output has blank text or holds anything but a tuple of citations."""


class InvalidRetrievalSettingsError(AnswerQuestionError):
    """Raised when a retrieval budget is not a positive int or `final_top_k` exceeds `rerank_top_m`."""


class StaleChunkError(AnswerQuestionError):
    """Raised when a retrieval stage returns a chunk that is not valid at the target patch (D1)."""


class UnretrievedChunkError(AnswerQuestionError):
    """Raised when the reranker returns a chunk that the indexes never retrieved."""


class DuplicateChunkError(AnswerQuestionError):
    """Raised when one stage returns the same chunk_id more than once in a single result."""


class MissingScoreError(AnswerQuestionError):
    """Raised when a stage returns a chunk without the score that stage is responsible for (D4)."""


class ChunkMismatchError(AnswerQuestionError):
    """Raised when the two indexes return different chunks for the same chunk_id."""
