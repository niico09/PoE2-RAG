"""One real OpenCode CLI call with two small chunks, asserting the citations map back to them.

Marked `live`: it needs the `opencode` binary on PATH, network and a valid OpenCode Go
subscription, so it is excluded by `addopts` like the Claude live suite and is never part of a
normal run. It exists to prove the one thing recorded responses cannot: that the dedicated,
tool-free agent accepts the request, returns a JSON answer and cites the delivered `chunk_id`s.
"""

from __future__ import annotations

import shutil
from datetime import date

import pytest

from poe2_rag.answering.adapters.outbound.llm.opencode_language_model import (
    OpenCodeLanguageModel,
)
from poe2_rag.answering.domain import Answer, RetrievedChunk, StageScores
from poe2_rag.shared.domain import Chunk, EntityRef, PatchVersion, SourceRef, Validity

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("opencode") is None,
        reason="opencode is not on PATH",
    ),
]

TARGET = PatchVersion.parse("0.2.0")
THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)
QUESTION = (
    "How much lightning damage does Spark deal, and how many enemies does it chain to?"
)
SCORES = StageScores(lexical=0.5, dense=0.25, fused=0.125, rerank=0.75)


def make_retrieved(section: str, text: str) -> RetrievedChunk:
    """Build one delivered chunk with the scores the pipeline would have given it."""
    chunk = Chunk(
        entity=EntityRef("patch_notes", "0-2-0", "Patch 0.2.0"),
        validity=Validity(TARGET),
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
    )
    return RetrievedChunk(chunk, SCORES)


SPARK = make_retrieved(
    "spark-damage",
    "Spark deals 10 to 15 lightning damage.\nSpark chains to two additional enemies.",
)
CASCADE = make_retrieved(
    "glacial-cascade",
    "Glacial Cascade releases a wave of cold damage in a cone.",
)
CONTEXT = (SPARK, CASCADE)


def test_a_real_answer_cites_the_delivered_chunks() -> None:
    generated = OpenCodeLanguageModel(
        model="opencode-go/deepseek-v4.1-flash",
        timeout=120.0,
    ).generate(QUESTION, CONTEXT)

    assert generated.citations, "a real cited answer must come back with citations"
    delivered_ids = {retrieved.chunk.chunk_id for retrieved in CONTEXT}
    assert {citation.chunk_id for citation in generated.citations} <= delivered_ids
    assert "10" in generated.text
    answer = Answer.build(generated.text, generated.citations, CONTEXT)
    assert answer.supported
