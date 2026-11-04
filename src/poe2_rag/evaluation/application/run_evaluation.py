"""Evaluate retrieval against a fixed dataset, one run, no model (§8, D4, ADR-0005).

The order is fixed: read the dataset, refuse an empty one, check every expected id against the
current corpus, then retrieve each case once and score it. The drift check runs before the first
retrieval, so a dataset that no longer matches the corpus costs nothing and never produces numbers
that look comparable with a previous run.
"""

from __future__ import annotations

from pathlib import Path

from poe2_rag.evaluation.application.errors import (
    DatasetDriftError,
    EmptyDatasetError,
    InvalidEvaluationRequestError,
)
from poe2_rag.evaluation.application.ports import (
    Clock,
    ContextRetriever,
    CorpusReader,
    EvalDatasetReader,
    EvaluationReportWriter,
)
from poe2_rag.evaluation.application.report import (
    CaseRecord,
    ConfigSnapshot,
    DatasetInfo,
    EvalDataset,
    EvaluationReport,
    EvaluationRun,
    RunMetadata,
)
from poe2_rag.evaluation.application.request import EvaluationRequest
from poe2_rag.evaluation.domain import (
    CaseOutcome,
    EvalCase,
    first_hit_rank,
    score,
    score_by_tag,
)


class RunEvaluation:
    """Produce one metrics report for one dataset, through retrieval only."""

    __slots__ = (
        "_clock",
        "_config_snapshot",
        "_corpus",
        "_default_dataset_path",
        "_default_output_dir",
        "_git_commit",
        "_reader",
        "_retriever",
        "_writer",
    )

    def __init__(
        self,
        *,
        retriever: ContextRetriever,
        corpus: CorpusReader,
        reader: EvalDatasetReader,
        writer: EvaluationReportWriter,
        clock: Clock,
        config_snapshot: ConfigSnapshot,
        default_dataset_path: Path,
        default_output_dir: Path,
        git_commit: str | None = None,
    ) -> None:
        self._retriever = retriever
        self._corpus = corpus
        self._reader = reader
        self._writer = writer
        self._clock = clock
        self._config_snapshot = config_snapshot
        self._default_dataset_path = default_dataset_path
        self._default_output_dir = default_output_dir
        self._git_commit = git_commit

    def execute(self, request: EvaluationRequest) -> EvaluationRun:
        """Evaluate `request` and write its report, returning the report and the file it landed in.

        An empty dataset raises `EmptyDatasetError` and a dataset whose expected ids the corpus does
        not have raises `DatasetDriftError` listing them: neither produces a report, because D4
        compares runs and a run against a stale dataset is not comparable with anything. A K above
        the delivered top-K is refused for the same reason: it cannot measure what it names.
        """
        if not isinstance(request, EvaluationRequest):
            raise InvalidEvaluationRequestError(
                f"request must be an EvaluationRequest, got {request!r}"
            )
        final_top_k = self._retriever.final_top_k
        if max(request.ks) > final_top_k:
            # Retrieval delivers at most `final_top_k` chunks, so a larger K can only repeat the last
            # column while looking like it measured a wider window (D4).
            raise InvalidEvaluationRequestError(
                f"K {max(request.ks)} is beyond the {final_top_k} chunks retrieval delivers: "
                f"Hit@{max(request.ks)} would repeat Hit@{final_top_k}"
            )
        dataset = self._reader.read(request.dataset or self._default_dataset_path)
        if not dataset.cases:
            raise EmptyDatasetError(
                f"{dataset.path} has no evaluation case: there is nothing to measure"
            )
        self._require_known_chunk_ids(dataset)
        run_at = self._clock.now()
        records = tuple(self._evaluate(case) for case in dataset.cases)
        outcomes = tuple(record.outcome for record in records)
        report = EvaluationReport(
            run=RunMetadata(run_at, self._git_commit),
            dataset=DatasetInfo(
                path=dataset.path,
                sha256=dataset.sha256,
                cases=len(dataset.cases),
                answerable=sum(1 for case in dataset.cases if case.is_answerable),
            ),
            config=self._config_snapshot,
            metrics=score(outcomes, request.ks),
            by_tag=score_by_tag(outcomes, request.ks),
            cases=records,
        )
        output_dir = request.output_dir or self._default_output_dir
        return EvaluationRun(report, self._writer.write(report, output_dir))

    def _require_known_chunk_ids(self, dataset: EvalDataset) -> None:
        """Refuse to evaluate a dataset that names ids the corpus does not have (S5).

        The whole set is collected and named at once: a drift is an edited corpus or an edited dataset,
        and the operator needs the complete list to decide which one moved.
        """
        known = {chunk.chunk_id for chunk in self._corpus.all()}
        missing = sorted(
            {
                chunk_id
                for case in dataset.cases
                for chunk_id in case.expected_chunk_ids
                if chunk_id not in known
            }
        )
        if missing:
            raise DatasetDriftError(
                f"{dataset.path} names {len(missing)} chunk id(s) the corpus does not have: "
                + ", ".join(missing)
            )

    def _evaluate(self, case: EvalCase) -> CaseRecord:
        """Run retrieval for one case and reduce its result to ids, a rank and the stale guard."""
        retrieval = self._retriever.execute(case.question, case.patch)
        delivered_ids = tuple(item.chunk.chunk_id for item in retrieval.delivered)
        stale_ids = tuple(
            item.chunk.chunk_id
            for item in retrieval.delivered
            if not item.chunk.is_valid_at(retrieval.target_version)
        )
        rank = (
            first_hit_rank(delivered_ids, case.expected_chunk_ids) if case.is_answerable else None
        )
        return CaseRecord(
            CaseOutcome(
                case_id=case.case_id,
                answerable=case.is_answerable,
                tags=case.tags,
                delivered_chunk_ids=delivered_ids,
                first_hit_rank=rank,
                stale_chunk_ids=stale_ids,
            ),
            retrieval.target_version,
        )
