"""How much each retrieval stage may keep for one query."""

from __future__ import annotations

from dataclasses import dataclass

from poe2_rag.answering.application.errors import InvalidRetrievalSettingsError
from poe2_rag.answering.domain.fusion import DEFAULT_K

_BUDGETS = ("lexical_top_n", "dense_top_n", "rerank_top_m", "final_top_k", "rrf_k")


@dataclass(frozen=True, slots=True)
class RetrievalSettings:
    """Per-stage budgets, small enough to keep the prompt cheap and wide enough to find the answer.

    `final_top_k <= rerank_top_m` keeps the pipeline monotone: the reranker cannot deliver more
    chunks than it was given, so a final budget above the rerank budget could never be filled.
    """

    lexical_top_n: int
    dense_top_n: int
    rerank_top_m: int
    final_top_k: int
    rrf_k: int = DEFAULT_K

    def __post_init__(self) -> None:
        for name in _BUDGETS:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidRetrievalSettingsError(f"{name} must be an int, got {value!r}")
            if value <= 0:
                raise InvalidRetrievalSettingsError(f"{name} must be positive, got {value}")
        if self.final_top_k > self.rerank_top_m:
            raise InvalidRetrievalSettingsError(
                f"final_top_k ({self.final_top_k}) must not exceed rerank_top_m ({self.rerank_top_m})"
            )
