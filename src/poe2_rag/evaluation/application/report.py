"""The evaluation report as data: ids and numbers only, ready to be serialized by an adapter.

The report is a value object, so the payload a run produces is checkable without a filesystem, and
the writer adapter stays a thin "encode and replace atomically" step. Nothing here holds chunk text:
a report can be shared or committed to a local runs directory without redistributing corpus content.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from poe2_rag.answering.application import RetrievalSettings
from poe2_rag.evaluation.application.errors import (
    InvalidDatasetError,
    InvalidReportError,
)
from poe2_rag.evaluation.domain import CaseOutcome, EvalCase, Metrics
from poe2_rag.shared.domain import PatchVersion, is_blank

REPORT_SCHEMA_VERSION = 1
"""The shape of the JSON document. Bump it when a consumer would have to read the file differently."""

_DIGEST = re.compile(r"[0-9a-f]{64}")
COMMIT_ID_PATTERN = re.compile(r"[0-9a-f]{7,64}")
"""A short or full git commit id, lowercase hex: the only shape a report file name can carry."""


@dataclass(frozen=True, slots=True)
class EvalDataset:
    """The cases of one dataset file, the path it was read from and the digest of its bytes."""

    path: Path
    sha256: str
    cases: tuple[EvalCase, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise InvalidDatasetError(f"path must be a Path, got {self.path!r}")
        if not isinstance(self.sha256, str) or not _DIGEST.fullmatch(self.sha256):
            raise InvalidDatasetError(f"sha256 must be a hex digest, got {self.sha256!r}")
        if not isinstance(self.cases, tuple) or not all(
            isinstance(case, EvalCase) for case in self.cases
        ):
            raise InvalidDatasetError(f"cases must be a tuple of EvalCase, got {self.cases!r}")
        seen: set[str] = set()
        for case in self.cases:
            if case.case_id in seen:
                raise InvalidDatasetError(f"the dataset repeats the case id {case.case_id!r}")
            seen.add(case.case_id)


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """The identity of the pipeline that produced a run: the models and the per-stage budgets.

    D4 compares two runs, so what produced the numbers has to travel with them. The credential is
    absent by construction: this snapshot holds model ids and budgets, never an environment value.
    It describes the run; it is not the authority on the budgets. The use case that applies them
    (`RetrieveContext.final_top_k`) is what a check has to ask, so the report cannot disagree with
    what actually ran.
    """

    embedder_model: str
    reranker_model: str
    retrieval: RetrievalSettings

    def __post_init__(self) -> None:
        for name in ("embedder_model", "reranker_model"):
            value = getattr(self, name)
            if not isinstance(value, str) or is_blank(value):
                raise InvalidReportError(f"{name} must be a non-blank string, got {value!r}")
        if not isinstance(self.retrieval, RetrievalSettings):
            raise InvalidReportError(
                f"retrieval must be a RetrievalSettings, got {self.retrieval!r}"
            )

    def as_dict(self) -> dict[str, object]:
        """Render the snapshot for the report.

        The retrieval budgets are read from the value object's own fields, so a budget added to the
        pipeline appears in the report without anyone remembering to list it here. A hand-written
        list would silently omit it, and two runs would look comparable while measuring different
        pipelines (D4).
        """
        return {
            "embedder_model": self.embedder_model,
            "reranker_model": self.reranker_model,
            "retrieval": {
                field.name: getattr(self.retrieval, field.name)
                for field in fields(self.retrieval)
            },
        }


@dataclass(frozen=True, slots=True)
class DatasetInfo:
    """Which dataset produced a run, how many cases it held and how many of them are answerable."""

    path: Path
    sha256: str
    cases: int
    answerable: int

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise InvalidReportError(f"path must be a Path, got {self.path!r}")
        if self.cases < 0 or self.answerable < 0 or self.answerable > self.cases:
            raise InvalidReportError(
                f"answerable ({self.answerable}) must be between 0 and cases ({self.cases})"
            )

    @property
    def unanswerable(self) -> int:
        """Cases whose expected list is empty: counted and listed, never scored (S4)."""
        return self.cases - self.answerable

    def as_dict(self) -> dict[str, object]:
        """Render the dataset identity for the report."""
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "cases": self.cases,
            "answerable": self.answerable,
            "unanswerable": self.unanswerable,
        }


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """When the run happened and which commit it measured; both injected, never computed here."""

    run_at: datetime
    git_commit: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_at, datetime) or self.run_at.tzinfo is None:
            raise InvalidReportError(
                f"run_at must be a timezone-aware datetime, got {self.run_at!r}"
            )
        if self.git_commit is None:
            return
        # Validated here, not only in the writer: a commit that cannot name a file would otherwise
        # burn a whole run and fail after every case was already retrieved.
        if not isinstance(self.git_commit, str) or not COMMIT_ID_PATTERN.fullmatch(
            self.git_commit
        ):
            raise InvalidReportError(
                f"git_commit must be a 7-64 character lowercase hex commit id or None, "
                f"got {self.git_commit!r}"
            )

    def as_dict(self) -> dict[str, object]:
        """Render the run identity for the report."""
        return {"run_at": self.run_at.isoformat(), "git_commit": self.git_commit}


@dataclass(frozen=True, slots=True)
class CaseRecord:
    """One case as it was evaluated: what retrieval delivered, where the first hit landed, its patch."""

    outcome: CaseOutcome
    target_version: PatchVersion

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, CaseOutcome):
            raise InvalidReportError(f"outcome must be a CaseOutcome, got {self.outcome!r}")
        if not isinstance(self.target_version, PatchVersion):
            raise InvalidReportError(
                f"target_version must be a PatchVersion, got {self.target_version!r}"
            )

    def as_dict(self) -> dict[str, object]:
        """Render the case for the report: ids and numbers, never chunk text."""
        outcome = self.outcome
        return {
            "id": outcome.case_id,
            "answerable": outcome.answerable,
            "tags": list(outcome.tags),
            "patch": str(self.target_version),
            "first_hit_rank": outcome.first_hit_rank,
            "delivered_chunk_ids": list(outcome.delivered_chunk_ids),
            "stale_chunk_ids": list(outcome.stale_chunk_ids),
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """One retrieval evaluation: what ran, against which dataset, and what it measured."""

    run: RunMetadata
    dataset: DatasetInfo
    config: ConfigSnapshot
    metrics: Metrics
    by_tag: Mapping[str, Metrics]
    cases: tuple[CaseRecord, ...]

    def __post_init__(self) -> None:
        for name, expected in (
            ("run", RunMetadata),
            ("dataset", DatasetInfo),
            ("config", ConfigSnapshot),
            ("metrics", Metrics),
        ):
            value = getattr(self, name)
            if not isinstance(value, expected):
                raise InvalidReportError(f"{name} must be a {expected.__name__}, got {value!r}")
        if not isinstance(self.cases, tuple) or not all(
            isinstance(record, CaseRecord) for record in self.cases
        ):
            raise InvalidReportError(f"cases must be a tuple of CaseRecord, got {self.cases!r}")
        if not isinstance(self.by_tag, Mapping):
            raise InvalidReportError(f"by_tag must be a mapping, got {self.by_tag!r}")
        object.__setattr__(self, "by_tag", MappingProxyType(dict(self.by_tag)))

    def as_dict(self) -> dict[str, object]:
        """Render the whole report: the payload the JSON adapter writes, with no chunk text."""
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "run": self.run.as_dict(),
            "dataset": self.dataset.as_dict(),
            "config": self.config.as_dict(),
            "metrics": {
                "aggregate": self.metrics.as_dict(),
                "by_tag": {tag: metrics.as_dict() for tag, metrics in self.by_tag.items()},
            },
            "cases": [record.as_dict() for record in self.cases],
        }


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    """The report plus the file the adapter wrote it to, so the CLI can print both."""

    report: EvaluationReport
    report_path: Path
