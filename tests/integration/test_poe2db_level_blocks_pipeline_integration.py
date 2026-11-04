"""A 40-row gem page through the real ingest and reindex: nothing is reported truncated (S4).

Marked `integration`: it runs the real `bge-base-en-v1.5` embeddings, a real Chroma collection and a
real BM25 file under `tmp_path`. The HTTP adapter is real and its transport is the only fake:
`httpx2.MockTransport` serves robots.txt and the generated page, so the run never touches the network.
The page comes from `poe2db_levels` because the committed fixture is a two-row excerpt (ADR-0002) and a
one-block page would report truncation for a reason no real capture shows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cli_e2e import client_serving, integration_config
from poe2db_levels import (
    BLOCKS,
    ROW_COUNT,
    level_blocks,
    rendered_rows,
    spark_level_page,
)

from poe2_rag.bootstrap import Container
from poe2_rag.cli import main
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)

pytestmark = pytest.mark.integration

URL = "https://poe2db.tw/us/Spark"


def test_a_full_level_table_is_stored_as_blocks_and_nothing_is_truncated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = integration_config(tmp_path)
    container = Container(config, environ={}, http_client=client_serving({URL: spark_level_page()}))

    assert main(["ingest", "poe2db", URL], container=container) == 0
    ingested = capsys.readouterr()

    assert "error: " not in ingested.err
    corpus = list(JsonChunkRepository(config.processed_dir).all())
    blocks = level_blocks(tuple(chunk for chunk in corpus if chunk.entity.slug == "spark"))
    assert [block.section for block in blocks] == BLOCKS

    rows = [line for block in blocks for line in block.text.splitlines()[2:]]
    assert rows == rendered_rows(ROW_COUNT)

    assert "Truncated chunk ids" not in ingested.out
    assert container.reindex_corpus.execute().truncated_chunk_ids == ()


def test_reingesting_the_same_page_keeps_one_set_of_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = integration_config(tmp_path)
    container = Container(config, environ={}, http_client=client_serving({URL: spark_level_page()}))

    assert main(["ingest", "poe2db", URL], container=container) == 0
    capsys.readouterr()
    assert main(["ingest", "poe2db", URL], container=container) == 0
    again = capsys.readouterr()

    assert "0 removed" in again.out
    assert "Truncated chunk ids" not in again.out
    blocks = level_blocks(
        tuple(
            chunk
            for chunk in JsonChunkRepository(config.processed_dir).all()
            if chunk.entity.slug == "spark"
        )
    )
    assert [block.section for block in blocks] == BLOCKS
