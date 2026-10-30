"""Exceptions raised when the ingestion application layer owns a failure.

None of these is a `DomainError`: the domain has no invariant about what a fetcher returns, so a
malformed raw document is an application failure. They still derive from `ValueError`, like the
domain errors, so a caller can catch every malformed value once.
"""


class IngestionError(ValueError):
    """Base class for every failure the ingestion application layer owns."""


class InvalidRawDocumentError(IngestionError):
    """Raised when a raw document has a blank locator or source type, content that is not a string
    or a fetch time that is not a `datetime`."""
