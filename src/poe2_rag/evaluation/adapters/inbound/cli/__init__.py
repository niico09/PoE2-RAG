"""Command-line inbound adapter for measuring retrieval against a fixed dataset."""

from poe2_rag.evaluation.adapters.inbound.cli.eval import (
    DEFAULT_KS,
    EvaluationRunner,
    run,
)

__all__ = ["DEFAULT_KS", "EvaluationRunner", "run"]
