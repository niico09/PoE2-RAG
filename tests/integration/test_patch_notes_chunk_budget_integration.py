"""Default patch-notes blocks fit the 512-token window the embedder and the reranker share (S4).

Marked `integration`: the budget is a property of the real tokenizer, so this module loads
`bge-base-en-v1.5`'s tokenizer and, for the truncation test, its embedder; the tokenizer import stays
inside the fixture so collecting the offline suite never touches `transformers`. The fixtures are
trimmed excerpts of the 2026-11-06 captures (ADR-0002 forbids committing the full pages): the
Content Update thread's banner and two sections, the widest section of that thread, and the 0.5.5c
thread, which has no section headings at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from poe2_rag.answering.adapters.outbound.dense.bge_embedder import (
    DEFAULT_MAX_SEQUENCE_LENGTH,
    DEFAULT_MODEL_ID,
    BgeEmbedder,
    token_lengths,
)
from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import (
    PatchNotesParser,
    estimate_tokens,
)
from poe2_rag.ingestion.application import RawDocument

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "fixtures" / "patch_notes"
CAPTURED_AT = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
MAX_TOKENS = DEFAULT_MAX_SEQUENCE_LENGTH
"""The window the real embedder enforces, so the guard cannot drift from the code it mirrors."""
MARGIN_TOKENS = 64
"""Headroom kept free in every block, so a slightly longer bullet still fits."""
BLOCKED_FIXTURES = (
    "content_update_thread.html",
    "oversized_section_thread.html",
    "notes_only_thread.html",
)
OBSERVED_WORST = 264
"""Tokens of the widest default block measured on these fixtures (a 0.5.5c `notes` block).

The five full captures of 2026-11-06 peak higher: the widest block of the Content Update thread
measures 326 tokens, and no block of any capture measures above its `estimate_tokens` value.
"""


@pytest.fixture(scope="module")
def tokenizer():
    """Load only the tokenizer: the budget is a token count, never a vector."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(DEFAULT_MODEL_ID)


def parse(name: str, *, max_block_tokens: int = 400) -> tuple:
    """Parse one trimmed capture with the requested budget."""
    parser = PatchNotesParser(max_block_tokens=max_block_tokens)
    return parser.parse(
        RawDocument(
            locator=f"data/raw/{name}",
            content=(FIXTURES / name).read_text(encoding="utf-8"),
            fetched_at=CAPTURED_AT,
            source_type="patch_notes",
        )
    )


def test_the_unblocked_sections_would_not_fit_one_window(tokenizer) -> None:
    """The shape this replaces: one chunk per section overflows the window."""
    for name in ("oversized_section_thread.html", "notes_only_thread.html"):
        (section,) = parse(name, max_block_tokens=100_000)
        (count,) = token_lengths(tokenizer, [section.text])
        print(f"{name} unblocked: {count} tokens, budget {MAX_TOKENS}")

        assert count > MAX_TOKENS


def test_every_default_block_fits_the_window_with_margin(tokenizer) -> None:
    worst = 0
    for name in BLOCKED_FIXTURES:
        chunks = parse(name)
        counts = token_lengths(tokenizer, [chunk.text for chunk in chunks])
        print(
            f"{name}: {len(counts)} blocks, max {max(counts)} tokens "
            f"(margin {MAX_TOKENS - max(counts)})"
        )
        worst = max(worst, *counts)

        assert max(counts) + MARGIN_TOKENS <= MAX_TOKENS

    print(f"worst default block over the fixtures: {worst} tokens")
    assert OBSERVED_WORST + MARGIN_TOKENS <= MAX_TOKENS


def test_the_estimate_stays_above_the_real_count_on_the_captured_shapes(tokenizer) -> None:
    """The parser splits offline, so its estimate must not fall under the real count."""
    texts = [chunk.text for name in BLOCKED_FIXTURES for chunk in parse(name)]
    real = token_lengths(tokenizer, texts)
    estimates = [estimate_tokens(text) for text in texts]

    assert all(
        estimate >= count for estimate, count in zip(estimates, real, strict=True)
    )


def test_the_real_embedder_reports_no_truncated_chunk() -> None:
    """`ReindexReport.truncated_chunk_ids` is exactly this list, so an empty list is the report."""
    embedder = BgeEmbedder(DEFAULT_MODEL_ID)
    chunks = [chunk for name in BLOCKED_FIXTURES for chunk in parse(name)]

    overflowing = embedder.overflowing_indices([chunk.text for chunk in chunks])
    truncated = [chunks[index].chunk_id for index in overflowing]
    print(f"{len(chunks)} chunks through the real embedder, truncated: {truncated}")

    assert truncated == []
