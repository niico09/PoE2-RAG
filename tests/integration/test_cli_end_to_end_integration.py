"""The CLI end to end over the real indexes, with only the language model simulated (S8).

Marked `integration`: it loads `bge-base-en-v1.5` and `bge-reranker-base`, so `addopts` excludes it and
it runs only with `uv run pytest -q -m integration`. What the default suite cannot prove is the wiring
between the two halves: `ingest` writes the corpus and both indexes through one container, and `ask`
reads them back through a **second, freshly built container** — the stand-in for a new process — so a
passing case means the artifacts on disk are the contract between `ingest` and `ask`, not shared
in-memory state.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cli_e2e import (
    PATCH,
    QUESTION,
    FirstChunkCitingLanguageModel,
    build_config,
    write_snapshot,
)

from poe2_rag.bootstrap import Container
from poe2_rag.cli import main
from poe2_rag.config import AppConfig
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)

pytestmark = pytest.mark.integration


def test_ingest_then_ask_reads_the_same_indexes_from_disk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One container writes, another one reads: the citation the user sees names a stored chunk."""
    config = build_config(tmp_path)
    snapshot = write_snapshot(tmp_path)

    writer = Container(config, environ={})
    assert main(["ingest", "patch-notes", str(snapshot)], container=writer) == 0
    ingested = capsys.readouterr()
    assert str(snapshot) in ingested.out
    assert "Reindexed" in ingested.out
    # `sentence-transformers` writes its model-load progress bar to stderr, so this asserts the
    # command's own contract (it reported no error) instead of silence from a third-party library.
    assert "error: " not in ingested.err

    corpus = JsonChunkRepository(config.processed_dir).all()
    chunk_ids = {chunk.chunk_id for chunk in corpus}
    assert any("spark" in chunk_id for chunk_id in chunk_ids)

    model = FirstChunkCitingLanguageModel()
    assert main(["ask", QUESTION], container=Container(config, environ={}, language_model=model)) == 0
    asked = capsys.readouterr()

    question, delivered = model.calls[0]
    assert question == QUESTION
    assert delivered, "the retrieval delivered nothing, so the indexes were not read back"
    cited_id = delivered[0]
    cited_chunk = next(chunk for chunk in corpus if chunk.chunk_id == cited_id)
    quote = next(line for line in cited_chunk.text.splitlines() if line.strip())

    assert f"Patch: {PATCH}" in asked.out
    assert f"1. {cited_id}" in asked.out
    assert quote in asked.out
    assert cited_chunk.source.url in asked.out
    assert "error: " not in asked.err


def test_ask_without_a_key_fails_before_reading_the_indexes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The generation half needs the credential; the failure is one line and no corpus is touched."""
    config: AppConfig = build_config(tmp_path)
    write_snapshot(tmp_path)

    assert main(["ask", QUESTION], container=Container(config, environ={})) == 1
    failed = capsys.readouterr()

    assert "ANTHROPIC_API_KEY" in failed.err
    assert failed.err.count("\n") == 1
    assert "Traceback" not in failed.err
    assert failed.out == ""
