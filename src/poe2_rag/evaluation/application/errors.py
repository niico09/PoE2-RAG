"""Exceptions the evaluation application layer owns: data and wiring problems, never a crash."""

from __future__ import annotations


class EvaluationError(ValueError):
    """Base class for every failure the evaluation use case reports as an expected error.

    It derives from `ValueError`, like the domain errors, so a caller can catch every malformed
    value or unusable artifact once, and the CLI boundary reports each one as a single line.
    """


class InvalidEvaluationRequestError(EvaluationError):
    """Raised when a request names no dataset, or a K set that cannot produce a metric table."""


class InvalidDatasetError(EvaluationError):
    """Raised when an evaluation dataset cannot be read, or one of its lines is malformed."""


class EmptyDatasetError(EvaluationError):
    """Raised when the dataset holds no case: there is nothing to measure (D4)."""


class InvalidReportError(EvaluationError):
    """Raised when a report value object is built with a field it cannot carry."""


class DatasetDriftError(EvaluationError):
    """Raised when the dataset names chunk ids the current corpus no longer has (S5)."""
