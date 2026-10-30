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


class UnknownPatchError(IngestionError):
    """Raised when a parsed chunk starts at a patch the catalog does not know (driver D1).

    A chunk whose `valid_from` is unknown cannot be placed on the timeline, so storing it would make
    the corpus claim a patch that the system does not acknowledge. The run stops instead of skipping
    the chunk.
    """


class DuplicateChunkIdError(IngestionError):
    """Raised when one document produces the same `chunk_id` more than once.

    A duplicate means two different sections resolve to the same identity, so persisting both would
    silently keep only one. The run stops before anything is written.
    """


class ChunkIdentityConflictError(IngestionError):
    """Raised when a `chunk_id` already stored from another source document is claimed again.

    `chunk_id` covers source type, entity, patch and section but not the document URL, so two
    threads of the same patch with a section of the same name would resolve to one identity. The run
    stops instead of overwriting the first document's text and moving its citation to the second URL.
    """


class EmptyCorpusError(IngestionError):
    """Raised when a reindex runs over a corpus with no chunks (driver D1).

    An index rebuilt from nothing answers every question with "no information", which is
    indistinguishable from a corpus that genuinely lacks the answer. The run stops before any writer
    is called, so the index of the previous rebuild survives untouched instead of being replaced by
    an empty one.
    """
