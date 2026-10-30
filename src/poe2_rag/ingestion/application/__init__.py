"""Ingestion application: one source orchestrated over its outbound ports (§5)."""

from poe2_rag.ingestion.application.errors import (
    IngestionError,
    InvalidRawDocumentError,
)
from poe2_rag.ingestion.application.ports import (
    ChunkRepository,
    DocumentParser,
    RawDocument,
    SourceFetcher,
)

__all__ = [
    "ChunkRepository",
    "DocumentParser",
    "IngestionError",
    "InvalidRawDocumentError",
    "RawDocument",
    "SourceFetcher",
]
