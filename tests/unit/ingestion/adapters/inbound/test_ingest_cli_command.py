"""Tests for the inbound CLI adapter that drives ingestion and reindexing (S3).

The command under test is a thin formatter: it calls the injected application runners in the
fixed order and prints their reports. These tests use real report values and fake runners that
record every call, so the assertions stay on observable CLI behavior rather than on business
outcomes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from poe2_rag.ingestion.adapters.inbound.cli.ingest import (
    SOURCES,
    run,
)
from poe2_rag.ingestion.application.ingest_source import IngestReport
from poe2_rag.ingestion.application.reindex_corpus import ReindexReport

if TYPE_CHECKING:
    from pytest import CaptureFixture


class FakeIngestRunner:
    """Records the locator it receives and returns a deterministic ingest report."""

    def __init__(self, report: IngestReport) -> None:
        self.report = report
        self.calls: list[str] = []

    def execute(self, locator: str) -> IngestReport:
        self.calls.append(f"ingest:{locator}")
        return self.report


class FakeReindexRunner:
    """Records every execution and returns a deterministic reindex report."""

    def __init__(self, report: ReindexReport) -> None:
        self.report = report
        self.calls: list[str] = []

    def execute(self) -> ReindexReport:
        self.calls.append("reindex")
        return self.report


@pytest.fixture
def rebuilt_at() -> datetime:
    return datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def locator() -> str:
    return "data/raw/patch-notes-0.2.0.html"


def test_successful_run_prints_locator_chunk_count_and_skipped_count(
    capsys: CaptureFixture[str],
    locator: str,
    rebuilt_at: datetime,
) -> None:
    ingest_report = IngestReport(
        locator=locator,
        chunk_ids=("chunk-1", "chunk-2", "chunk-3"),
        skipped=1,
    )
    reindex_report = ReindexReport(
        chunk_count=3,
        rebuilt_at=rebuilt_at,
        truncated_chunk_ids=(),
    )

    run(
        source="patch-notes",
        locator=locator,
        ingest=FakeIngestRunner(ingest_report),
        reindex=FakeReindexRunner(reindex_report),
    )

    captured = capsys.readouterr()
    assert f"{locator}: 3 chunks produced, 1 skipped, 0 closed, 0 removed" in captured.out


def test_successful_run_prints_reindexed_chunk_count_and_iso_rebuild_moment(
    capsys: CaptureFixture[str],
    locator: str,
    rebuilt_at: datetime,
) -> None:
    ingest_report = IngestReport(
        locator=locator,
        chunk_ids=("chunk-1", "chunk-2"),
        skipped=0,
    )
    reindex_report = ReindexReport(
        chunk_count=2,
        rebuilt_at=rebuilt_at,
        truncated_chunk_ids=(),
    )

    run(
        source="patch-notes",
        locator=locator,
        ingest=FakeIngestRunner(ingest_report),
        reindex=FakeReindexRunner(reindex_report),
    )

    captured = capsys.readouterr()
    assert "Reindexed 2 chunks" in captured.out
    assert "2026-10-30T12:00:00+00:00" in captured.out


def test_truncated_chunk_ids_are_named_when_non_empty(
    capsys: CaptureFixture[str],
    locator: str,
    rebuilt_at: datetime,
) -> None:
    ingest_report = IngestReport(
        locator=locator,
        chunk_ids=("chunk-a", "chunk-b"),
        skipped=0,
    )
    reindex_report = ReindexReport(
        chunk_count=2,
        rebuilt_at=rebuilt_at,
        truncated_chunk_ids=("chunk-b",),
    )

    run(
        source="patch-notes",
        locator=locator,
        ingest=FakeIngestRunner(ingest_report),
        reindex=FakeReindexRunner(reindex_report),
    )

    captured = capsys.readouterr()
    assert "chunk-b" in captured.out
    assert "truncated" in captured.out.lower()


def test_empty_truncated_chunk_ids_add_nothing_to_output(
    capsys: CaptureFixture[str],
    locator: str,
    rebuilt_at: datetime,
) -> None:
    ingest_report = IngestReport(
        locator=locator,
        chunk_ids=("chunk-1",),
        skipped=0,
    )
    reindex_report = ReindexReport(
        chunk_count=1,
        rebuilt_at=rebuilt_at,
        truncated_chunk_ids=(),
    )

    run(
        source="patch-notes",
        locator=locator,
        ingest=FakeIngestRunner(ingest_report),
        reindex=FakeReindexRunner(reindex_report),
    )

    captured = capsys.readouterr()
    assert "truncated" not in captured.out.lower()


def test_ingest_runs_before_reindex_with_exact_locator(
    locator: str,
    rebuilt_at: datetime,
) -> None:
    ingest_report = IngestReport(
        locator=locator,
        chunk_ids=("chunk-1",),
        skipped=0,
    )
    reindex_report = ReindexReport(
        chunk_count=1,
        rebuilt_at=rebuilt_at,
        truncated_chunk_ids=(),
    )
    ingest = FakeIngestRunner(ingest_report)
    reindex = FakeReindexRunner(reindex_report)

    run(
        source="patch-notes",
        locator=locator,
        ingest=ingest,
        reindex=reindex,
    )

    assert ingest.calls == [f"ingest:{locator}"]
    assert reindex.calls == ["reindex"]
    assert ingest.calls[0].endswith(locator)


def test_sources_are_the_closed_set_the_cli_accepts() -> None:
    assert SOURCES == ("patch-notes", "poe2db")


def test_unknown_source_raises_value_error_without_calling_runners(
    locator: str,
    rebuilt_at: datetime,
) -> None:
    ingest = FakeIngestRunner(
        IngestReport(locator=locator, chunk_ids=(), skipped=0)
    )
    reindex = FakeReindexRunner(
        ReindexReport(chunk_count=0, rebuilt_at=rebuilt_at, truncated_chunk_ids=())
    )

    with pytest.raises(ValueError, match="unknown-source"):
        run(
            source="unknown-source",
            locator=locator,
            ingest=ingest,
            reindex=reindex,
        )

    assert ingest.calls == []
    assert reindex.calls == []


def test_nothing_written_to_stderr_on_success(
    capsys: CaptureFixture[str],
    locator: str,
    rebuilt_at: datetime,
) -> None:
    ingest_report = IngestReport(
        locator=locator,
        chunk_ids=("chunk-1",),
        skipped=0,
    )
    reindex_report = ReindexReport(
        chunk_count=1,
        rebuilt_at=rebuilt_at,
        truncated_chunk_ids=(),
    )

    run(
        source="patch-notes",
        locator=locator,
        ingest=FakeIngestRunner(ingest_report),
        reindex=FakeReindexRunner(reindex_report),
    )

    captured = capsys.readouterr()
    assert captured.err == ""


def test_successful_run_prints_the_superseded_counts(
    capsys: CaptureFixture[str],
    locator: str,
    rebuilt_at: datetime,
) -> None:
    ingest_report = IngestReport(
        locator=locator,
        chunk_ids=("chunk-1", "chunk-2"),
        skipped=0,
        closed=3,
        removed=1,
    )
    reindex_report = ReindexReport(chunk_count=4, rebuilt_at=rebuilt_at, truncated_chunk_ids=())

    run(
        source="poe2db",
        locator=locator,
        ingest=FakeIngestRunner(ingest_report),
        reindex=FakeReindexRunner(reindex_report),
    )

    captured = capsys.readouterr()
    assert (
        f"{locator}: 2 chunks produced, 0 skipped, 3 closed, 1 removed" in captured.out
    )


def test_poe2db_run_names_the_source_and_rebuilds_after_it(
    capsys: CaptureFixture[str],
    rebuilt_at: datetime,
) -> None:
    """The dispatch table sends poe2db through the same two-step flow with its own report label."""
    locator = "https://poe2db.tw/us/Spark"
    ingest_report = IngestReport(locator=locator, chunk_ids=("chunk-1", "chunk-2"), skipped=0)
    reindex_report = ReindexReport(chunk_count=2, rebuilt_at=rebuilt_at, truncated_chunk_ids=())
    ingest = FakeIngestRunner(ingest_report)
    reindex = FakeReindexRunner(reindex_report)

    run(source="poe2db", locator=locator, ingest=ingest, reindex=reindex)

    captured = capsys.readouterr()
    assert f"Ingested poe2db from {locator}" in captured.out
    assert "2 chunks produced, 0 skipped, 0 closed, 0 removed" in captured.out
    assert "Reindexed 2 chunks" in captured.out
    assert ingest.calls == [f"ingest:{locator}"]
    assert reindex.calls == ["reindex"]
