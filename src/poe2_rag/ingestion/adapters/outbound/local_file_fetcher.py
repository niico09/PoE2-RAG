"""Fetch a saved source snapshot from the local filesystem.

Polite HTTP fetching is a separate adapter and the next task: this one reads a snapshot that was
already downloaded into `data/raw/` by hand, so tests and manual ingests never touch the network.
The snapshot's mtime is its capture time: a citation must say when the content was captured, not when
this process happened to read the file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from poe2_rag.ingestion.application import RawDocument
from poe2_rag.shared.domain import is_blank

DEFAULT_SOURCE_TYPE = "patch_notes"


class LocalSnapshotError(RuntimeError):
    """Raised when a saved snapshot cannot be read as UTF-8 text.

    Deliberately not a `DomainError` and not an `IngestionError`: a missing or undecodable file is an
    environment failure, not a malformed value produced by the application.
    """


class LocalFileFetcher:
    """Read a snapshot from disk and describe it as a `RawDocument`."""

    __slots__ = ("_source_type",)

    def __init__(self, source_type: str = DEFAULT_SOURCE_TYPE) -> None:
        if is_blank(source_type):
            raise LocalSnapshotError("source_type must be a non-blank string")
        self._source_type = source_type

    def fetch(self, locator: str) -> RawDocument:
        """Return the snapshot at `locator` with its mtime as `fetched_at`.

        `locator` stays exactly as given so a report can echo it; the parser recovers the source URL
        from the document itself when the locator is not an absolute URL.
        """
        path = Path(locator)
        try:
            content = path.read_text(encoding="utf-8")
            captured_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError as exc:
            raise LocalSnapshotError(f"cannot read snapshot {path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise LocalSnapshotError(f"{path} is not valid UTF-8: {exc}") from exc
        return RawDocument(
            locator=locator,
            content=content,
            fetched_at=captured_at,
            source_type=self._source_type,
        )
