"""One real snapshot through the four ingestion components, not through fakes (S2, §5.2).

Every component is tested in isolation elsewhere; this test pins the composition: a local snapshot
with its capture metadata, the forum parser, the JSON repository and the patch catalog together must
turn a locator into persisted, catalog-approved chunks, and a second run must be a no-op.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)
from poe2_rag.ingestion.adapters.outbound.local_file_fetcher import (
    METADATA_SUFFIX,
    LocalFileFetcher,
)
from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import PatchNotesParser
from poe2_rag.ingestion.application import IngestSource
from poe2_rag.shared.domain import InMemoryPatchCatalog, PatchVersion

FIXTURES = Path(__file__).parents[2] / "fixtures" / "patch_notes"
CAPTURED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
THREAD_URL = "https://www.pathofexile.com/forum/view-thread/7654321"


def build_pipeline(
    tmp_path: Path,
) -> tuple[Path, JsonChunkRepository, IngestSource]:
    """Write a snapshot and its sidecar, then wire the real fetcher, parser, repository and catalog."""
    snapshot = tmp_path / "patch-notes-0.2.0.html"
    shutil.copyfile(FIXTURES / "full_020_thread.html", snapshot)
    snapshot.with_name(snapshot.name + METADATA_SUFFIX).write_text(
        json.dumps({"captured_at": CAPTURED_AT.isoformat()}), encoding="utf-8"
    )
    repository = JsonChunkRepository(tmp_path / "processed")
    version = PatchVersion.parse("0.2.0")
    ingest = IngestSource(
        fetcher=LocalFileFetcher(),
        parser=PatchNotesParser(),
        repository=repository,
        catalog=InMemoryPatchCatalog([version], version),
    )
    return snapshot, repository, ingest


def test_one_snapshot_becomes_persisted_catalog_approved_chunks(tmp_path: Path) -> None:
    snapshot, repository, ingest = build_pipeline(tmp_path)

    report = ingest.execute(str(snapshot))

    stored = repository.all()
    assert [chunk.section for chunk in stored] == [
        "overview",
        "general-changes",
        "skill-changes",
    ]
    assert report.chunk_ids == tuple(chunk.chunk_id for chunk in stored)
    assert report.skipped == 0
    for chunk in stored:
        assert chunk.validity.valid_from == PatchVersion.parse("0.2.0")
        assert chunk.validity.valid_until is None
        assert chunk.source.url == THREAD_URL
        assert chunk.source.captured_at == CAPTURED_AT.date()


def test_a_second_run_writes_nothing(tmp_path: Path) -> None:
    snapshot, repository, ingest = build_pipeline(tmp_path)
    first = ingest.execute(str(snapshot))
    before = repository.path.read_bytes()

    second = ingest.execute(str(snapshot))

    assert second.chunk_ids == first.chunk_ids
    assert second.skipped == len(first.chunk_ids)
    assert repository.path.read_bytes() == before
