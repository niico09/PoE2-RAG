"""Ingestion application: one source orchestrated over its outbound ports (§5)."""

from poe2_rag.ingestion.application.errors import (
    DuplicateChunkIdError,
    IngestionError,
    InvalidRawDocumentError,
    UnknownPatchError,
)
from poe2_rag.ingestion.application.ingest_source import IngestReport, IngestSource
from poe2_rag.ingestion.application.ports import (
    ChunkRepository,
    DocumentParser,
    RawDocument,
    SourceFetcher,
)

__all__ = [
    "ChunkRepository",
    "DocumentParser",
    "DuplicateChunkIdError",
    "IngestReport",
    "IngestSource",
    "IngestionError",
    "InvalidRawDocumentError",
    "RawDocument",
    "SourceFetcher",
    "UnknownPatchError",
]
