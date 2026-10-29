"""Exceptions raised when a domain invariant is violated."""


class DomainError(ValueError):
    """Base class for every domain invariant violation."""


class InvalidPatchVersionError(DomainError):
    """Raised when a patch version is malformed or has invalid components."""


class InvalidValidityError(DomainError):
    """Raised when a validity range is not a well-formed `[valid_from, valid_until)` interval."""
