"""Ingestion application: one source orchestrated over its outbound ports (§5)."""

from poe2_rag.ingestion.application.errors import (
    ChunkIdentityConflictError,
    DuplicateChunkIdError,
    EmptyCorpusError,
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
from poe2_rag.ingestion.application.reindex_corpus import ReindexCorpus, ReindexReport

__all__ = [
    "ChunkIdentityConflictError",
    "ChunkRepository",
    "DocumentParser",
    "DuplicateChunkIdError",
    "EmptyCorpusError",
    "IngestReport",
    "IngestSource",
    "IngestionError",
    "InvalidRawDocumentError",
    "LexicalIndexWriter",
    "RawDocument",
    "ReindexCorpus",
    "ReindexReport",
    "SourceFetcher",
    "UnknownPatchError",
]
