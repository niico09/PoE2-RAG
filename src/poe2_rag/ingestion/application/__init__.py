"""Ingestion application: one source orchestrated over its outbound ports (§5)."""

from poe2_rag.ingestion.application.errors import (
    ChunkIdentityConflictError,
    DuplicateChunkIdError,
    IngestionError,
    InvalidRawDocumentError,
    UnknownPatchError,
)
from poe2_rag.ingestion.application.ingest_source import IngestReport, IngestSource
from poe2_rag.ingestion.application.ports import (
    ChunkRepository,
    DocumentParser,
    LexicalIndexWriter,
    RawDocument,
    SourceFetcher,
)

__all__ = [
    "ChunkIdentityConflictError",
    "ChunkRepository",
    "DocumentParser",
    "DuplicateChunkIdError",
    "IngestReport",
    "IngestSource",
    "IngestionError",
    "InvalidRawDocumentError",
    "LexicalIndexWriter",
    "RawDocument",
    "SourceFetcher",
    "UnknownPatchError",
]
