"""The evaluation use case over fakes: drift, per-tag aggregation and a deterministic payload (§8)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from evaluation_fakes import (
    RUN_AT,
    FakeClock,
    FakeCorpus,
    FakeDatasetReader,
    FakeReportWriter,
    FakeRetriever,
    dataset,
    delivered,
    make_chunk,
)

from poe2_rag.answering.application import RetrievalResult, RetrievalSettings
from poe2_rag.evaluation.application import (
    ConfigSnapshot,
    DatasetDriftError,
    EmptyDatasetError,
    EvaluationRequest,
    EvaluationRun,
    InvalidEvaluationRequestError,
    RunEvaluation,
)
from poe2_rag.evaluation.domain import EvalCase
from poe2_rag.shared.domain import PatchVersion

DEFAULT_PATCH = PatchVersion.parse("0.1.1")
SPARK = make_chunk("spark", section="properties")
PIERCE = make_chunk("pierce", kind="support_gem")
STALE = make_chunk("fireball", valid_from="0.1.2")

CASE_SPARK = EvalCase(
    "spark-mana-cost",
    "How much mana does Spark cost?",
    (SPARK.chunk_id,),
    tags=("gems",),
)
CASE_PIERCE = EvalCase(
    "pierce-support",
    "What does the Pierce support gem do?",
    (PIERCE.chunk_id,),
    tags=("gems", "patch-notes"),
)
CASE_UNANSWERABLE = EvalCase(
    "temporalis-drop-rate",
    "What is the drop rate of the Temporalis?",
    (),
    tags=("unanswerable",),
)

CONFIG = ConfigSnapshot(
    embedder_model="BAAI/bge-base-en-v1.5",
    reranker_model="BAAI/bge-reranker-base",
    retrieval=RetrievalSettings(
        lexical_top_n=50, dense_top_n=50, rerank_top_m=20, final_top_k=5
    ),
)


@dataclass
class Harness:
    """One wired use case with the fakes it was built from, so a test can read what it did."""

    use_case: RunEvaluation
    retriever: FakeRetriever
    reader: FakeDatasetReader
    writer: FakeReportWriter
    clock: FakeClock
    corpus: FakeCorpus

    def run(self, request: EvaluationRequest | None = None) -> EvaluationRun:
        """Evaluate with `request`, or with the wiring's defaults."""
        # pi-lens-ignore: python-sql-injection -- an injected use case port; this project has no SQL.
        return self.use_case.execute(request or EvaluationRequest())


def expected_ids(cases: tuple[EvalCase, ...]) -> tuple[str, ...]:
    """Every id the cases expect, in order, so the default corpus makes the dataset consistent."""
    return tuple(dict.fromkeys(cid for case in cases for cid in case.expected_chunk_ids))


def harness(
    cases: tuple[EvalCase, ...],
    *,
    results: dict[str, RetrievalResult] | None = None,
    corpus_ids: tuple[str, ...] | None = None,
    git_commit: str | None = "6fb9b03",
    dataset_path: Path = Path("eval/dataset.jsonl"),
) -> Harness:
    """Wire the use case against fresh fakes with a corpus that matches every expected id."""
    reader = FakeDatasetReader(dataset(cases, path=dataset_path))
    retriever = FakeRetriever(results=results or {})
    writer = FakeReportWriter()
    clock = FakeClock()
    corpus = FakeCorpus.with_ids(*(corpus_ids if corpus_ids is not None else expected_ids(cases)))
    use_case = RunEvaluation(
        retriever=retriever,
        corpus=corpus,
        reader=reader,
        writer=writer,
        clock=clock,
        config_snapshot=CONFIG,
        default_dataset_path=Path("eval/dataset.jsonl"),
        default_output_dir=Path("data/eval/runs"),
        git_commit=git_commit,
    )
    return Harness(use_case, retriever, reader, writer, clock, corpus)


def test_evaluates_every_case_and_reports_the_aggregate_metrics() -> None:
    """Two answerable cases at ranks 1 and none, plus an unanswerable one that is only counted."""
    harnessed = harness(
        (CASE_SPARK, CASE_PIERCE, CASE_UNANSWERABLE),
        results={
            CASE_SPARK.question: RetrievalResult(DEFAULT_PATCH, (delivered(SPARK),)),
        },
    )

    run = harnessed.run()

    metrics = run.report.metrics
    assert metrics.answerable_cases == 2
    assert metrics.hit_at_k == ((1, 0.5), (3, 0.5), (5, 0.5))
    assert metrics.mrr == 0.5
    assert metrics.stale_retrieval_rate == 0.0
    assert run.report.dataset.cases == 3
    assert run.report.dataset.answerable == 2
    assert run.report.dataset.unanswerable == 1


def test_the_report_carries_the_run_identity_and_the_dataset_digest() -> None:
    """The clock and the commit are injected, never computed: a payload stays reproducible."""
    harnessed = harness((CASE_SPARK,))

    run = harnessed.run()

    assert run.report.run.run_at == RUN_AT
    assert run.report.run.git_commit == "6fb9b03"
    assert run.report.dataset.path == Path("eval/dataset.jsonl")
    assert run.report.dataset.sha256 == "0" * 64
    assert harnessed.clock.readings == 1


def test_each_case_is_recorded_with_its_patch_rank_and_delivered_ids() -> None:
    """The per-case list is the debugging view: what came back, in which order, at which patch."""
    longer = make_chunk("spark", section="levels")
    harnessed = harness(
        (CASE_SPARK,),
        results={
            CASE_SPARK.question: RetrievalResult(DEFAULT_PATCH, (delivered(longer), delivered(SPARK))),
        },
    )

    record = harnessed.run().report.cases[0]

    assert record.outcome.case_id == CASE_SPARK.case_id
    assert record.outcome.first_hit_rank == 2
    assert record.outcome.delivered_chunk_ids == (longer.chunk_id, SPARK.chunk_id)
    assert record.outcome.stale_chunk_ids == ()
    assert record.target_version == DEFAULT_PATCH


def test_the_report_payload_is_a_deterministic_document() -> None:
    """The whole JSON shape is pinned here, so a silent field rename breaks a test, not a comparison."""
    harnessed = harness(
        (CASE_SPARK, CASE_UNANSWERABLE),
        results={CASE_SPARK.question: RetrievalResult(DEFAULT_PATCH, (delivered(SPARK),))},
    )

    payload = harnessed.run(EvaluationRequest(ks=(1, 3))).report.as_dict()

    assert payload == {
        "schema_version": 1,
        "run": {"run_at": "2026-11-01T09:30:00+00:00", "git_commit": "6fb9b03"},
        "dataset": {
            "path": "eval/dataset.jsonl",
            "sha256": "0" * 64,
            "cases": 2,
            "answerable": 1,
            "unanswerable": 1,
        },
        "config": {
            "embedder_model": "BAAI/bge-base-en-v1.5",
            "reranker_model": "BAAI/bge-reranker-base",
            "retrieval": {
                "lexical_top_n": 50,
                "dense_top_n": 50,
                "rerank_top_m": 20,
                "final_top_k": 5,
                "rrf_k": 60,
            },
        },
        "metrics": {
            "aggregate": {
                "answerable_cases": 1,
                "evaluated_cases": 2,
                "hit_at_k": {"1": 1.0, "3": 1.0},
                "mrr": 1.0,
                "stale_retrieval_rate": 0.0,
            },
            "by_tag": {
                "gems": {
                    "answerable_cases": 1,
                    "evaluated_cases": 1,
                    "hit_at_k": {"1": 1.0, "3": 1.0},
                    "mrr": 1.0,
                    "stale_retrieval_rate": 0.0,
                }
            },
        },
        "cases": [
            {
                "id": "spark-mana-cost",
                "answerable": True,
                "tags": ["gems"],
                "patch": "0.1.1",
                "first_hit_rank": 1,
                "delivered_chunk_ids": [SPARK.chunk_id],
                "stale_chunk_ids": [],
            },
            {
                "id": "temporalis-drop-rate",
                "answerable": False,
                "tags": ["unanswerable"],
                "patch": "0.1.1",
                "first_hit_rank": None,
                "delivered_chunk_ids": [],
                "stale_chunk_ids": [],
            },
        ],
    }


def test_the_report_holds_ids_and_never_chunk_text() -> None:
    """A report can live in a local runs directory only if it does not carry the corpus with it."""
    harnessed = harness(
        (CASE_SPARK,),
        results={CASE_SPARK.question: RetrievalResult(DEFAULT_PATCH, (delivered(SPARK),))},
    )

    serialized = json.dumps(harnessed.run().report.as_dict())

    assert SPARK.chunk_id in serialized
    assert SPARK.text not in serialized


def test_a_dataset_naming_an_unknown_chunk_id_fails_before_any_retrieval() -> None:
    """S5: the drift is named, listed, and costs no retrieval and no report."""
    harnessed = harness((CASE_SPARK, CASE_PIERCE), corpus_ids=(SPARK.chunk_id,))

    with pytest.raises(DatasetDriftError) as exc_info:
        harnessed.run()

    assert PIERCE.chunk_id in str(exc_info.value)
    assert SPARK.chunk_id not in str(exc_info.value)
    assert harnessed.retriever.calls == []
    assert harnessed.writer.writes == []


def test_an_empty_dataset_is_a_specific_error() -> None:
    """A dataset with no line cannot measure anything, and writing an empty report would hide it."""
    harnessed = harness(())

    with pytest.raises(EmptyDatasetError, match="nothing to measure"):
        harnessed.run()

    assert harnessed.writer.writes == []


def test_the_report_groups_metrics_by_tag() -> None:
    """A case with two tags counts in both tables; the aggregate stays the total."""
    harnessed = harness(
        (CASE_SPARK, CASE_PIERCE),
        results={CASE_SPARK.question: RetrievalResult(DEFAULT_PATCH, (delivered(SPARK),))},
    )

    by_tag = harnessed.run(EvaluationRequest(ks=(1,))).report.by_tag

    assert sorted(by_tag) == ["gems", "patch-notes"]
    assert by_tag["gems"].answerable_cases == 2
    assert by_tag["gems"].hit_at_k == ((1, 0.5),)
    assert by_tag["patch-notes"].answerable_cases == 1
    assert by_tag["patch-notes"].hit_at_k == ((1, 0.0),)


def test_the_default_dataset_and_runs_directory_come_from_the_wiring() -> None:
    """The CLI passes what the user typed; the container owns `eval/dataset.jsonl` and `data/eval/runs`."""
    harnessed = harness((CASE_SPARK,))

    harnessed.run()

    assert harnessed.reader.paths == [Path("eval/dataset.jsonl")]
    assert harnessed.writer.writes[0][1] == Path("data/eval/runs")


def test_the_request_overrides_the_dataset_the_ks_and_the_runs_directory() -> None:
    harnessed = harness((CASE_SPARK,))

    run = harnessed.run(
        EvaluationRequest(
            dataset=Path("other/dataset.jsonl"), ks=(4,), output_dir=Path("other/runs")
        )
    )

    assert harnessed.reader.paths == [Path("other/dataset.jsonl")]
    assert harnessed.writer.writes[0][1] == Path("other/runs")
    assert [k for k, _ in run.report.metrics.hit_at_k] == [4]
    assert run.report_path == harnessed.writer.path


def test_a_case_without_a_patch_asks_retrieval_for_the_default_patch() -> None:
    """The dataset decides only when it names a patch: otherwise the catalog's current patch wins."""
    harnessed = harness((CASE_SPARK,))

    harnessed.run()

    assert harnessed.retriever.calls == [(CASE_SPARK.question, None)]


def test_a_case_with_a_patch_asks_for_it() -> None:
    case = EvalCase(
        "spark-mana-cost-old",
        "How much mana did Spark cost?",
        (SPARK.chunk_id,),
        patch=DEFAULT_PATCH,
    )
    harnessed = harness((case,))

    harnessed.run()

    assert harnessed.retriever.calls == [(case.question, DEFAULT_PATCH)]


def test_a_stale_delivered_chunk_is_recorded_and_raises_the_stale_rate() -> None:
    """D1's guard: the filter should make this impossible, so the metric must expose it if it is not."""
    harnessed = harness(
        (CASE_SPARK,),
        results={CASE_SPARK.question: RetrievalResult(DEFAULT_PATCH, (delivered(STALE),))},
    )

    run = harnessed.run()

    assert run.report.cases[0].outcome.stale_chunk_ids == (STALE.chunk_id,)
    assert run.report.metrics.stale_retrieval_rate == 1.0


@pytest.mark.parametrize("ks", [(), (0,), (1, 1)])
def test_an_unusable_k_set_is_rejected_by_the_request(ks: tuple[int, ...]) -> None:
    with pytest.raises(InvalidEvaluationRequestError, match="k"):
        EvaluationRequest(ks=ks)


def test_a_k_beyond_the_delivered_top_k_is_refused_before_any_retrieval() -> None:
    """Hit@10 over a top-5 pipeline would repeat Hit@5 while looking like a wider measurement."""
    harnessed = harness((CASE_SPARK,))

    with pytest.raises(InvalidEvaluationRequestError, match="beyond the 5 chunks"):
        harnessed.run(EvaluationRequest(ks=(10,)))

    assert harnessed.retriever.calls == []
    assert harnessed.writer.writes == []
