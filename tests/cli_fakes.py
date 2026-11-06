"""Test doubles for the CLI boundary (S5, S6).

The fakes stand in for the application use cases so the CLI suite stays offline and
independent of the composition root. They return real domain objects built through the
public constructors, so the CLI still exercises the real answer/ingest shapes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from poe2_rag.answering.adapters.inbound.cli import AnswerQuestionRunner
from poe2_rag.answering.application import NO_INFORMATION_TEXT, RetrievalSettings
from poe2_rag.answering.application.answer_question import AnswerResult
from poe2_rag.answering.domain import Answer, Citation, RetrievedChunk, StageScores
from poe2_rag.bootstrap import EXPECTED_ERRORS as CONTAINER_ERRORS
from poe2_rag.evaluation.application import (
    CaseRecord,
    ConfigSnapshot,
    DatasetInfo,
    EvaluationReport,
    EvaluationRequest,
    EvaluationRun,
    RunMetadata,
)
from poe2_rag.evaluation.domain import CaseOutcome, score, score_by_tag
from poe2_rag.ingestion.adapters.inbound.cli import (
    IngestSourceRunner,
    ReindexCorpusRunner,
)
from poe2_rag.ingestion.application.ingest_source import IngestReport
from poe2_rag.ingestion.application.reindex_corpus import ReindexReport
from poe2_rag.shared.domain import Chunk, EntityRef, PatchVersion, SourceRef, Validity


class FakeIngestSource:
    """Records every locator it receives and returns a deterministic ingest report."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, locator: str) -> IngestReport:
        self.calls.append(locator)
        return IngestReport(
            locator=locator,
            chunk_ids=("chunk-1", "chunk-2"),
            skipped=0,
        )


class FakeReindexCorpus:
    """Records every reindex call and returns a deterministic reindex report."""

    def __init__(
        self,
        *,
        chunk_count: int = 2,
        truncated_chunk_ids: tuple[str, ...] = (),
    ) -> None:
        self.calls: list[object] = []
        self.chunk_count = chunk_count
        self.truncated_chunk_ids = truncated_chunk_ids

    def execute(self) -> ReindexReport:
        self.calls.append(())
        return ReindexReport(
            chunk_count=self.chunk_count,
            rebuilt_at=datetime.now(UTC),
            truncated_chunk_ids=self.truncated_chunk_ids,
        )


class FakeAnswerQuestion:
    """Records every (text, version) pair and returns a real answer result."""

    def __init__(self, result: AnswerResult | None = None) -> None:
        self.calls: list[tuple[str, PatchVersion | None]] = []
        self._result = result

    def execute(self, text: str, version: PatchVersion | None = None) -> AnswerResult:
        self.calls.append((text, version))
        if self._result is not None:
            return self._result
        return make_cited_answer(text, version)


class FakeContainer:
    """A container whose three runners are fakes, satisfying ``CliContainer`` structurally.

    ``expected_errors`` is the composition root's real tuple: the point of these cases is that the
    boundary reports what the container declares, so a fake that invented its own list would test the
    wrong thing. ``ingest_source`` is the one source-aware entry point, so it records the
    ``(source, refresh)`` pair the CLI asked for and hands back the stored runner: the assertions read
    both, which is how a wrong refresh flag or a wrong source name becomes visible.
    """

    expected_errors: tuple[type[Exception], ...] = CONTAINER_ERRORS

    def __init__(
        self,
        *,
        ingest_source: FakeIngestSource | None = None,
        reindex_corpus: FakeReindexCorpus | None = None,
        answer_question: FakeAnswerQuestion | None = None,
    ) -> None:
        self.ingest = ingest_source or FakeIngestSource()
        self.reindex_corpus = reindex_corpus or FakeReindexCorpus()
        self.answer_question = answer_question or FakeAnswerQuestion()
        self.ingest_requests: list[tuple[str, bool]] = []

    def ingest_source(self, *, source: str, refresh: bool) -> FakeIngestSource:
        """Record the ``(source, refresh)`` request and return the stored runner."""
        self.ingest_requests.append((source, refresh))
        return self.ingest


class FailingContainer:
    """A container that raises a configured exception from one of its runners.

    This lets the error-contract tests prove the CLI boundary without wiring a real
    adapter that would load models or need credentials.
    """

    expected_errors: tuple[type[Exception], ...] = CONTAINER_ERRORS

    def __init__(
        self,
        *,
        ingest_source: Exception | None = None,
        reindex_corpus: Exception | None = None,
        answer_question: Exception | None = None,
    ) -> None:
        self._ingest_source = ingest_source
        self._reindex_corpus = reindex_corpus
        self._answer_question = answer_question

    def ingest_source(self, *, source: str, refresh: bool) -> IngestSourceRunner:
        if self._ingest_source is not None:
            raise self._ingest_source
        return FakeIngestSource()

    @property
    def reindex_corpus(self) -> ReindexCorpusRunner:
        if self._reindex_corpus is not None:
            raise self._reindex_corpus
        return FakeReindexCorpus()

    @property
    def answer_question(self) -> AnswerQuestionRunner:
        if self._answer_question is not None:
            raise self._answer_question
        return FakeAnswerQuestion()


def _make_chunk(question: str) -> Chunk:
    """Build a real chunk to back a cited answer."""
    return Chunk(
        entity=EntityRef(kind="mechanic", slug="mana", name="Mana"),
        validity=Validity(valid_from=PatchVersion(0, 1, 0)),
        section="overview",
        text=f"The answer to {question} is 42.",
        source=SourceRef(
            type="patch_notes",
            url="https://www.pathofexile.com/forum/view-thread/1234",
            captured_at=date(2024, 12, 6),
        ),
    )


def make_cited_answer(question: str, version: PatchVersion | None) -> AnswerResult:
    """Return a supported answer with two citations to one real chunk."""
    target_version = version or PatchVersion(0, 1, 1)
    chunk = _make_chunk(question)
    retrieved = RetrievedChunk(
        chunk,
        StageScores(lexical=0.9, dense=0.8, fused=0.85, rerank=0.95),
    )
    citations = (
        Citation(chunk_id=chunk.chunk_id, quoted_text="The answer to"),
        Citation(chunk_id=chunk.chunk_id, quoted_text="is 42."),
    )
    answer = Answer.build("42", citations, (retrieved,))
    return AnswerResult(answer, target_version, (retrieved,))


def make_unsupported_answer(version: PatchVersion | None = None) -> AnswerResult:
    """Return the fixed unsupported answer with no citations."""
    target_version = version or PatchVersion(0, 1, 1)
    answer = Answer(NO_INFORMATION_TEXT, (), False)
    return AnswerResult(answer, target_version, ())


EVAL_RUN_AT = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
EVAL_REPORT_PATH = Path("data/eval/runs/20261101T093000Z-6fb9b03-aaaaaaaa.json")
"""A fixed report path, so a command test can assert the line the adapter prints."""


class FakeEvaluationRunner:
    """Records every evaluation request and returns one scripted run, or raises what it was given."""

    def __init__(self, failure: Exception | None = None, run: EvaluationRun | None = None) -> None:
        self.requests: list[EvaluationRequest] = []
        self._failure = failure
        self._run = run

    def execute(self, request: EvaluationRequest) -> EvaluationRun:
        self.requests.append(request)
        if self._failure is not None:
            raise self._failure
        if self._run is not None:
            return self._run
        return make_evaluation_run()


def eval_outcome(case_id: str, rank: int | None, tags: tuple[str, ...] = ()) -> CaseOutcome:
    """One scored case with two delivered chunks, so a rank of 2 is representable."""
    return CaseOutcome(
        case_id=case_id,
        answerable=True,
        tags=tags,
        delivered_chunk_ids=("a", "b"),
        first_hit_rank=rank,
    )


def make_evaluation_run(
    outcomes: Sequence[CaseOutcome] = (),
    *,
    ks: tuple[int, ...] = (1, 3, 5),
    dataset_cases: int | None = None,
) -> EvaluationRun:
    """Build one run from real value objects, so a command test formats values and not a mock.

    Both CLI suites use it: the adapter suite asks for a two-case table with two K columns, the
    dispatch suite asks for the default shape. One builder keeps the two payloads from drifting.
    """
    scored = tuple(outcomes) or (eval_outcome("spark", 1, ("gems",)),)
    answerable = sum(1 for item in scored if item.answerable)
    report = EvaluationReport(
        run=RunMetadata(EVAL_RUN_AT, "6fb9b03"),
        dataset=DatasetInfo(
            path=Path("eval/dataset.jsonl"),
            sha256="a" * 64,
            cases=dataset_cases if dataset_cases is not None else len(scored),
            answerable=answerable,
        ),
        config=ConfigSnapshot(
            "BAAI/bge-base-en-v1.5",
            "BAAI/bge-reranker-base",
            RetrievalSettings(50, 50, 20, 5),
        ),
        metrics=score(scored, ks),
        by_tag=score_by_tag(scored, ks),
        cases=tuple(CaseRecord(item, PatchVersion(0, 1, 1)) for item in scored),
    )
    return EvaluationRun(report, EVAL_REPORT_PATH)
