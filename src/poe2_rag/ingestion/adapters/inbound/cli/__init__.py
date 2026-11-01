"""Inbound CLI adapter for ingestion.

The module exposes the closed set of source names and the command runner, plus the protocol
names used by callers that inject application runners.
"""

from poe2_rag.ingestion.adapters.inbound.cli.ingest import (
    SOURCES,
    IngestSourceRunner,
    ReindexCorpusRunner,
    run,
)

__all__ = [
    "SOURCES",
    "IngestSourceRunner",
    "ReindexCorpusRunner",
    "run",
]
