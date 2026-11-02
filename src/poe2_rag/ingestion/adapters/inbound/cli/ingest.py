"""CLI command that ingests one source and rebuilds the indexes (S3).

This module owns no business logic: it dispatches to the injected application runner for the
chosen source, prints the resulting reports, and lets any error propagate to the boundary that
maps it to an exit code.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from poe2_rag.ingestion.application.ingest_source import IngestReport
from poe2_rag.ingestion.application.reindex_corpus import ReindexReport

SOURCES: tuple[str, ...] = ("patch-notes", "poe2db")
"""The closed set of source names the `ingest` command accepts. The wiki is added here when its
adapter exists."""


class IngestSourceRunner(Protocol):
    """Port the CLI uses to run an ingest use case without knowing its constructor."""

    def execute(self, locator: str) -> IngestReport:
        """Ingest the document at `locator` and return the report."""
        ...


class ReindexCorpusRunner(Protocol):
    """Port the CLI uses to run the reindex use case without knowing its constructor."""

    def execute(self) -> ReindexReport:
        """Rebuild the indexes and return the report."""
        ...


def _run_source(
    source: str,
    *,
    locator: str,
    ingest: IngestSourceRunner,
    reindex: ReindexCorpusRunner,
) -> None:
    """Ingest one source document, then rebuild the indexes from the resulting corpus."""
    ingest_report = ingest.execute(locator)
    reindex_report = reindex.execute()

    print(
        f"Ingested {source} from {ingest_report.locator}: "
        f"{len(ingest_report.chunk_ids)} chunks produced, {ingest_report.skipped} skipped, "
        f"{ingest_report.closed} closed, {ingest_report.removed} removed."
    )

    reindex_line = (
        f"Reindexed {reindex_report.chunk_count} chunks "
        f"at {reindex_report.rebuilt_at.isoformat()}."
    )
    if reindex_report.truncated_chunk_ids:
        reindex_line += (
            " Truncated chunk ids: " + ", ".join(reindex_report.truncated_chunk_ids) + "."
        )
    print(reindex_line)


def _run_patch_notes(*, locator: str, ingest: IngestSourceRunner, reindex: ReindexCorpusRunner) -> None:
    """Ingest a patch-notes document and rebuild the indexes from the resulting corpus."""
    _run_source("patch-notes", locator=locator, ingest=ingest, reindex=reindex)


def _run_poe2db(*, locator: str, ingest: IngestSourceRunner, reindex: ReindexCorpusRunner) -> None:
    """Ingest one poe2db page and rebuild the indexes from the resulting corpus."""
    _run_source("poe2db", locator=locator, ingest=ingest, reindex=reindex)


_SOURCE_HANDLERS: dict[str, Callable[..., None]] = {
    "patch-notes": _run_patch_notes,
    "poe2db": _run_poe2db,
}


def run(*, source: str, locator: str, ingest: IngestSourceRunner, reindex: ReindexCorpusRunner) -> None:
    """Dispatch to the handler for `source`, then print both reports in fixed order.

    `ingest.execute(locator)` always runs first; `reindex.execute()` runs only after it succeeds.
    An unknown `source` raises `ValueError`: argparse already restricts the choice, so this is the
    belt-and-braces path and keeps the dispatch table explicit.
    """
    handler = _SOURCE_HANDLERS.get(source)
    if handler is None:
        raise ValueError(f"unknown source: {source!r}")
    handler(locator=locator, ingest=ingest, reindex=reindex)
