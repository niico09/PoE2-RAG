"""Recording fakes for the evaluation application ports.

Each fake records the arguments it received, so a run can be checked for what it asked and in which
order without a file, a clock or a real retrieval. The corpus fake returns real `Chunk` values built
from their ids, because the drift check compares ids and nothing else.

The module carries the capability name because `tests/` is not a package: pytest puts each test
directory on `sys.path`, and a second capability's `fakes.py` would shadow this one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from poe2_rag.answering.application import RetrievalResult
from poe2_rag.answering.domain import RetrievedChunk, StageScores
from poe2_rag.evaluation.application import EvalDataset, EvaluationReport
from poe2_rag.evaluation.domain import EvalCase
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    PatchVersion,
    SourceRef,
    Validity,
)

RUN_AT = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
DEFAULT_PATCH = PatchVersion.parse("0.1.1")


def make_chunk(
    slug: str = "spark",
    *,
    kind: str = "active_gem",
    source_type: str = "poe2db",
    section: str = "description",
    valid_from: str = "0.1.1",
    valid_until: str | None = None,
) -> Chunk:
    """Build a chunk valid from `valid_from`, or up to `valid_until` when it is given."""
    return Chunk(
        entity=EntityRef(kind, slug, slug.title()),
        validity=Validity(
            PatchVersion.parse(valid_from),
            None if valid_until is None else PatchVersion.parse(valid_until),
        ),
        section=section,
        text=f"{slug.title()} {section} text.",
        source=SourceRef(source_type, f"https://poe2db.tw/us/{slug.title()}", date(2026, 10, 29)),
    )


def delivered(chunk: Chunk, rank: float = 1.0) -> RetrievedChunk:
    """A chunk as the reranker delivers it, with every stage score set."""
    return RetrievedChunk(chunk, StageScores(lexical=1.0, dense=0.5, fused=1.0, rerank=rank))


@dataclass
class FakeRetriever:
    """Returns one scripted retrieval per question and records every call."""

    results: dict[str, RetrievalResult] = field(default_factory=dict)
    calls: list[tuple[str, PatchVersion | None]] = field(default_factory=list)
    fallback: RetrievalResult | None = None
    delivered_top_k: int = 5

    @property
    def final_top_k(self) -> int:
        """The budget the real use case would apply: the evaluation validates K against it."""
        return self.delivered_top_k

    def execute(self, text: str, version: PatchVersion | None = None) -> RetrievalResult:
        self.calls.append((text, version))
        if text in self.results:
            return self.results[text]
        if self.fallback is not None:
            return self.fallback
        return RetrievalResult(version or DEFAULT_PATCH, ())


@dataclass
class FakeCorpus:
    """Holds the ids the corpus knows, and rejects nothing: the drift check reads `all`."""

    chunks: tuple[Chunk, ...] = ()

    @classmethod
    def with_ids(cls, *chunk_ids: str) -> FakeCorpus:
        """Build a corpus whose chunks carry `chunk_ids` (the slugs are parsed out of each id)."""
        chunks = tuple(_chunk_for_id(chunk_id) for chunk_id in chunk_ids)
        return cls(chunks)

    def all(self) -> tuple[Chunk, ...]:
        return self.chunks


@dataclass
class FakeDatasetReader:
    """Returns one scripted dataset and records the path it was asked for."""

    dataset: EvalDataset
    paths: list[Path] = field(default_factory=list)

    def read(self, path: Path) -> EvalDataset:
        self.paths.append(path)
        return self.dataset


@dataclass
class FakeReportWriter:
    """Records every report it was asked to write and returns a fixed path."""

    path: Path = Path("data/eval/runs/report.json")
    writes: list[tuple[EvaluationReport, Path]] = field(default_factory=list)

    def write(self, report: EvaluationReport, directory: Path) -> Path:
        self.writes.append((report, directory))
        return self.path


@dataclass
class FakeClock:
    """A clock frozen at one instant, so a report payload is deterministic."""

    instant: datetime = RUN_AT
    readings: int = 0

    def now(self) -> datetime:
        self.readings += 1
        return self.instant


def dataset(cases: Sequence[EvalCase], *, path: Path | None = None) -> EvalDataset:
    """Wrap `cases` in a dataset with a fixed digest, so a test never has to hash a file."""
    return EvalDataset(
        path=path or Path("eval/dataset.jsonl"),
        sha256="0" * 64,
        cases=tuple(cases),
    )


def _chunk_for_id(chunk_id: str) -> Chunk:
    """Build a chunk whose computed `chunk_id` is `chunk_id`, so the corpus and the id agree."""
    identity, _, section = chunk_id.partition("#")
    prefix, _, version = identity.partition("@")
    source_type, kind, slug = prefix.split(":", 2)
    return Chunk(
        entity=EntityRef(kind, slug, slug.title()),
        validity=Validity(PatchVersion.parse(version), None),
        section=section,
        text=f"{slug} text.",
        source=SourceRef(source_type, f"https://example.invalid/{slug}", date(2026, 10, 29)),
    )
