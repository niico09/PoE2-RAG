"""The reindex use case over fakes: read the corpus, rebuild both indexes, report the run."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime

import pytest
from ingestion_fakes import (
    CallRecorder,
    FakeChunkRepository,
    FakeDocumentEmbedder,
    FakeLexicalIndexWriter,
    FakeVectorIndexWriter,
    make_chunk,
)

from poe2_rag.ingestion.application import (
    DocumentEmbedder,
    EmbedderContractError,
    EmptyCorpusError,
    IngestionError,
    LexicalIndexWriter,
    ReindexCorpus,
    ReindexReport,
    VectorIndexWriter,
)
from poe2_rag.shared.domain import Chunk

REBUILT_AT = datetime(2026, 10, 30, 9, 30, tzinfo=UTC)
JOURNAL = ["all", "embed_documents", "overflowing_indices", "rebuild", "vector_rebuild"]


@dataclass(frozen=True)
class Harness:
    """The use case wired to recording fakes, with every port reachable for assertions."""

    reindex: ReindexCorpus
    recorder: CallRecorder
    lexical: FakeLexicalIndexWriter
    vector: FakeVectorIndexWriter
    embedder: FakeDocumentEmbedder


def make_reindex(
    chunks: Sequence[Chunk] = (),
    *,
    lexical_error: Exception | None = None,
    vector_error: Exception | None = None,
    embedder_error: Exception | None = None,
    overflowing: Sequence[int] = (),
    vector_count: int | None = None,
) -> Harness:
    """Build the use case with recording fakes and a pinned clock."""
    recorder = CallRecorder()
    lexical = FakeLexicalIndexWriter(recorder, error=lexical_error)
    vector = FakeVectorIndexWriter(recorder, error=vector_error)
    embedder = FakeDocumentEmbedder(
        recorder, overflowing=overflowing, error=embedder_error, vector_count=vector_count
    )
    reindex = ReindexCorpus(
        repository=FakeChunkRepository(recorder, chunks),
        lexical_writer=lexical,
        document_embedder=embedder,
        vector_writer=vector,
        clock=lambda: REBUILT_AT,
    )
    return Harness(reindex, recorder, lexical, vector, embedder)


class TestReport:
    def test_counts_every_chunk_the_corpus_holds(self) -> None:
        harness = make_reindex((make_chunk("general-changes"), make_chunk("skill-changes")))

        assert harness.reindex.execute().chunk_count == 2

    def test_reports_the_moment_the_clock_gives(self) -> None:
        assert make_reindex((make_chunk(),)).reindex.execute().rebuilt_at == REBUILT_AT

    def test_defaults_to_the_current_utc_moment(self) -> None:
        recorder = CallRecorder()
        reindex = ReindexCorpus(
            FakeChunkRepository(recorder, (make_chunk(),)),
            FakeLexicalIndexWriter(recorder),
            document_embedder=FakeDocumentEmbedder(recorder),
            vector_writer=FakeVectorIndexWriter(recorder),
        )

        before = datetime.now(UTC)
        rebuilt_at = reindex.execute().rebuilt_at
        after = datetime.now(UTC)

        assert rebuilt_at.tzinfo is UTC
        assert before <= rebuilt_at <= after

    def test_reports_no_truncation_when_the_embedder_reports_none(self) -> None:
        assert make_reindex((make_chunk(),)).reindex.execute().truncated_chunk_ids == ()

    def test_reports_the_ids_of_the_texts_the_embedder_could_not_fit(self) -> None:
        """No silent truncation: the report names the chunks the model had to cut (ADR-0004)."""
        chunks = (
            make_chunk("general-changes"),
            make_chunk("skill-changes"),
            make_chunk("bug-fixes"),
        )
        harness = make_reindex(chunks, overflowing=(2,))

        report = harness.reindex.execute()

        assert report.truncated_chunk_ids == (chunks[2].chunk_id,)

    def test_reports_each_truncated_chunk_once(self) -> None:
        chunks = (make_chunk("general-changes"), make_chunk("skill-changes"))
        harness = make_reindex(chunks, overflowing=(1, 1))

        assert harness.reindex.execute().truncated_chunk_ids == (chunks[1].chunk_id,)

    def test_reports_truncated_chunks_in_corpus_order(self) -> None:
        """The port does not promise an order, so the report imposes the corpus one."""
        chunks = (
            make_chunk("general-changes"),
            make_chunk("skill-changes"),
            make_chunk("bug-fixes"),
        )
        harness = make_reindex(chunks, overflowing=(2, 0))

        assert harness.reindex.execute().truncated_chunk_ids == (
            chunks[0].chunk_id,
            chunks[2].chunk_id,
        )

    def test_is_frozen(self) -> None:
        report = ReindexReport(1, REBUILT_AT)

        with pytest.raises(FrozenInstanceError):
            report.chunk_count = 2  # type: ignore[misc]


class TestEmbedding:
    def test_embeds_every_chunk_text_in_corpus_order(self) -> None:
        chunks = (
            make_chunk("general-changes", text="First text."),
            make_chunk("skill-changes", text="Second text."),
        )
        harness = make_reindex(chunks)

        harness.reindex.execute()

        assert harness.recorder.arguments("embed_documents") == [
            (("First text.", "Second text."),)
        ]

    def test_asks_the_embedder_which_texts_overflow(self) -> None:
        harness = make_reindex((make_chunk(),))

        harness.reindex.execute()

        assert harness.recorder.arguments("overflowing_indices") == [(("Notes for general-changes in 0.1.0.",),)]

    def test_embeds_before_it_writes_any_index(self) -> None:
        """A model that cannot run must leave both persisted indexes untouched (§5.3)."""
        harness = make_reindex((make_chunk(),), embedder_error=RuntimeError("no model"))

        with pytest.raises(RuntimeError, match="no model"):
            harness.reindex.execute()

        assert harness.recorder.kinds() == ["all", "embed_documents"]

    def test_hands_the_vector_writer_the_same_snapshot_and_the_vectors(self) -> None:
        chunks = (make_chunk("general-changes"), make_chunk("skill-changes"))
        harness = make_reindex(chunks)

        harness.reindex.execute()

        (argument_chunks, argument_vectors) = harness.recorder.arguments("vector_rebuild")[0]
        assert argument_chunks == chunks
        assert argument_vectors == harness.embedder.vectors(2)


class TestRebuild:
    def test_reads_the_corpus_once_and_rebuilds_both_indexes(self) -> None:
        harness = make_reindex((make_chunk(),))

        harness.reindex.execute()

        assert harness.recorder.kinds() == JOURNAL
        assert harness.recorder.kinds().count("all") == 1

    def test_rebuilds_each_index_once_with_every_chunk(self) -> None:
        chunks = (
            make_chunk("general-changes"),
            make_chunk("skill-changes"),
            make_chunk("bug-fixes"),
        )
        harness = make_reindex(chunks)

        harness.reindex.execute()

        (lexical,) = harness.recorder.arguments("rebuild")
        (vector, _) = harness.recorder.arguments("vector_rebuild")[0]
        assert lexical == (chunks,)
        assert vector == chunks
        assert harness.lexical.chunks == chunks
        assert harness.vector.chunks == chunks

    def test_hands_both_writers_an_immutable_snapshot(self) -> None:
        harness = make_reindex((make_chunk(),))

        harness.reindex.execute()

        (lexical,) = harness.recorder.arguments("rebuild")
        (vector, vectors) = harness.recorder.arguments("vector_rebuild")[0]
        assert isinstance(lexical[0], tuple)
        assert isinstance(vector, tuple)
        assert isinstance(vectors, tuple)


class TestFailures:
    def test_an_empty_corpus_raises_before_the_embedder_or_a_writer_runs(self) -> None:
        """An index rebuilt from nothing would answer "no information" for every question (D1)."""
        harness = make_reindex(())

        with pytest.raises(EmptyCorpusError):
            harness.reindex.execute()

        assert harness.recorder.kinds() == ["all"]

    def test_a_failing_lexical_writer_stops_the_run_before_the_vector_writer(self) -> None:
        harness = make_reindex(
            (make_chunk(),), lexical_error=RuntimeError("index directory is read-only")
        )

        with pytest.raises(RuntimeError, match="read-only"):
            harness.reindex.execute()

        assert harness.recorder.kinds() == JOURNAL[:-1]

    def test_a_failing_vector_writer_propagates(self) -> None:
        harness = make_reindex((make_chunk(),), vector_error=RuntimeError("chroma cannot write"))

        with pytest.raises(RuntimeError, match="chroma cannot write"):
            harness.reindex.execute()

        assert harness.recorder.kinds() == JOURNAL


class TestEmbedderContract:
    def test_rejects_a_vector_count_that_does_not_match_the_corpus(self) -> None:
        harness = make_reindex((make_chunk("general-changes"), make_chunk("skill-changes")), vector_count=1)

        with pytest.raises(EmbedderContractError, match="2"):
            harness.reindex.execute()

        assert harness.recorder.kinds() == ["all", "embed_documents"]

    def test_rejects_an_overflow_index_outside_the_batch(self) -> None:
        harness = make_reindex((make_chunk(),), overflowing=(1,))

        with pytest.raises(EmbedderContractError, match="1"):
            harness.reindex.execute()

        assert harness.recorder.kinds()[:3] == ["all", "embed_documents", "overflowing_indices"]
        assert "rebuild" not in harness.recorder.kinds()


class TestPorts:
    def test_the_writer_fake_satisfies_the_write_port(self) -> None:
        assert isinstance(FakeLexicalIndexWriter(CallRecorder()), LexicalIndexWriter)

    def test_the_vector_writer_fake_satisfies_the_write_port(self) -> None:
        assert isinstance(FakeVectorIndexWriter(CallRecorder()), VectorIndexWriter)

    def test_the_embedder_fake_satisfies_the_document_embedder_port(self) -> None:
        assert isinstance(FakeDocumentEmbedder(CallRecorder()), DocumentEmbedder)


class TestErrors:
    def test_empty_corpus_is_an_ingestion_error(self) -> None:
        assert issubclass(EmptyCorpusError, IngestionError)

    def test_an_embedder_contract_violation_is_an_ingestion_error(self) -> None:
        assert issubclass(EmbedderContractError, IngestionError)
