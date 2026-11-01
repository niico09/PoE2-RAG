"""Command-line inbound adapter for asking questions against the retrieval pipeline."""

from poe2_rag.answering.adapters.inbound.cli.ask import AnswerQuestionRunner, run

__all__ = ["AnswerQuestionRunner", "run"]
