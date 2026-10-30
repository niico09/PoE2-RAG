"""Reading a saved source snapshot from disk, with no network and no interpretation."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from poe2_rag.ingestion.adapters.outbound.local_file_fetcher import (
    DEFAULT_SOURCE_TYPE,
    METADATA_SUFFIX,
    LocalFileFetcher,
    LocalSnapshotError,
)
from poe2_rag.ingestion.application import RawDocument, SourceFetcher

CAPTURED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
DIFFERENT_MTIME = 1_793_000_000  # 2026-10-26T17:33:20Z, deliberately not CAPTURED_AT


def metadata_path(snapshot: Path) -> Path:
    return snapshot.with_name(snapshot.name + METADATA_SUFFIX)


def write_snapshot(
    tmp_path: Path,
    content: str = "<html><h1>0.1.0 Patch Notes</h1></html>",
    captured_at: datetime = CAPTURED_AT,
) -> Path:
    path = tmp_path / "patch-notes-0.1.0.html"
    path.write_text(content, encoding="utf-8")
    os.utime(path, (DIFFERENT_MTIME, DIFFERENT_MTIME))
    metadata_path(path).write_text(
        json.dumps({"captured_at": captured_at.isoformat()}), encoding="utf-8"
    )
    return path


class TestFetch:
    def test_is_a_source_fetcher(self) -> None:
        assert isinstance(LocalFileFetcher(), SourceFetcher)

    def test_returns_the_snapshot_content(self, tmp_path: Path) -> None:
        path = write_snapshot(tmp_path, "<html>snapshot</html>")

        raw = LocalFileFetcher().fetch(str(path))

        assert isinstance(raw, RawDocument)
        assert raw.content == "<html>snapshot</html>"

    def test_keeps_the_locator_it_was_given(self, tmp_path: Path) -> None:
        path = write_snapshot(tmp_path)

        raw = LocalFileFetcher().fetch(str(path))

        assert raw.locator == str(path)

    def test_uses_the_metadata_date_not_the_file_mtime(self, tmp_path: Path) -> None:
        path = write_snapshot(tmp_path)

        raw = LocalFileFetcher().fetch(str(path))

        assert raw.fetched_at == CAPTURED_AT
        assert raw.fetched_at.date().isoformat() == "2026-09-21"

    def test_defaults_to_the_patch_notes_source_type(self, tmp_path: Path) -> None:
        raw = LocalFileFetcher().fetch(str(write_snapshot(tmp_path)))

        assert raw.source_type == DEFAULT_SOURCE_TYPE == "patch_notes"

    def test_accepts_another_source_type(self, tmp_path: Path) -> None:
        raw = LocalFileFetcher("poe2db").fetch(str(write_snapshot(tmp_path)))

        assert raw.source_type == "poe2db"

    def test_an_empty_file_is_fetched_for_the_parser_to_reject(self, tmp_path: Path) -> None:
        assert LocalFileFetcher().fetch(str(write_snapshot(tmp_path, ""))).content == ""


class TestFailures:
    def test_a_missing_snapshot_raises_a_specific_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "absent.html"

        with pytest.raises(LocalSnapshotError) as exc_info:
            LocalFileFetcher().fetch(str(missing))

        assert str(missing) in str(exc_info.value)

    def test_a_non_utf8_snapshot_raises_a_specific_error(self, tmp_path: Path) -> None:
        path = tmp_path / "latin-1.html"
        path.write_bytes(b"<html>caf\xe9</html>")
        metadata_path(path).write_text(
            json.dumps({"captured_at": CAPTURED_AT.isoformat()}), encoding="utf-8"
        )

        with pytest.raises(LocalSnapshotError) as exc_info:
            LocalFileFetcher().fetch(str(path))

        assert str(path) in str(exc_info.value)

    def test_a_directory_raises_a_specific_error(self, tmp_path: Path) -> None:
        with pytest.raises(LocalSnapshotError):
            LocalFileFetcher().fetch(str(tmp_path))

    def test_rejects_a_blank_source_type(self) -> None:
        with pytest.raises(LocalSnapshotError):
            LocalFileFetcher("")

    def test_the_error_is_an_environment_failure(self) -> None:
        assert issubclass(LocalSnapshotError, RuntimeError)


class TestCaptureMetadata:
    def test_a_snapshot_without_metadata_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "patch-notes-0.1.0.html"
        path.write_text("<html></html>", encoding="utf-8")

        with pytest.raises(LocalSnapshotError) as exc_info:
            LocalFileFetcher().fetch(str(path))

        message = str(exc_info.value)
        assert str(metadata_path(path)) in message
        assert "captured_at" in message

    def test_malformed_metadata_raises(self, tmp_path: Path) -> None:
        path = write_snapshot(tmp_path)
        metadata_path(path).write_text("{not json", encoding="utf-8")

        with pytest.raises(LocalSnapshotError) as exc_info:
            LocalFileFetcher().fetch(str(path))

        assert str(metadata_path(path)) in str(exc_info.value)

    def test_metadata_without_the_capture_date_raises(self, tmp_path: Path) -> None:
        path = write_snapshot(tmp_path)
        metadata_path(path).write_text(json.dumps({"other": "value"}), encoding="utf-8")

        with pytest.raises(LocalSnapshotError):
            LocalFileFetcher().fetch(str(path))

    @pytest.mark.parametrize(
        "captured_at",
        ["2026-09-21", "2026-09-21T12:00:00", 1_758_456_000],
    )
    def test_requires_an_iso_datetime_with_a_timezone(
        self, tmp_path: Path, captured_at: object
    ) -> None:
        path = write_snapshot(tmp_path)
        metadata_path(path).write_text(
            json.dumps({"captured_at": captured_at}), encoding="utf-8"
        )

        with pytest.raises(LocalSnapshotError) as exc_info:
            LocalFileFetcher().fetch(str(path))

        assert "captured_at" in str(exc_info.value)

    def test_accepts_an_offset_timezone(self, tmp_path: Path) -> None:
        path = write_snapshot(tmp_path)
        metadata_path(path).write_text(
            json.dumps({"captured_at": "2026-09-21T09:00:00-03:00"}), encoding="utf-8"
        )

        raw = LocalFileFetcher().fetch(str(path))

        assert raw.fetched_at.date().isoformat() == "2026-09-21"
