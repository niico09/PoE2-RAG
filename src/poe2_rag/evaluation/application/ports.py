"""Outbound ports of `RunEvaluation`: read a dataset, read the corpus, retrieve, write a report.

Every port is a `typing.Protocol`, so an adapter only has to match the method signatures and the use
case runs against the in-memory fakes of `tests/unit/evaluation/application/evaluation_fakes.py` (§8).
The retrieval port is the `RetrieveContext` use case itself: evaluation depends on the deterministic
half of the read path and never on a `LanguageModel` (ADR-0005).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from poe2_rag.answering.application import RetrievalResult
from poe2_rag.evaluation.application.report import EvalDataset, EvaluationReport
from poe2_rag.shared.domain import Chunk, PatchVersion


@runtime_checkable
class ContextRetriever(Protocol):
    """The retrieval use case, as evaluation consumes it: question in, delivered ids out."""

    @property
    def final_top_k(self) -> int:
        """How many chunks one retrieval delivers at most; no metric can measure beyond it."""
        ...

    def execute(self, text: str, version: PatchVersion | None = None) -> RetrievalResult:
        """Retrieve the context for `text` at `version`, or at the catalog's current patch."""
        ...


@runtime_checkable
class CorpusReader(Protocol):
    """The corpus as the drift check reads it: every stored chunk, or nothing when it is empty."""

    def all(self) -> Sequence[Chunk]:
        """Return every chunk of the current corpus."""
        ...


@runtime_checkable
class EvalDatasetReader(Protocol):
    """Turn a dataset file into cases plus the identity of the bytes that produced them."""

    def read(self, path: Path) -> EvalDataset:
        """Return the dataset at `path`, or fail with `InvalidDatasetError` naming the line.

        The returned dataset carries the file's SHA-256, so a report can be tied to the exact dataset
        that produced it without the use case touching the filesystem.
        """
        ...


@runtime_checkable
class EvaluationReportWriter(Protocol):
    """Persist one report under `directory` and return the path it wrote."""

    def write(self, report: EvaluationReport, directory: Path) -> Path:
        """Write `report` into `directory`, atomically, and return the file it created."""
        ...


@runtime_checkable
class Clock(Protocol):
    """The source of the run timestamp, injected so a report payload is reproducible."""

    def now(self) -> datetime:
        """Return the current instant, with a timezone."""
        ...
