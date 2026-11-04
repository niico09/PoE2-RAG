"""Evaluation application: `RunEvaluation` and its ports, over the retrieval use case (ADR-0005)."""

from poe2_rag.evaluation.application.errors import (
    DatasetDriftError,
    EmptyDatasetError,
    EvaluationError,
    InvalidDatasetError,
    InvalidEvaluationRequestError,
    InvalidReportError,
)
from poe2_rag.evaluation.application.ports import (
    Clock,
    ContextRetriever,
    CorpusReader,
    EvalDatasetReader,
    EvaluationReportWriter,
)
from poe2_rag.evaluation.application.report import (
    REPORT_SCHEMA_VERSION,
    CaseRecord,
    ConfigSnapshot,
    DatasetInfo,
    EvalDataset,
    EvaluationReport,
    EvaluationRun,
    RunMetadata,
)
from poe2_rag.evaluation.application.request import DEFAULT_KS, EvaluationRequest
from poe2_rag.evaluation.application.run_evaluation import RunEvaluation

__all__ = [
    "DEFAULT_KS",
    "REPORT_SCHEMA_VERSION",
    "CaseRecord",
    "Clock",
    "ConfigSnapshot",
    "ContextRetriever",
    "CorpusReader",
    "DatasetDriftError",
    "DatasetInfo",
    "EmptyDatasetError",
    "EvalDataset",
    "EvalDatasetReader",
    "EvaluationError",
    "EvaluationReport",
    "EvaluationReportWriter",
    "EvaluationRequest",
    "EvaluationRun",
    "InvalidDatasetError",
    "InvalidEvaluationRequestError",
    "InvalidReportError",
    "RunEvaluation",
    "RunMetadata",
]
