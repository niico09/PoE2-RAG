"""Test doubles for the CLI boundary (S5, S6).

The fakes stand in for the application use cases so the CLI suite stays offline and
independent of the composition root. They return real domain objects built through the
public constructors, so the CLI still exercises the real answer/ingest shapes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from poe2_rag.answering.adapters.inbound.cli import AnswerQuestionRunner
from poe2_rag.answering.application import NO_INFORMATION_TEXT
from poe2_rag.answering.application.answer_question import AnswerResult
from poe2_rag.answering.domain import Answer, Citation, RetrievedChunk, StageScores
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
    """A container whose three runners are fakes, satisfying ``CliContainer`` structurally."""

    def __init__(
        self,
        *,
        ingest_source: FakeIngestSource | None = None,
        reindex_corpus: FakeReindexCorpus | None = None,
        answer_question: FakeAnswerQuestion | None = None,
    ) -> None:
        self.ingest_source = ingest_source or FakeIngestSource()
        self.reindex_corpus = reindex_corpus or FakeReindexCorpus()
        self.answer_question = answer_question or FakeAnswerQuestion()


class FailingContainer:
    """A container that raises a configured exception from one of its runners.

    This lets the error-contract tests prove the CLI boundary without wiring a real
    adapter that would load models or need credentials.
    """

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

    @property
    def ingest_source(self) -> IngestSourceRunner:
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
