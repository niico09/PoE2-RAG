"""Exceptions raised when an evaluation-domain invariant is violated."""

from poe2_rag.shared.domain.errors import DomainError


class InvalidEvalCaseError(DomainError):
    """Raised when a dataset case is malformed: a bad id, a blank question or an unusable field."""


class InvalidMetricsError(DomainError):
    """Raised when a scored case is inconsistent or the requested K set cannot be measured."""
