"""The ingestion use case orchestrated over recording fakes: fetch, parse, validate, persist."""

from __future__ import annotations

from collections.abc import Sequence

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
    DuplicateChunkIdError,
    IngestReport,
    IngestSource,
    UnknownPatchError,
)
from poe2_rag.shared.domain import Chunk, InMemoryPatchCatalog, PatchVersion

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
    source = IngestSource(
        fetcher=FakeSourceFetcher(recorder),
        parser=FakeDocumentParser(recorder, chunks),
        repository=repository,
        catalog=CATALOG,
    )
    return source, recorder, repository


class TestHappyPath:
    def test_fetches_the_requested_locator(self) -> None:
        source, recorder, _ = build_source([make_chunk()])

        source.execute(LOCATOR)

        assert recorder.arguments("fetch") == [(LOCATOR,)]

    def test_parses_the_document_the_fetcher_returned(self) -> None:
        source, recorder, _ = build_source([make_chunk()])

        source.execute(LOCATOR)

        assert recorder.arguments("parse") == [(make_raw(locator=LOCATOR),)]

    def test_persists_every_chunk_of_a_new_document(self) -> None:
        chunks = [make_chunk("general-changes"), make_chunk("skill-changes")]
        source, _, repository = build_source(chunks)

        source.execute(LOCATOR)

        assert repository.all() == tuple(chunks)

    def test_reports_the_document_ids_in_order(self) -> None:
        chunks = [make_chunk("general-changes"), make_chunk("skill-changes")]
        source, _, _ = build_source(chunks)

        report = source.execute(LOCATOR)

        assert report == IngestReport(
            locator=LOCATOR,
            chunk_ids=(chunks[0].chunk_id, chunks[1].chunk_id),
            skipped=0,
        )

    def test_reads_the_repository_before_writing(self) -> None:
        source, recorder, _ = build_source([make_chunk()])

        source.execute(LOCATOR)

        assert recorder.kinds() == ["fetch", "parse", "all", "save"]

    def test_writes_new_chunks_only(self) -> None:
        chunk = make_chunk()
        source, recorder, _ = build_source([chunk])

        source.execute(LOCATOR)

        assert recorder.arguments("save") == [((chunk,),)]


class TestSkipsUnchangedChunks:
    def test_a_second_run_writes_nothing(self) -> None:
        chunk = make_chunk()
        source, recorder, repository = build_source([chunk], stored=[chunk])

        report = source.execute(LOCATOR)

        assert report.chunk_ids == (chunk.chunk_id,)
        assert report.skipped == 1
        assert "save" not in recorder.kinds()
        assert repository.all() == (chunk,)

    def test_saves_only_the_chunks_that_changed(self) -> None:
        unchanged = make_chunk("general-changes")
        stale = make_chunk("skill-changes", text="Old text.")
        fresh = make_chunk("skill-changes", text="New text.")
        source, recorder, repository = build_source([unchanged, fresh], stored=[unchanged, stale])

        report = source.execute(LOCATOR)

        assert report.skipped == 1
        assert recorder.arguments("save") == [((fresh,),)]
        assert repository.all() == (unchanged, fresh)

    def test_updates_a_chunk_whose_text_changed(self) -> None:
        stale = make_chunk("general-changes", text="Old text.")
        fresh = make_chunk("general-changes", text="New text.")
        source, _, repository = build_source([fresh], stored=[stale])

        report = source.execute(LOCATOR)

        assert report.skipped == 0
        assert repository.all() == (fresh,)


class TestValidation:
    def test_rejects_a_chunk_at_an_unknown_patch(self) -> None:
        unknown = make_chunk("general-changes", "0.2.0")
        source, recorder, _ = build_source([unknown])

        with pytest.raises(UnknownPatchError) as exc_info:
            source.execute(LOCATOR)

        message = str(exc_info.value)
        assert unknown.chunk_id in message
        assert "0.2.0" in message
        assert "save" not in recorder.kinds()

    def test_rejects_duplicate_chunk_ids_in_one_document(self) -> None:
        first = make_chunk("general-changes", text="First.")
        second = make_chunk("general-changes", text="Second.")
        source, recorder, _ = build_source([first, second])

        with pytest.raises(DuplicateChunkIdError) as exc_info:
            source.execute(LOCATOR)

        assert first.chunk_id in str(exc_info.value)
        assert "save" not in recorder.kinds()

    def test_checks_patches_before_duplicates(self) -> None:
        unknown = make_chunk("general-changes", "0.2.0")
        first = make_chunk("skill-changes", text="First.")
        second = make_chunk("skill-changes", text="Second.")
        source, _, _ = build_source([unknown, first, second])

        with pytest.raises(UnknownPatchError):
            source.execute(LOCATOR)

    def test_an_empty_document_writes_nothing(self) -> None:
        source, recorder, _ = build_source([])

        report = source.execute(LOCATOR)

        assert report == IngestReport(locator=LOCATOR, chunk_ids=(), skipped=0)
        assert recorder.kinds() == ["fetch", "parse"]


class TestFailures:
    def test_a_fetch_failure_stops_the_run(self) -> None:
        recorder = CallRecorder()
        failure = RuntimeError("snapshot missing")
        source = IngestSource(
            fetcher=FakeSourceFetcher(recorder, error=failure),
            parser=FakeDocumentParser(recorder),
            repository=FakeChunkRepository(recorder),
            catalog=CATALOG,
        )

        with pytest.raises(RuntimeError, match="snapshot missing"):
            source.execute(LOCATOR)

        assert recorder.kinds() == ["fetch"]


class TestErrors:
    def test_unknown_patch_is_an_ingestion_error(self) -> None:
        from poe2_rag.ingestion.application import IngestionError

        assert issubclass(UnknownPatchError, IngestionError)

    def test_duplicate_chunk_id_is_an_ingestion_error(self) -> None:
        from poe2_rag.ingestion.application import IngestionError

        assert issubclass(DuplicateChunkIdError, IngestionError)
