"""Shared helpers for every language-model adapter (S5, S12).

These functions encode the common pre-conditions and citation-verification rules that both
adapters must enforce with identical behaviour and messages.
"""

from __future__ import annotations

from collections.abc import Sequence

from poe2_rag.answering.adapters.outbound.llm.errors import LanguageModelRequestError
from poe2_rag.answering.domain import RetrievedChunk
from poe2_rag.shared.domain import is_blank


def require_question(question: object) -> None:
    """Reject a blank or non-string question before any request is sent."""
    if is_blank(question):
        raise LanguageModelRequestError(
            f"question must be a non-blank string, got {question!r}"
        )


def require_context(context: object) -> tuple[RetrievedChunk, ...]:
    """Materialize the context, rejecting anything that is not a delivered chunk.

    A bare string is rejected explicitly: iterating it would read characters as chunks. An empty
    context is rejected too, because a model with no document would answer from the question alone
    and every citation it produced would be unmappable.
    """
    if isinstance(context, (str, bytes)) or not isinstance(context, Sequence):
        raise LanguageModelRequestError(
            f"context must be a sequence of RetrievedChunk, got {context!r}"
        )
    materialized = tuple(context)
    if not materialized:
        raise LanguageModelRequestError("context must deliver at least one chunk")
    for retrieved in materialized:
        if not isinstance(retrieved, RetrievedChunk):
            raise LanguageModelRequestError(
                f"context must be RetrievedChunk, got {retrieved!r}"
            )
    return materialized


def quotes_chunk(quoted_text: str, chunk_text: str) -> bool:
    """Return True when `quoted_text` is part of the chunk, tolerant about whitespace.

    Exact about the character sequence, tolerant about the join: the model is asked to copy
    `quoted_text` verbatim, but it may re-join lines or drop the separators between them. An exact
    substring is accepted first, which is the normal case and costs nothing; otherwise both sides
    lose every whitespace character before comparing. That tolerance is a deliberate decision, not
    a side effect: the adapter test suite pins it, so a re-joined quote is evidence of backing while a
    quote that re-orders or re-spells words is not.
    """
    if quoted_text in chunk_text:
        return True
    return "".join(quoted_text.split()) in "".join(chunk_text.split())
