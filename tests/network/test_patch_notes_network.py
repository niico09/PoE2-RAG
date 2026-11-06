"""Live fetch of one official patch-notes thread (S6).

Marked `network`: it performs a real HTTP request, so the default suite excludes it. Run it at most
once manually with a real contact and report the result; never in CI:

    POE2_RAG_HTTP_CONTACT="<your contact>" \
        uv run pytest -q -m network tests/network --basetemp=/tmp/patch-notes-capture

The page it downloads is a full forum thread: it stays under `tmp_path` and is never committed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx2
import pytest

from poe2_rag.bootstrap import FORUM_POLICY
from poe2_rag.config import HTTP_CONTACT_VARIABLE
from poe2_rag.ingestion.adapters.outbound.http import PoliteHttpFetcher
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)
from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import PatchNotesParser
from poe2_rag.ingestion.application import IngestSource
from poe2_rag.shared.adapters.outbound.toml_patch_catalog import TomlPatchCatalog
from poe2_rag.shared.domain import PatchVersion

pytestmark = pytest.mark.network

URL = "https://www.pathofexile.com/forum/view-thread/4009785"
"""The 0.5.5e thread: the first ingest that was done by hand before this feature."""

VERSION = "0.5.5e"
CACHE_NAME = "www.pathofexile.com-forum-view-thread-4009785.html"
CATALOG = Path(__file__).parents[2] / "config" / "patches.toml"
REPORTED_SECTIONS = ["notes"]
"""The chunk list the manual run of 2026-11-05 observed.

The staff post of this thread is a hotfix body: an `h3` repeating the title and five bullet lines,
with no section heading under it, so the parser produces exactly one `notes` chunk. A change here
means the live thread (or the parser's section split) moved, which is what this test is for.
"""


def test_one_real_thread_ingest_keeps_everything_under_tmp_path(tmp_path: Path) -> None:
    """Fetch 0.5.5e once through the real pipeline and ingest it into a throwaway corpus."""
    contact = (os.environ.get(HTTP_CONTACT_VARIABLE) or "").strip()
    if not contact:
        pytest.skip(
            f"{HTTP_CONTACT_VARIABLE} is not set; the live fetch requires a real contact so the "
            f"User-Agent identifies the operator"
        )

    catalog = TomlPatchCatalog(CATALOG)
    repository = JsonChunkRepository(tmp_path / "processed")
    with httpx2.Client(timeout=60.0) as client:
        pipeline = IngestSource(
            fetcher=PoliteHttpFetcher(
                policy=FORUM_POLICY,
                client=client,
                cache_dir=tmp_path,
                contact=contact,
                request_interval=1.0,
                timeout=60.0,
            ),
            parser=PatchNotesParser(),
            repository=repository,
            catalog=catalog,
        )
        report = pipeline.execute(URL)

    chunks = repository.all()
    stored_version = PatchVersion.parse(VERSION)
    assert report.skipped == 0
    assert report.chunk_ids == tuple(chunk.chunk_id for chunk in chunks)
    assert chunks
    assert {chunk.validity.valid_from for chunk in chunks} == {stored_version}
    assert {chunk.source.type for chunk in chunks} == {"patch_notes"}
    assert {chunk.source.url for chunk in chunks} == {URL}
    assert [chunk.section for chunk in chunks] == REPORTED_SECTIONS
    assert [chunk.chunk_id for chunk in chunks] == [
        f"patch_notes:patch_notes:0-5-5@{VERSION}#{section}" for section in REPORTED_SECTIONS
    ]

    cache_directory = tmp_path / "patch_notes"
    html_path = cache_directory / CACHE_NAME
    meta = json.loads(
        (cache_directory / f"{CACHE_NAME}.meta.json").read_text(encoding="utf-8")
    )
    assert html_path.is_file()
    assert meta["url"] == URL
    assert meta["status"] == 200
    assert len(meta["sha256"]) == 64

    print(f"ingested {len(chunks)} chunks from {len(html_path.read_text(encoding='utf-8'))} chars")
