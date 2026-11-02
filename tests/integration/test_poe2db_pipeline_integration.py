"""One trimmed poe2db gem page through the real write and read halves (S13).

Marked `integration`: it loads `bge-base-en-v1.5` and `bge-reranker-base` and writes real indexes under
`tmp_path`. The HTTP adapter is real but its transport is the only fake: `httpx2.MockTransport` serves
robots.txt and the committed fixture, so the run never touches the network while still exercising the
cache, the parser, the JSON corpus, the catalog, `ReindexCorpus` and `AnswerQuestion`.

The point is the wiring, not the retrieval quality: `ingest poe2db` writes the corpus and both indexes
through one container, and `ask` reads them back through a second, freshly built container with a
scripted language model.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from cli_e2e import QUESTION, FirstChunkCitingLanguageModel, build_config

from poe2_rag.bootstrap import Container
from poe2_rag.cli import main
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)

pytestmark = pytest.mark.integration

URL = "https://poe2db.tw/us/Spark"
ROBOTS_URL = "https://poe2db.tw/robots.txt"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "poe2db"


def _offline_client() -> httpx2.Client:
    """Serve robots.txt and the trimmed Spark page without any network access."""
    page = (FIXTURES / "active_gem_spark.html").read_text(encoding="utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == ROBOTS_URL:
            return httpx2.Response(200, text="User-agent: *\nAllow: /")
        if url == URL:
            return httpx2.Response(200, text=page)
        raise AssertionError(f"unexpected request to {url}")

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_ingest_poe2db_then_ask_reads_the_same_indexes_from_disk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = replace(
        build_config(tmp_path),
        http_contact="integration@example.invalid",
        http_request_interval=0.001,
    )

    writer = Container(config, environ={}, http_client=_offline_client())
    assert main(["ingest", "poe2db", URL], container=writer) == 0
    ingested = capsys.readouterr()

    assert f"Ingested poe2db from {URL}" in ingested.out
    assert "Reindexed" in ingested.out
    assert "error: " not in ingested.err

    corpus = JsonChunkRepository(config.processed_dir).all()
    spark_chunks = [chunk for chunk in corpus if chunk.entity.slug == "spark"]
    assert {chunk.section for chunk in spark_chunks} == {
        "description",
        "properties",
        "explicit-modifiers",
        "levels",
    }
    for chunk in spark_chunks:
        assert chunk.source.url == URL

    model = FirstChunkCitingLanguageModel()
    reader = Container(config, environ={}, language_model=model)
    assert main(["ask", QUESTION], container=reader) == 0
    asked = capsys.readouterr()

    question, delivered = model.calls[0]
    assert question == QUESTION
    assert delivered, "retrieval delivered nothing, so the indexes were not read back"
    cited = next(chunk for chunk in corpus if chunk.chunk_id == delivered[0])
    quote = next(line for line in cited.text.splitlines() if line.strip())

    assert "Patch: 0.1.1" in asked.out
    assert f"1. {cited.chunk_id}" in asked.out
    assert quote in asked.out
    assert cited.source.url in asked.out
    assert "error: " not in asked.err
