"""Helpers to inspect the two parts of a chunk's text: its context line and its body."""

from __future__ import annotations

from poe2_rag.shared.domain import Chunk


def context_line(chunk: Chunk) -> str:
    """Return the chunk's first line, the one that names the entity and the section."""
    return chunk.text.split("\n", 1)[0]


def body(chunk: Chunk) -> str:
    """Return everything after the context line, which stays the page's own text."""
    return chunk.text.split("\n", 1)[1]
