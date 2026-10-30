"""Deterministic fake embedder for offline vector-index tests.

A hashed bag-of-words keeps the contract suite reproducible: the same text always produces the
same normalized vector, with no model download or GPU dependency.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence


class HashingEmbedder:
    """Produce a 64-dim L2-normalized vector from the hashed words of a text.

    The hash is unsalted (`hashlib.sha256`) so the output is stable across processes and
    platforms, which matters for contract tests that compare expected ids.
    """

    model_id: str = "fake-hashed-bow"
    dimension: int = 64

    def embed(self, text: str) -> tuple[float, ...]:
        """Return the embedding of a single query text."""
        return self._embed(text)

    def embed_documents(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Return one embedding per document, in the same order."""
        return tuple(self._embed(text) for text in texts)

    def overflowing_indices(self, texts: Sequence[str]) -> tuple[int, ...]:
        """The fake has no sequence-length limit, so nothing overflows."""
        return ()

    def _embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self.dimension
        words = re.findall(r"[a-z0-9]+", text.lower())
        for word in words:
            first = hashlib.sha256(word.encode("utf-8")).digest()
            second = hashlib.sha256(first).digest()
            for index, byte in enumerate(first + second):
                vector[index] += byte / 255.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return tuple(vector)
        return tuple(value / norm for value in vector)
