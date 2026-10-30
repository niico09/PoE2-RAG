"""The CLI with the real Claude adapter: same flow as the integration suite, real API call (S9).

Marked `live`: it costs money, needs `ANTHROPIC_API_KEY` and is excluded by `addopts`, so it runs only
with `uv run pytest -q -m live`. It is written and never run by the feature that added it (S9). What it
adds over the integration suite is the one thing a fake cannot prove: that the adapter's request is
accepted by the real API and that its native citations map back to chunk ids this corpus really holds.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cli_e2e import PATCH, QUESTION, build_config, write_snapshot

from poe2_rag.bootstrap import Container
from poe2_rag.cli import main
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="the live suite needs ANTHROPIC_API_KEY and costs money",
)
def test_the_cli_answers_with_the_real_claude_adapter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`ingest` then `ask` through the real container: cited answer, real chunk ids, exit 0."""
    config = build_config(tmp_path)
    snapshot = write_snapshot(tmp_path)

    assert main(["ingest", "patch-notes", str(snapshot)], container=Container(config)) == 0
    capsys.readouterr()

    assert main(["ask", QUESTION], container=Container(config)) == 0
    asked = capsys.readouterr()

    assert f"Patch: {PATCH}" in asked.out
    assert asked.err == ""
    stored = {chunk.chunk_id for chunk in JsonChunkRepository(config.processed_dir).all()}
    if "Citations:" in asked.out:
        cited = [
            line.split(". ", 1)[1]
            for line in asked.out.splitlines()
            if line[:1].isdigit() and ". " in line
        ]
        assert cited, "a supported answer must print at least one numbered citation"
        assert set(cited) <= stored, f"a citation named a chunk outside the corpus: {cited}"
    else:
        assert "not backed by any citation" in asked.out
