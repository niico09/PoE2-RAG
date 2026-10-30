"""The ingestion use case orchestrated over recording fakes: fetch, parse, validate, persist."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date

import pytest
from ingestion_fakes import (
    CallRecorder,
    FakeChunkRepository,
    FakeDocumentParser,
    FakeSourceFetcher,
    make_chunk,
    make_raw,
)

from poe2_rag.ingestion.application import (
    ChunkIdentityConflictError,
    DuplicateChunkIdError,
    IngestReport,
    IngestSource,
    UnknownPatchError,
)
from poe2_rag.shared.domain import Chunk, InMemoryPatchCatalog, PatchVersion, SourceRef

CATALOG = InMemoryPatchCatalog(
    [PatchVersion.parse("0.1.0"), PatchVersion.parse("0.1.1")],
    PatchVersion.parse("0.1.1"),
)
LOCATOR = "data/raw/patch-notes-0.1.0.html"


def build_source(
    chunks: Sequence[Chunk] = (), stored: Sequence[Chunk] = ()
) -> tuple[IngestSource, CallRecorder, FakeChunkRepository]:
    """Wire the use case over fakes, sharing one journal so the whole order is observable."""
    recorder = CallRecorder()
    repository = FakeChunkRepository(recorder, stored)
    ingest = IngestSource(
        fetcher=FakeSourceFetcher(recorder),
        parser=FakeDocumentParser(recorder, chunks),
        repository=repository,
        catalog=CATALOG,
    )
    return ingest, recorder, repository


class TestHappyPath:
    def test_fetches_the_requested_locator(self) -> None:
        ingest, recorder, _ = build_source([make_chunk()])

        ingest.execute(LOCATOR)

        assert recorder.arguments("fetch") == [(LOCATOR,)]

    def test_parses_the_document_the_fetcher_returned(self) -> None:
        ingest, recorder, _ = build_source([make_chunk()])

        ingest.execute(LOCATOR)

        assert recorder.arguments("parse") == [(make_raw(locator=LOCATOR),)]

    def test_persists_every_chunk_of_a_new_document(self) -> None:
        chunks = [make_chunk("general-changes"), make_chunk("skill-changes")]
        ingest, _, repository = build_source(chunks)

        ingest.execute(LOCATOR)

        assert repository.all() == tuple(chunks)

    def test_reports_the_document_ids_in_order(self) -> None:
        chunks = [make_chunk("general-changes"), make_chunk("skill-changes")]
        ingest, _, _ = build_source(chunks)

        report = ingest.execute(LOCATOR)

        assert report == IngestReport(
            locator=LOCATOR,
            chunk_ids=(chunks[0].chunk_id, chunks[1].chunk_id),
            skipped=0,
        )

    def test_reads_the_repository_before_writing(self) -> None:
        ingest, recorder, _ = build_source([make_chunk()])

        ingest.execute(LOCATOR)

        assert recorder.kinds() == ["fetch", "parse", "all", "save"]

    def test_writes_new_chunks_only(self) -> None:
        chunk = make_chunk()
        ingest, recorder, _ = build_source([chunk])

        ingest.execute(LOCATOR)

        assert recorder.arguments("save") == [((chunk,),)]


class TestSkipsUnchangedChunks:
    def test_a_second_run_writes_nothing(self) -> None:
        chunk = make_chunk()
        ingest, recorder, repository = build_source([chunk], stored=[chunk])

        report = ingest.execute(LOCATOR)

        assert report.chunk_ids == (chunk.chunk_id,)
        assert report.skipped == 1
        assert "save" not in recorder.kinds()
        assert repository.all() == (chunk,)

    def test_saves_only_the_chunks_that_changed(self) -> None:
        unchanged = make_chunk("general-changes")
        stale = make_chunk("skill-changes", text="Old text.")
        fresh = make_chunk("skill-changes", text="New text.")
        ingest, recorder, repository = build_source([unchanged, fresh], stored=[unchanged, stale])

        report = ingest.execute(LOCATOR)

        assert report.skipped == 1
        assert recorder.arguments("save") == [((fresh,),)]
        assert repository.all() == (unchanged, fresh)

    def test_updates_a_chunk_whose_text_changed(self) -> None:
        stale = make_chunk("general-changes", text="Old text.")
        fresh = make_chunk("general-changes", text="New text.")
        ingest, _, repository = build_source([fresh], stored=[stale])

        report = ingest.execute(LOCATOR)

        assert report.skipped == 0
        assert repository.all() == (fresh,)


class TestChunkIdentity:
    def test_rejects_a_chunk_id_claimed_by_another_source(self) -> None:
        stored = make_chunk()
        incoming = make_chunk(
            text="Different thread.",
            url="https://www.pathofexile.com/forum/view-thread/9999999",
        )
        ingest, recorder, _ = build_source([incoming], stored=[stored])

        with pytest.raises(ChunkIdentityConflictError) as exc_info:
            ingest.execute(LOCATOR)

        message = str(exc_info.value)
        assert stored.source.url in message
        assert incoming.source.url in message
        assert "save" not in recorder.kinds()

    def test_allows_a_recapture_of_the_same_thread(self) -> None:
        stored = make_chunk()
        recaptured = replace(
            stored, source=SourceRef("patch_notes", stored.source.url, date(2026, 10, 30))
        )
        ingest, _, repository = build_source([recaptured], stored=[stored])

        report = ingest.execute(LOCATOR)

        assert report.skipped == 0
        assert repository.all() == (recaptured,)


class TestValidation:
    def test_rejects_a_chunk_at_an_unknown_patch(self) -> None:
        unknown = make_chunk("general-changes", "0.2.0")
        ingest, recorder, _ = build_source([unknown])

        with pytest.raises(UnknownPatchError) as exc_info:
            ingest.execute(LOCATOR)

        message = str(exc_info.value)
        assert unknown.chunk_id in message
        assert "0.2.0" in message
        assert "save" not in recorder.kinds()

    def test_rejects_duplicate_chunk_ids_in_one_document(self) -> None:
        first = make_chunk("general-changes", text="First.")
        second = make_chunk("general-changes", text="Second.")
        ingest, recorder, _ = build_source([first, second])

        with pytest.raises(DuplicateChunkIdError) as exc_info:
            ingest.execute(LOCATOR)

        assert first.chunk_id in str(exc_info.value)
        assert "save" not in recorder.kinds()

    def test_checks_patches_before_duplicates(self) -> None:
        unknown = make_chunk("general-changes", "0.2.0")
        first = make_chunk("skill-changes", text="First.")
        second = make_chunk("skill-changes", text="Second.")
        ingest, _, _ = build_source([unknown, first, second])

        with pytest.raises(UnknownPatchError):
            ingest.execute(LOCATOR)

    def test_an_empty_document_writes_nothing(self) -> None:
        ingest, recorder, _ = build_source([])

        report = ingest.execute(LOCATOR)

        assert report == IngestReport(locator=LOCATOR, chunk_ids=(), skipped=0)
        assert recorder.kinds() == ["fetch", "parse"]


class TestFailures:
    def test_a_fetch_failure_stops_the_run(self) -> None:
        recorder = CallRecorder()
        failure = RuntimeError("snapshot missing")
        ingest = IngestSource(
            fetcher=FakeSourceFetcher(recorder, error=failure),
            parser=FakeDocumentParser(recorder),
            repository=FakeChunkRepository(recorder),
            catalog=CATALOG,
        )

        with pytest.raises(RuntimeError, match="snapshot missing"):
            ingest.execute(LOCATOR)

        assert recorder.kinds() == ["fetch"]


class TestErrors:
    def test_unknown_patch_is_an_ingestion_error(self) -> None:
        from poe2_rag.ingestion.application import IngestionError

        assert issubclass(UnknownPatchError, IngestionError)

    def test_duplicate_chunk_id_is_an_ingestion_error(self) -> None:
        from poe2_rag.ingestion.application import IngestionError

        assert issubclass(DuplicateChunkIdError, IngestionError)

    def test_chunk_identity_conflict_is_an_ingestion_error(self) -> None:
        from poe2_rag.ingestion.application import IngestionError

        assert issubclass(ChunkIdentityConflictError, IngestionError)
