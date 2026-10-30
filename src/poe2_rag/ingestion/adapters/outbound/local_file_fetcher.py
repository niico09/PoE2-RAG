"""Fetch a saved source snapshot from the local filesystem.

Polite HTTP fetching is a separate adapter: this one reads a snapshot that was already downloaded
into `data/raw/`, so tests and manual ingests never touch the network. The capture date travels with
the content in a sidecar `<snapshot>.meta.json` (`captured_at`, ISO 8601 with timezone), never in the
file mtime: a copy that does not preserve mtime would otherwise silently falsify every citation date
of that document. A missing or malformed sidecar fails loud instead of guessing.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from poe2_rag.ingestion.application import RawDocument
from poe2_rag.shared.domain import is_blank

DEFAULT_SOURCE_TYPE = "patch_notes"
METADATA_SUFFIX = ".meta.json"


class LocalSnapshotError(RuntimeError):
    """Raised when a saved snapshot or its capture metadata cannot be read.

    Deliberately not a `DomainError` and not an `IngestionError`: a missing or undecodable file is an
    environment failure, not a malformed value produced by the application.
    """


class LocalFileFetcher:
    """Read a snapshot and its sidecar metadata from disk, and describe it as a `RawDocument`."""

    __slots__ = ("_source_type",)

    def __init__(self, source_type: str = DEFAULT_SOURCE_TYPE) -> None:
        if is_blank(source_type):
            raise LocalSnapshotError("source_type must be a non-blank string")
        self._source_type = source_type

    def fetch(self, locator: str) -> RawDocument:
        """Return the snapshot at `locator` with its sidecar capture time as `fetched_at`.

        `locator` stays exactly as given so a report can echo it; the parser recovers the source URL
        from the document itself when the locator is not an absolute URL.
        """
        path = Path(locator)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LocalSnapshotError(f"cannot read snapshot {path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise LocalSnapshotError(f"{path} is not valid UTF-8: {exc}") from exc
        return RawDocument(
            locator=locator,
            content=content,
            fetched_at=_read_capture_time(path),
            source_type=self._source_type,
        )


def _read_capture_time(path: Path) -> datetime:
    """Read `captured_at` from the snapshot's sidecar, or fail loud when it is unusable."""
    metadata_path = path.with_name(path.name + METADATA_SUFFIX)
    try:
        text = metadata_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LocalSnapshotError(
            f"cannot read capture metadata {metadata_path}: {exc}; create it with "
            f'{{"captured_at": "<ISO 8601 datetime with timezone>"}}'
        ) from exc
    except UnicodeDecodeError as exc:
        raise LocalSnapshotError(f"{metadata_path} is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LocalSnapshotError(f"{metadata_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LocalSnapshotError(f"{metadata_path} must contain a JSON object")
    value = data.get("captured_at")
    if not isinstance(value, str):
        raise LocalSnapshotError(
            f"{metadata_path}: 'captured_at' must be an ISO 8601 datetime string"
        )
    try:
        captured_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LocalSnapshotError(
            f"{metadata_path}: 'captured_at' is not a valid ISO 8601 datetime: {value!r}"
        ) from exc
    if captured_at.tzinfo is None:
        raise LocalSnapshotError(
            f"{metadata_path}: 'captured_at' must include a timezone, got {value!r}"
        )
    return captured_at
