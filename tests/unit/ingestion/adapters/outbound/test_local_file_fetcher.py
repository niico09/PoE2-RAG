"""Reading a saved source snapshot from disk, with no network and no interpretation."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from poe2_rag.ingestion.adapters.outbound.local_file_fetcher import (
    DEFAULT_SOURCE_TYPE,
    LocalFileFetcher,
    LocalSnapshotError,
)
from poe2_rag.ingestion.application import RawDocument, SourceFetcher

CAPTURED_AT = 1_793_000_000  # 2026-10-26T17:33:20Z, an arbitrary fixed snapshot mtime


def write_snapshot(tmp_path: Path, content: str = "<html><h1>0.1.0 Patch Notes</h1></html>") -> Path:
    path = tmp_path / "patch-notes-0.1.0.html"
    path.write_text(content, encoding="utf-8")
    os.utime(path, (CAPTURED_AT, CAPTURED_AT))
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

    def test_uses_the_snapshot_mtime_as_the_capture_time(self, tmp_path: Path) -> None:
        path = write_snapshot(tmp_path)

        raw = LocalFileFetcher().fetch(str(path))

        assert raw.fetched_at == datetime.fromtimestamp(CAPTURED_AT, tz=UTC)

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
