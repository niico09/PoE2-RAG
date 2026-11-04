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
    MixedSnapshotVersionError,
    OutOfOrderSnapshotError,
    UnknownPatchError,
)
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    InMemoryPatchCatalog,
    PatchCatalog,
    PatchRelease,
    PatchVersion,
    SourceRef,
    Validity,
)

CATALOG = InMemoryPatchCatalog(
    [
        PatchRelease(PatchVersion.parse("0.1.0"), date(2024, 12, 6)),
        PatchRelease(PatchVersion.parse("0.1.1"), date(2025, 1, 17)),
    ],
    PatchVersion.parse("0.1.1"),
)
LOCATOR = "data/raw/patch-notes-0.1.0.html"
GEM_URL = "https://poe2db.tw/us/Spark"


def gem_chunk(
    section: str = "description",
    version: str = "0.1.0",
    *,
    slug: str = "spark",
    name: str = "Spark",
    text: str | None = None,
) -> Chunk:
    """One poe2db section of a stable entity, so two versions of it share one identity."""
    parsed = PatchVersion.parse(version)
    return Chunk(
        entity=EntityRef("active_gem", slug, name),
        validity=Validity(parsed),
        section=section,
        text=f"{name} {section} at {parsed}." if text is None else text,
        source=SourceRef("poe2db", GEM_URL, date(2026, 11, 1)),
    )


def closed_at(chunk: Chunk, version: str) -> Chunk:
    """Return `chunk` with its open range closed at `version`, the copy supersession must store."""
    return replace(chunk, validity=Validity(chunk.validity.valid_from, PatchVersion.parse(version)))


def build_source(
    chunks: Sequence[Chunk] = (),
    stored: Sequence[Chunk] = (),
    *,
    catalog: PatchCatalog = CATALOG,
) -> tuple[IngestSource, CallRecorder, FakeChunkRepository]:
    """Wire the use case over fakes, sharing one journal so the whole order is observable."""
    recorder = CallRecorder()
    repository = FakeChunkRepository(recorder, stored)
    ingest = IngestSource(
        fetcher=FakeSourceFetcher(recorder),
        parser=FakeDocumentParser(recorder, chunks),
        repository=repository,
        catalog=catalog,
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

class TestSupersession:
    def test_a_first_snapshot_closes_and_removes_nothing(self) -> None:
        chunk = gem_chunk()
        ingest, recorder, repository = build_source([chunk])

        report = ingest.execute(LOCATOR)

        assert (report.closed, report.removed) == (0, 0)
        assert repository.all() == (chunk,)
        assert "remove" not in recorder.kinds()

    def test_a_newer_snapshot_closes_the_previous_open_range(self) -> None:
        previous = gem_chunk("description", "0.1.0")
        fresh = gem_chunk("description", "0.1.1")
        ingest, recorder, repository = build_source([fresh], stored=[previous])

        report = ingest.execute(LOCATOR)

        closed = closed_at(previous, "0.1.1")
        assert (report.closed, report.removed) == (1, 0)
        assert closed.chunk_id == previous.chunk_id
        assert recorder.arguments("save") == [((fresh, closed),)]
        assert repository.all() == (closed, fresh)

    def test_the_same_snapshot_again_is_a_noop(self) -> None:
        previous = gem_chunk("description", "0.1.0")
        current = gem_chunk("description", "0.1.1")
        ingest, recorder, repository = build_source([current], stored=[previous])
        ingest.execute(LOCATOR)
        recorder.calls.clear()

        report = ingest.execute(LOCATOR)

        assert (report.closed, report.removed, report.skipped) == (0, 0, 1)
        assert "save" not in recorder.kinds()
        assert "remove" not in recorder.kinds()
        assert repository.all() == (closed_at(previous, "0.1.1"), current)

    def test_a_section_removed_at_the_same_version_is_removed(self) -> None:
        description = gem_chunk("description", "0.1.1")
        levels = gem_chunk("levels", "0.1.1")
        ingest, recorder, repository = build_source([description], stored=[description, levels])

        report = ingest.execute(LOCATOR)

        assert report.removed == 1
        assert recorder.arguments("remove") == [((levels.chunk_id,),)]
        assert "save" not in recorder.kinds()
        assert repository.all() == (description,)

    def test_a_reingest_replaces_the_single_levels_chunk_with_the_row_blocks(self) -> None:
        """The block ids are new snapshot content, so the old single section cannot survive it (S5)."""
        description = gem_chunk("description", "0.1.1")
        old_levels = gem_chunk("levels", "0.1.1")
        blocks = (gem_chunk("levels-01-10", "0.1.1"), gem_chunk("levels-11-20", "0.1.1"))
        ingest, recorder, repository = build_source(
            [description, *blocks], stored=[description, old_levels]
        )

        report = ingest.execute(LOCATOR)

        assert (report.closed, report.removed, report.skipped) == (0, 1, 1)
        assert recorder.arguments("save") == [(tuple(blocks),)]
        assert recorder.arguments("remove") == [((old_levels.chunk_id,),)]
        assert repository.all() == (description, *blocks)

    def test_an_older_snapshot_is_rejected_and_writes_nothing(self) -> None:
        stored = gem_chunk("description", "0.1.1")
        older = gem_chunk("description", "0.1.0")
        ingest, recorder, repository = build_source([older], stored=[stored])

        with pytest.raises(OutOfOrderSnapshotError) as exc_info:
            ingest.execute(LOCATOR)

        message = str(exc_info.value)
        assert "0.1.0" in message
        assert "0.1.1" in message
        assert recorder.kinds() == ["fetch", "parse", "all"]
        assert repository.all() == (stored,)

    def test_an_older_snapshot_cannot_overlap_a_range_that_reaches_past_it(self) -> None:
        """A closed `[0.1.0, 0.2.0)` proves the entity was captured at 0.2.0 already."""
        catalog = InMemoryPatchCatalog(
            [
                PatchRelease(PatchVersion.parse("0.1.0"), date(2024, 12, 6)),
                PatchRelease(PatchVersion.parse("0.1.1"), date(2025, 1, 17)),
                PatchRelease(PatchVersion.parse("0.2.0"), date(2025, 3, 1)),
            ],
            PatchVersion.parse("0.2.0"),
        )
        stored = closed_at(gem_chunk("description", "0.1.0"), "0.2.0")
        older = gem_chunk("description", "0.1.1")
        ingest, recorder, repository = build_source([older], stored=[stored], catalog=catalog)

        with pytest.raises(OutOfOrderSnapshotError):
            ingest.execute(LOCATOR)

        assert "save" not in recorder.kinds()
        assert repository.all() == (stored,)

    def test_two_entities_from_one_document_are_both_superseded(self) -> None:
        old_spark = gem_chunk("description", "0.1.0")
        old_cascade = gem_chunk("description", "0.1.0", slug="glacial-cascade", name="Glacial Cascade")
        new_spark = gem_chunk("description", "0.1.1")
        new_cascade = gem_chunk(
            "description", "0.1.1", slug="glacial-cascade", name="Glacial Cascade"
        )
        ingest, _, repository = build_source(
            [new_spark, new_cascade], stored=[old_spark, old_cascade]
        )

        report = ingest.execute(LOCATOR)

        assert (report.closed, report.removed) == (2, 0)
        stored = repository.all()
        assert closed_at(old_spark, "0.1.1") in stored
        assert closed_at(old_cascade, "0.1.1") in stored
        assert len(stored) == 4

    def test_an_entity_the_document_does_not_mention_is_untouched(self) -> None:
        other = gem_chunk("description", "0.1.0", slug="ice-nova", name="Ice Nova")
        previous = gem_chunk("description", "0.1.0")
        ingest, _, repository = build_source(
            [gem_chunk("description", "0.1.1")], stored=[previous, other]
        )

        ingest.execute(LOCATOR)

        assert other in repository.all()
        assert other.validity.valid_until is None

    def test_patch_notes_of_consecutive_versions_do_not_close_each_other(self) -> None:
        older = make_chunk("general-changes", "0.1.0")
        newer = make_chunk("general-changes", "0.1.1")
        ingest, recorder, repository = build_source([newer], stored=[older])

        report = ingest.execute(LOCATOR)

        assert (report.closed, report.removed) == (0, 0)
        assert "remove" not in recorder.kinds()
        assert repository.all() == (older, newer)
        assert repository.all()[0].validity.valid_until is None

    def test_one_document_cannot_hold_two_versions_of_one_entity(self) -> None:
        first = gem_chunk("description", "0.1.0")
        second = gem_chunk("levels", "0.1.1")
        ingest, recorder, repository = build_source([first, second])

        with pytest.raises(MixedSnapshotVersionError) as exc_info:
            ingest.execute(LOCATOR)

        assert "active_gem:spark" in str(exc_info.value)
        assert "save" not in recorder.kinds()
        assert repository.all() == ()

    def test_the_snapshot_version_is_the_parsed_one_not_the_catalog_current(self) -> None:
        previous = gem_chunk("description", "0.1.0")
        ingest, _, repository = build_source([gem_chunk("description", "0.1.0")], stored=[previous])

        report = ingest.execute(LOCATOR)

        assert report.skipped == 1
        assert report.closed == 0
        assert repository.all() == (previous,)
        assert repository.all()[0].validity.valid_until is None


class TestValidityInvariant:
    def test_exactly_one_version_of_the_entity_is_valid_at_every_known_patch(self) -> None:
        sections = ("description", "levels")
        first = tuple(gem_chunk(section, "0.1.0") for section in sections)
        second = tuple(gem_chunk(section, "0.1.1") for section in sections)
        ingest, _, repository = build_source(second, stored=first)

        report = ingest.execute(LOCATOR)

        assert (report.closed, report.removed) == (2, 0)
        corpus = tuple(
            chunk for chunk in repository.all() if chunk.entity.key == "active_gem:spark"
        )
        assert len(corpus) == 4
        for version in CATALOG.known():
            for section in sections:
                valid = [
                    chunk
                    for chunk in corpus
                    if chunk.section == section and chunk.is_valid_at(version)
                ]
                assert len(valid) == 1
                assert valid[0].validity.valid_from == version
