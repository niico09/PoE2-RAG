"""Evaluation domain: the fixed dataset's cases and the retrieval metrics, free of external dependencies.

Nothing here reads a file, formats a report or knows a patch catalog: a case is data and a metric is
arithmetic, which is what lets D4 compare two pipeline versions without spending a token (ADR-0005).
"""

from poe2_rag.evaluation.domain.errors import InvalidEvalCaseError, InvalidMetricsError
from poe2_rag.evaluation.domain.eval_case import EvalCase
from poe2_rag.evaluation.domain.metrics import (
    CaseOutcome,
    Metrics,
    first_hit_rank,
    require_ks,
    score,
    score_by_tag,
)

__all__ = [
    "CaseOutcome",
    "EvalCase",
    "InvalidEvalCaseError",
    "InvalidMetricsError",
    "Metrics",
    "first_hit_rank",
    "require_ks",
    "score",
    "score_by_tag",
]
