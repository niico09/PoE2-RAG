"""The per-level blocks fit the 512-token window the embedder and the reranker share (S4).

Marked `integration`: it loads the real `bge-base-en-v1.5` tokenizer and nothing else — the budget is a
property of the tokenizer, so no weights are loaded and no index is written, and the tokenizer import
stays inside the fixture so collecting the offline suite never touches `transformers`. The page is the
generated 40-row reconstruction from `poe2db_levels`, because the committed fixture is a two-row excerpt
of the capture (ADR-0002) and a one-block measurement of it would prove nothing about the shape the
parser really sees.
"""

from __future__ import annotations

import pytest
from poe2db_levels import (
    BLOCKS,
    FIXTURE,
    OBSERVED_WIDEST_ROW,
    ROW_COUNT,
    level_blocks,
    parse_page,
    rendered_rows,
    spark_level_page,
)

from poe2_rag.answering.adapters.outbound.dense.bge_embedder import (
    DEFAULT_MAX_SEQUENCE_LENGTH,
    DEFAULT_MODEL_ID,
    count_tokens,
)

pytestmark = pytest.mark.integration

MAX_TOKENS = DEFAULT_MAX_SEQUENCE_LENGTH
"""The window the real embedder enforces, so the guard cannot drift from the code it mirrors."""
MARGIN_TOKENS = 64
"""Headroom kept free in every block, so a slightly longer row still fits."""
OBSERVED_WORST = 380
"""Tokens of the widest 10-row block measured on the six real captures (Ice Nova)."""


@pytest.fixture(scope="module")
def tokenizer():
    """Load only the tokenizer: the budget is a token count, never a vector."""
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(DEFAULT_MODEL_ID)


def test_the_generated_rows_are_at_least_as_wide_as_the_widest_observed_row() -> None:
    """A narrower shape would make the measured budget meaningless."""
    assert max(len(row) for row in rendered_rows()) >= OBSERVED_WIDEST_ROW


def test_the_whole_table_would_not_fit_one_window(tokenizer) -> None:
    """The shape this replaces: one block holding all 40 rows overflows the window."""
    (whole,) = level_blocks(parse_page(spark_level_page(), ROW_COUNT))

    assert count_tokens(tokenizer, whole.text) > MAX_TOKENS


def test_every_default_block_fits_the_window_with_margin(tokenizer) -> None:
    blocks = level_blocks(parse_page(spark_level_page()))

    assert [block.section for block in blocks] == BLOCKS
    counts = [count_tokens(tokenizer, block.text) for block in blocks]
    print(
        f"{len(counts)} blocks of {ROW_COUNT // len(counts)} rows: generated proxy max "
        f"{max(counts)} tokens (margin {MAX_TOKENS - max(counts)}); observed worst {OBSERVED_WORST} "
        f"tokens (margin {MAX_TOKENS - OBSERVED_WORST})"
    )

    assert max(counts) + MARGIN_TOKENS <= MAX_TOKENS
    assert OBSERVED_WORST + MARGIN_TOKENS <= MAX_TOKENS


def test_the_committed_fixture_block_fits_the_window(tokenizer) -> None:
    (block,) = level_blocks(parse_page(FIXTURE.read_text(encoding="utf-8")))

    assert count_tokens(tokenizer, block.text) + MARGIN_TOKENS <= MAX_TOKENS
