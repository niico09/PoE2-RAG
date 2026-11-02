"""A poe2db page captured under two patches supersedes itself and the indexes follow (§5.2, D1).

Marked `integration`: it loads `bge-base-en-v1.5` and writes a real BM25 file and a real Chroma
collection under `tmp_path`. The HTTP adapter is not involved: a scripted fetcher serves the same
committed Spark fixture with two capture instants, which is the fake clock this test needs. Everything
else is real — the parser, the JSON corpus, the catalog, `IngestSource` with its supersession rules,
`ReindexCorpus`, both indexes and their validity filters.

The point is the chain the unit suites can only pin in pieces: ingesting the newer snapshot closes the
older range in the corpus, a reindex rebuilds both indexes from that corpus, and no search at patch A
can reach a B chunk or the other way round.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from poe2_rag.answering.adapters.outbound.dense.bge_embedder import BgeEmbedder
from poe2_rag.answering.adapters.outbound.dense.chroma_index import ChromaVectorIndex
from poe2_rag.answering.adapters.outbound.lexical.bm25_index import Bm25Index
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)
from poe2_rag.ingestion.adapters.outbound.poe2db import Poe2dbGemPageParser
from poe2_rag.ingestion.application import IngestSource, RawDocument, ReindexCorpus
from poe2_rag.shared.domain import InMemoryPatchCatalog, PatchRelease, PatchVersion

pytestmark = pytest.mark.integration

URL = "https://poe2db.tw/us/Spark"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "poe2db"
PATCH_A = PatchVersion.parse("0.1.0")
PATCH_B = PatchVersion.parse("0.1.1")
RELEASED_ON = {
    PATCH_A: date(2024, 12, 6),
    PATCH_B: date(2025, 1, 17),
}
CAPTURED_UNDER_A = datetime(2024, 12, 10, 12, 0, tzinfo=UTC)
CAPTURED_UNDER_B = datetime(2025, 1, 20, 12, 0, tzinfo=UTC)
REBUILT_AT = datetime(2025, 2, 1, 12, 0, tzinfo=UTC)
QUERY = "Spark level mana cost"


class ScriptedFetcher:
    """Serve the fixture once per scripted capture instant, so the clock is a value, not a wait."""

    def __init__(self, page: str, captured: Sequence[datetime]) -> None:
        self._page = page
        self._captured = list(captured)

    def fetch(self, locator: str) -> RawDocument:
        return RawDocument(
            locator=locator,
            content=self._page,
            fetched_at=self._captured.pop(0),
            source_type="poe2db",
        )


@pytest.fixture(scope="module")
def embedder() -> BgeEmbedder:
    """The real model, loaded once for the whole module."""
    return BgeEmbedder()


@pytest.fixture
def catalog() -> InMemoryPatchCatalog:
    """Both patches with their release days, so the capture instant decides `valid_from`."""
    releases = [PatchRelease(version, day) for version, day in RELEASED_ON.items()]
    return InMemoryPatchCatalog(releases, PATCH_B)


def test_a_second_capture_supersedes_the_first_and_both_indexes_follow(
    tmp_path: Path, embedder: BgeEmbedder, catalog: InMemoryPatchCatalog
) -> None:
    page = (FIXTURES / "active_gem_spark.html").read_text(encoding="utf-8")
    repository = JsonChunkRepository(tmp_path / "processed")
    ingest = IngestSource(
        fetcher=ScriptedFetcher(page, [CAPTURED_UNDER_A, CAPTURED_UNDER_B]),
        parser=Poe2dbGemPageParser(catalog),
        repository=repository,
        catalog=catalog,
    )

    first = ingest.execute(URL)
    second = ingest.execute(URL)

    ids_a = set(first.chunk_ids)
    ids_b = set(second.chunk_ids)
    assert len(ids_a) == len(ids_b) == 4
    assert all(chunk_id.endswith("@0.1.0#description") or "@0.1.0#" in chunk_id for chunk_id in ids_a)
    assert all("@0.1.1#" in chunk_id for chunk_id in ids_b)
    assert (first.closed, first.removed) == (0, 0)
    assert (second.closed, second.removed) == (4, 0)

    corpus = repository.all()
    assert len(corpus) == 8
    stored_a = [chunk for chunk in corpus if chunk.chunk_id in ids_a]
    for chunk in stored_a:
        assert chunk.validity.valid_until == PATCH_B
        assert not chunk.is_valid_at(PATCH_B)
    assert all(
        chunk.validity.valid_until is None for chunk in corpus if chunk.chunk_id in ids_b
    )

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

    assert report.chunk_count == 8

    vector = embedder.embed(QUERY)
    for version, expected in ((PATCH_A, ids_a), (PATCH_B, ids_b)):
        lexical_hits = {hit.chunk.chunk_id for hit in lexical.search(QUERY, version, 10)}
        dense_hits = {hit.chunk.chunk_id for hit in dense.search(vector, version, 10)}

        assert lexical_hits, f"the lexical index returned nothing at {version}"
        assert lexical_hits <= expected, f"a chunk from another patch reached {version} through BM25"
        assert dense_hits == expected, f"the dense index did not return exactly the {version} chunks"

    assert lexical.search(QUERY, PATCH_A, 10)
    assert not ({hit.chunk.chunk_id for hit in lexical.search(QUERY, PATCH_A, 10)} & ids_b)
