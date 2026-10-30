"""One real Claude call with two small chunks, asserting the citations map back to them.

Marked `live`: it costs money and needs network and an `ANTHROPIC_API_KEY`, so it is excluded by
`addopts` like the `integration` suite and is never part of a normal run. It exists to prove the one
thing no recorded response can: that the request the adapter sends is accepted by the real API, that
the API returns content-block citations for custom content documents, and that those citations map
back to the delivered `chunk_id`s and pass `Answer.build`.
"""

from __future__ import annotations

import os
from datetime import date

import anthropic
import pytest

from poe2_rag.answering.adapters.outbound.llm.claude_language_model import (
    ClaudeLanguageModel,
)
from poe2_rag.answering.domain import Answer, RetrievedChunk, StageScores
from poe2_rag.shared.domain import Chunk, EntityRef, PatchVersion, SourceRef, Validity

pytestmark = pytest.mark.live

TARGET = PatchVersion.parse("0.2.0")
THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)
QUESTION = "How much lightning damage does Spark deal, and how many enemies does it chain to?"
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
    "Spark deals 10 to 15 lightning damage.\n"
    "Spark chains to two additional enemies.",
)
CASCADE = make_retrieved(
    "glacial-cascade",
    "Glacial Cascade releases a wave of cold damage in a cone.",
)
CONTEXT = (SPARK, CASCADE)


def test_a_real_answer_cites_the_delivered_chunks() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY is not set; the live suite is opt-in and costs money")

    client = anthropic.Anthropic(api_key=api_key)
    generated = ClaudeLanguageModel(client, max_tokens=256).generate(QUESTION, CONTEXT)

    assert generated.citations, "a real cited answer must come back with citations"
    delivered_ids = {retrieved.chunk.chunk_id for retrieved in CONTEXT}
    assert {citation.chunk_id for citation in generated.citations} <= delivered_ids
    assert "10" in generated.text
    # The domain still has the last word: every citation must name a delivered chunk.
    answer = Answer.build(generated.text, generated.citations, CONTEXT)
    assert answer.supported
