"""The `poe2-rag eval` command: build one evaluation request and print its metrics table (S7).

No business logic lives here. The adapter turns the parsed arguments into an `EvaluationRequest`,
lets the use case read, retrieve, score and write, and formats what came back. It never resolves a
default path, never hashes a dataset and never rounds a number that goes into the report: the table is
a view of the report, not a second source of truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from poe2_rag.evaluation.application import DEFAULT_KS, EvaluationRequest, EvaluationRun
from poe2_rag.evaluation.domain import Metrics


class EvaluationRunner(Protocol):
    """Port the CLI uses to run one evaluation without knowing its constructor."""

    def execute(self, request: EvaluationRequest) -> EvaluationRun:
        """Evaluate `request` and return the report plus the file it was written to."""
        ...


def run(
    *,
    dataset: Path | None,
    output_dir: Path | None,
    ks: tuple[int, ...] = DEFAULT_KS,
    evaluation: EvaluationRunner,
) -> None:
    """Evaluate the dataset the user asked for and print the result.

    A `None` dataset or output directory is passed through untouched: the wiring owns the defaults, so
    a command that omits `--dataset` measures the configured dataset and not a guess from this layer.
    """
    # pi-lens-ignore: python-sql-injection -- an injected use case port; this project has no SQL.
    result = evaluation.execute(
        EvaluationRequest(dataset=dataset, ks=ks, output_dir=output_dir)
    )
    print(_table(result))


def _table(result: EvaluationRun) -> str:
    """Render the compact table: the counts, the aggregate metrics, each tag and the report path."""
    report = result.report
    lines = [
        (
            f"Retrieval evaluation: {report.dataset.cases} cases "
            f"({report.dataset.answerable} answerable, {report.dataset.unanswerable} unanswerable)"
        ),
        f"Dataset: {report.dataset.path} (sha256 {report.dataset.sha256})",
        _rates(report.metrics),
        f"MRR: {report.metrics.mrr:.3f}",
        f"Stale-retrieval rate: {report.metrics.stale_retrieval_rate:.3f}",
    ]
    if report.by_tag:
        lines.append("By tag:")
        lines.extend(
            f"  {tag}: {metrics.answerable_cases} answerable, {_rates(metrics)}, "
            f"MRR {metrics.mrr:.3f}, "
            f"stale {metrics.stale_retrieval_rate:.3f}"
            for tag, metrics in report.by_tag.items()
        )
    lines.append(f"Report: {result.report_path}")
    return "\n".join(lines)


def _rates(metrics: Metrics) -> str:
    """Render the Hit@K columns of one table row, in the order the run asked for them."""
    return "  ".join(f"Hit@{k}: {rate:.3f}" for k, rate in metrics.hit_at_k)
