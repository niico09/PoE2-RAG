"""The reindex use case over fakes: read the corpus, rebuild the lexical index, report the run."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from ingestion_fakes import (
    CallRecorder,
    FakeChunkRepository,
    FakeLexicalIndexWriter,
    make_chunk,
)

from poe2_rag.ingestion.application import (
    EmptyCorpusError,
    IngestionError,
    LexicalIndexWriter,
    ReindexCorpus,
    ReindexReport,
)
from poe2_rag.shared.domain import Chunk

REBUILT_AT = datetime(2026, 10, 30, 9, 30, tzinfo=UTC)


def make_reindex(
    chunks: Sequence[Chunk] = (), error: Exception | None = None
) -> tuple[ReindexCorpus, CallRecorder, FakeLexicalIndexWriter]:
    """Build the use case with recording fakes and a pinned clock."""
    recorder = CallRecorder()
    writer = FakeLexicalIndexWriter(recorder, error=error)
    reindex = ReindexCorpus(
        repository=FakeChunkRepository(recorder, chunks),
        lexical_writer=writer,
        clock=lambda: REBUILT_AT,
    )
    return reindex, recorder, writer


class TestReport:
    def test_counts_every_chunk_the_corpus_holds(self) -> None:
        reindex, _, _ = make_reindex(
            (make_chunk("general-changes"), make_chunk("skill-changes"))
        )

        assert reindex.execute().chunk_count == 2

    def test_reports_the_moment_the_clock_gives(self) -> None:
        reindex, _, _ = make_reindex((make_chunk(),))

        assert reindex.execute().rebuilt_at == REBUILT_AT

    def test_defaults_to_the_current_utc_moment(self) -> None:
        recorder = CallRecorder()
        reindex = ReindexCorpus(
            FakeChunkRepository(recorder, (make_chunk(),)),
            FakeLexicalIndexWriter(recorder),
        )

        before = datetime.now(UTC)
        rebuilt_at = reindex.execute().rebuilt_at
        after = datetime.now(UTC)

        assert rebuilt_at.tzinfo is UTC
        assert before <= rebuilt_at <= after

    def test_is_frozen(self) -> None:
        report = ReindexReport(1, REBUILT_AT)

        with pytest.raises(FrozenInstanceError):
            report.chunk_count = 2  # type: ignore[misc]


class TestRebuild:
    def test_reads_the_corpus_before_it_rebuilds(self) -> None:
        reindex, recorder, _ = make_reindex((make_chunk(),))

        reindex.execute()

        assert recorder.kinds() == ["all", "rebuild"]

    def test_rebuilds_once_with_every_chunk_in_corpus_order(self) -> None:
        chunks = (
            make_chunk("general-changes"),
            make_chunk("skill-changes"),
            make_chunk("bug-fixes"),
        )
        reindex, recorder, writer = make_reindex(chunks)

        reindex.execute()

        assert len(recorder.arguments("rebuild")) == 1
        (rebuilt,) = recorder.arguments("rebuild")
        assert rebuilt == (chunks,)
        assert writer.chunks == chunks

    def test_hands_the_writer_an_immutable_snapshot(self) -> None:
        reindex, recorder, _ = make_reindex((make_chunk(),))

        reindex.execute()

        (rebuilt,) = recorder.arguments("rebuild")
        assert isinstance(rebuilt[0], tuple)

    def test_an_empty_corpus_raises_and_never_calls_the_writer(self) -> None:
        """An index rebuilt from nothing would answer "no information" for every question (D1)."""
        reindex, recorder, _ = make_reindex(())

        with pytest.raises(EmptyCorpusError):
            reindex.execute()

        assert recorder.kinds() == ["all"]

    def test_a_failing_writer_stops_the_run(self) -> None:
        reindex, recorder, _ = make_reindex(
            (make_chunk(),), error=RuntimeError("index directory is read-only")
        )

        with pytest.raises(RuntimeError, match="read-only"):
            reindex.execute()

        assert recorder.kinds() == ["all", "rebuild"]


class TestPorts:
    def test_the_writer_fake_satisfies_the_write_port(self) -> None:
        assert isinstance(FakeLexicalIndexWriter(CallRecorder()), LexicalIndexWriter)


class TestErrors:
    def test_empty_corpus_is_an_ingestion_error(self) -> None:
        assert issubclass(EmptyCorpusError, IngestionError)
