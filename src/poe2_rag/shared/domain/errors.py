"""Exceptions raised when a domain invariant is violated."""


class DomainError(ValueError):
    """Base class for every domain invariant violation."""


class InvalidPatchVersionError(DomainError):
    """Raised when a patch version is malformed or has invalid components."""


class InvalidValidityError(DomainError):
    """Raised when a validity range is not a well-formed `[valid_from, valid_until)` interval."""


class InvalidEntityRefError(DomainError):
    """Raised when an entity reference has a blank field or a malformed slug."""


class InvalidSourceRefError(DomainError):
    """Raised when a source reference has a blank type, a non-HTTP URL or a non-date capture."""


class InvalidModifierError(DomainError):
    """Raised when a modifier has a blank stat, a non-numeric value or an unknown kind."""
