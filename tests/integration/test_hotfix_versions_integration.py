"""Two Path of Exile 2 hotfix captures supersede each other and the indexes follow (S7).

Marked `integration`: it loads `bge-base-en-v1.5` and writes a real BM25 file and a real Chroma
collection under `tmp_path`. The HTTP adapter is not involved: a scripted fetcher serves two
committed patch-notes fixtures with their own capture instants, which is the fake clock this test
needs. Everything else is real — the patch-notes parser, the JSON corpus, the catalog,
`IngestSource` with its supersession rules, `ReindexCorpus`, both indexes and their hotfix-aware
validity filters.

The point is the chain the unit suites can only pin in pieces: ingesting the newer hotfix closes the
older range in the corpus, a reindex rebuilds both indexes from that corpus, and no search at patch
0.5.5d can reach a 0.5.5e chunk or the other way round.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from poe2_rag.answering.adapters.outbound.dense.bge_embedder import BgeEmbedder
from poe2_rag.answering.adapters.outbound.dense.chroma_index import ChromaVectorIndex
from poe2_rag.answering.adapters.outbound.lexical.bm25_index import Bm25Index
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)
from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import PatchNotesParser
from poe2_rag.ingestion.application import IngestSource, RawDocument, ReindexCorpus
from poe2_rag.shared.domain import InMemoryPatchCatalog, PatchRelease, PatchVersion

pytestmark = pytest.mark.integration

URL_D = "https://www.pathofexile.com/forum/view-thread/4444444"
URL_E = "https://www.pathofexile.com/forum/view-thread/5555555"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "patch_notes"
PATCH_D = PatchVersion.parse("0.5.5d")
PATCH_E = PatchVersion.parse("0.5.5e")
RELEASED_ON = {
    PATCH_D: date(2026, 11, 2),
    PATCH_E: date(2026, 11, 4),
}
CAPTURED_UNDER_D = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
CAPTURED_UNDER_E = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
REBUILT_AT = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
QUERY = "Explosive Shot damage"


class ScriptedFetcher:
    """Serve each fixture once per scripted locator, so the clock is a value, not a wait."""

    def __init__(self, pages: Mapping[str, tuple[str, datetime]]) -> None:
        self._pages = dict(pages)

    def fetch(self, locator: str) -> RawDocument:
        content, fetched_at = self._pages[locator]
        return RawDocument(
            locator=locator,
            content=content,
            fetched_at=fetched_at,
            source_type="patch_notes",
        )


@pytest.fixture(scope="module")
def embedder() -> BgeEmbedder:
    """The real model, loaded once for the whole module."""
    return BgeEmbedder()


@pytest.fixture
def catalog() -> InMemoryPatchCatalog:
    """Both hotfixes with their release days, so the capture instant decides `valid_from`."""
    releases = [PatchRelease(version, day) for version, day in RELEASED_ON.items()]
    return InMemoryPatchCatalog(releases, PATCH_E)


def test_two_hotfix_captures_supersede_each_other_and_both_indexes_follow(
    tmp_path: Path, embedder: BgeEmbedder, catalog: InMemoryPatchCatalog
) -> None:
    page_d = (FIXTURES / "suffixed_version_d.html").read_text(encoding="utf-8")
    page_e = (FIXTURES / "suffixed_version.html").read_text(encoding="utf-8")
    repository = JsonChunkRepository(tmp_path / "processed")
    ingest_source = IngestSource(
        fetcher=ScriptedFetcher(
            {
                URL_D: (page_d, CAPTURED_UNDER_D),
                URL_E: (page_e, CAPTURED_UNDER_E),
            }
        ),
        parser=PatchNotesParser(),
        repository=repository,
        catalog=catalog,
    )

    first = ingest_source.execute(URL_D)
    second = ingest_source.execute(URL_E)

    ids_d = set(first.chunk_ids)
    ids_e = set(second.chunk_ids)
    assert len(ids_d) == len(ids_e) == 1
    assert all(chunk_id.endswith("@0.5.5d#notes") for chunk_id in ids_d)
    assert all(chunk_id.endswith("@0.5.5e#notes") for chunk_id in ids_e)
    assert (first.closed, first.removed) == (0, 0)
    assert (second.closed, second.removed) == (1, 0)

    corpus = repository.all()
    assert len(corpus) == 2
    stored_d = [chunk for chunk in corpus if chunk.chunk_id in ids_d]
    assert len(stored_d) == 1
    assert stored_d[0].validity.valid_until == PATCH_E
    assert not stored_d[0].is_valid_at(PATCH_E)

    lexical = Bm25Index(tmp_path / "index")
    dense = ChromaVectorIndex(tmp_path / "index" / "chroma", embedder=embedder)
    reindex = ReindexCorpus(
        repository,
        lexical,
        document_embedder=embedder,
        vector_writer=dense,
        clock=lambda: REBUILT_AT,
    )

    report = reindex.execute()

    assert report.chunk_count == 2

    vector = embedder.embed(QUERY)
    for version, expected in ((PATCH_D, ids_d), (PATCH_E, ids_e)):
        lexical_hits = {hit.chunk.chunk_id for hit in lexical.search(QUERY, version, 10)}
        dense_hits = {hit.chunk.chunk_id for hit in dense.search(vector, version, 10)}

        assert lexical_hits, f"the lexical index returned nothing at {version}"
        assert lexical_hits <= expected, f"a chunk from another patch reached {version} through BM25"
        assert dense_hits == expected, f"the dense index did not return exactly the {version} chunks"
