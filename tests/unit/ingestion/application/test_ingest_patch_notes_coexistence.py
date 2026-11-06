"""A Content Update thread and its hotfix coexist, and a re-capture supersedes one of them (S6).

The parser is the real one and the fixtures are trimmed captures; only the ports are fakes, so the
test covers the whole application path. The entity key carries the full version (`0-5-5`, `0-5-5e`),
so a hotfix thread is its own delta and never closes the thread it was built on, while a second
capture of the same thread replaces what that thread used to say.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from ingestion_fakes import CallRecorder, FakeChunkRepository

from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import PatchNotesParser
from poe2_rag.ingestion.application import IngestSource, RawDocument
from poe2_rag.shared.domain import (
    Chunk,
    InMemoryPatchCatalog,
    PatchRelease,
    PatchVersion,
)

FIXTURES = Path(__file__).parents[3] / "fixtures" / "patch_notes"
CAPTURED_AT = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
RECAPTURED_AT = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
CATALOG = InMemoryPatchCatalog(
    [
        PatchRelease(PatchVersion.parse("0.5.4"), date(2026, 8, 20)),
        PatchRelease(PatchVersion.parse("0.5.5"), date(2026, 8, 27)),
        PatchRelease(PatchVersion.parse("0.5.5e"), date(2026, 10, 9)),
    ],
    PatchVersion.parse("0.5.5e"),
)


def document(name: str, fetched_at: datetime) -> RawDocument:
    """One trimmed capture, read from disk so the real parser sees the real markup."""
    return RawDocument(
        locator=name,
        content=(FIXTURES / name).read_text(encoding="utf-8"),
        fetched_at=fetched_at,
        source_type="patch_notes",
    )


class ScriptedFetcher:
    """Serves one raw document per run and records the locator it was asked to fetch."""

    def __init__(self, documents: Sequence[RawDocument]) -> None:
        self._documents = list(documents)
        self.locators: list[str] = []

    def fetch(self, locator: str) -> RawDocument:
        self.locators.append(locator)
        return self._documents.pop(0)


def build_source(
    documents: Sequence[RawDocument], stored: Sequence[Chunk] = ()
) -> tuple[IngestSource, FakeChunkRepository]:
    """Wire the real parser over in-memory fakes: the application path, nothing else replaced."""
    repository = FakeChunkRepository(CallRecorder(), stored)
    ingest = IngestSource(
        fetcher=ScriptedFetcher(documents),
        parser=PatchNotesParser(),
        repository=repository,
        catalog=CATALOG,
    )
    return ingest, repository


def test_a_hotfix_thread_and_the_content_update_thread_stay_open_together() -> None:
    ingest, repository = build_source(
        [
            document("content_update_thread.html", CAPTURED_AT),
            document("suffixed_version_e_thread.html", CAPTURED_AT),
        ]
    )

    ingest.execute("content_update_thread.html")
    ingest.execute("suffixed_version_e_thread.html")

    stored = repository.all()
    assert {chunk.entity.key for chunk in stored} == {"patch_notes:0-5-5", "patch_notes:0-5-5e"}
    assert all(chunk.validity.valid_until is None for chunk in stored)
    assert {chunk.section for chunk in stored if chunk.entity.slug == "0-5-5"} == {
        "the-forbidden-rites-event-league",
        "endgame-changes",
    }
    assert {chunk.section for chunk in stored if chunk.entity.slug == "0-5-5e"} == {
        "overview",
        "general-changes",
        "bug-fixes",
    }


def test_a_recapture_of_the_same_thread_supersedes_it() -> None:
    ingest, repository = build_source(
        [
            document("content_update_thread.html", CAPTURED_AT),
            document("content_update_thread_edited.html", RECAPTURED_AT),
        ]
    )

    ingest.execute("content_update_thread.html")
    ingest.execute("content_update_thread_edited.html")

    stored = repository.all()
    assert [chunk.section for chunk in stored] == ["the-forbidden-rites-event-league"]
    (kept,) = stored
    assert kept.entity.key == "patch_notes:0-5-5"
    assert kept.validity.valid_until is None
    assert kept.source.captured_at == RECAPTURED_AT.date()
