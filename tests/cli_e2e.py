"""Helpers shared by the end-to-end CLI suites (`tests/integration` and `tests/live`).

`tests/conftest.py` puts this directory on `sys.path`, so both suites import the same configuration,
snapshot writer and scripted language model instead of keeping two copies that could drift. The
language model is the only fake in the end-to-end flow: it cites the first delivered chunk exactly as
a model would, so the citation that reaches the terminal is a real `chunk_id` from the real corpus.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx2

from poe2_rag.answering.application import GeneratedAnswer
from poe2_rag.answering.domain import Citation, RetrievedChunk
from poe2_rag.config import AppConfig

FIXTURES = Path(__file__).parent / "fixtures" / "cli"
POE2DB_FIXTURES = Path(__file__).parent / "fixtures" / "poe2db"
ROBOTS_URL = "https://poe2db.tw/robots.txt"
SNAPSHOT_NAME = "patch-notes-0.1.1.html"
PATCH = "0.1.1"
QUESTION = "How much mana does Spark cost?"
CAPTURED_AT = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def build_config(tmp_path: Path) -> AppConfig:
    """Point every artifact at `tmp_path`, so no test reads or writes a real corpus or index."""
    return AppConfig(
        project_root=tmp_path,
        raw_dir=tmp_path / "raw",
        processed_dir=tmp_path / "processed",
        index_dir=tmp_path / "index",
        patch_catalog_path=write_catalog(tmp_path),
        embedder_model_id="BAAI/bge-base-en-v1.5",
        reranker_model_id="BAAI/bge-reranker-base",
        llm_model="claude-sonnet-5-5",
        llm_max_tokens=1024,
        llm_effort="medium",
    )


def integration_config(tmp_path: Path) -> AppConfig:
    """Config for a run that parses poe2db pages: a contact and a negligible request interval."""
    return replace(
        build_config(tmp_path),
        http_contact="integration@example.invalid",
        http_request_interval=0.001,
    )


def client_serving(bodies: Mapping[str, str]) -> httpx2.Client:
    """Serve `robots.txt` and each URL's in-memory body, so no suite touches the network.

    One transport serves every suite that ingests a poe2db page: the HTTP handler is the only fake in
    all of them, so it lives here instead of one copy per suite that would drift.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == ROBOTS_URL:
            return httpx2.Response(200, text="User-agent: *\nAllow: /")
        body = bodies.get(url)
        if body is None:
            raise AssertionError(f"unexpected request to {url}")
        return httpx2.Response(200, text=body)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def offline_client(pages: Mapping[str, str]) -> httpx2.Client:
    """Serve the committed fixtures, given as a URL → fixture-name map."""
    bodies = {
        url: (POE2DB_FIXTURES / name).read_text(encoding="utf-8") for url, name in pages.items()
    }
    return client_serving(bodies)


def write_catalog(root: Path) -> Path:
    """Write a catalog whose current patch is the one the snapshot declares."""
    path = root / "patches.toml"
    path.write_text(
        f'current = "{PATCH}"\n\n'
        '[[patches]]\nversion = "0.1.0"\nreleased_on = 2024-12-06\n\n'
        f'[[patches]]\nversion = "{PATCH}"\nreleased_on = 2025-01-17\n',
        encoding="utf-8",
    )
    return path


def write_snapshot(root: Path) -> Path:
    """Copy the hand-written fixture and write the sidecar capture date the fetcher requires."""
    snapshot = root / SNAPSHOT_NAME
    shutil.copyfile(FIXTURES / SNAPSHOT_NAME, snapshot)
    snapshot.with_name(snapshot.name + ".meta.json").write_text(
        json.dumps({"captured_at": CAPTURED_AT.isoformat()}), encoding="utf-8"
    )
    return snapshot


class FirstChunkCitingLanguageModel:
    """A `LanguageModel` that quotes the first delivered chunk, without reaching the API.

    It records the question and the delivered `chunk_id`s so a test can prove the retrieval it saw was
    real, and it cites one non-empty line of that chunk so `Answer.build` accepts the answer and the
    CLI prints a citation the corpus can be searched for.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def generate(self, question: str, context: Sequence[RetrievedChunk]) -> GeneratedAnswer:
        """Return an answer that quotes the first delivered chunk once."""
        self.calls.append((question, tuple(item.chunk.chunk_id for item in context)))
        first = context[0]
        quote = next(line for line in first.chunk.text.splitlines() if line.strip())
        return GeneratedAnswer(
            f"{quote} [{first.chunk.chunk_id}]",
            (Citation(first.chunk.chunk_id, quote),),
        )

    @property
    def delivered_chunk_ids(self) -> tuple[str, ...]:
        """The chunk ids the retrieval delivered on the first call."""
        return self.calls[0][1] if self.calls else ()
