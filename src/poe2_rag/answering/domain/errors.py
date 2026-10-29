"""Exceptions raised when an answering-domain invariant is violated."""

from poe2_rag.shared.domain.errors import DomainError


class InvalidQueryError(DomainError):
    """Raised when a query has a blank text or a target version the catalog does not know."""
