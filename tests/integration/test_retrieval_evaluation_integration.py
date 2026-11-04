"""The retrieval evaluation end to end over the real indexes, at zero API cost (S9).

Marked `integration`: it loads `bge-base-en-v1.5` and `bge-reranker-base`. The fixtures are ingested into
`tmp_path` through the real parsers, so the run never reads a real corpus; the only fake is the HTTP
transport that serves the two committed gem pages. The container is built with an empty environment:
if the evaluation needed a credential the command would fail here, which is the point of the case.

The stale-retrieval assertion below is a guard, not a proof of the guard: `RetrieveContext` raises
`StaleChunkError` when a stage returns a chunk that is not valid at the target patch, so the real
pipeline cannot produce a stale delivered chunk at all. What this case proves is that the end-to-end
run reports a clean zero; the metric itself is proven to fire by
`test_a_stale_delivered_chunk_is_recorded_and_raises_the_stale_rate`, which feeds `RunEvaluation` a
retriever that breaks the rule on purpose.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cli_e2e import PATCH, integration_config, offline_client, write_snapshot

from poe2_rag.bootstrap import Container
from poe2_rag.cli import main
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)

pytestmark = pytest.mark.integration

EXAMPLE_DATASET = Path(__file__).parents[2] / "eval" / "dataset.example.jsonl"
PAGES = {
    "https://poe2db.tw/us/Spark": "active_gem_spark.html",
    "https://poe2db.tw/us/Pierce": "support_gem_pierce.html",
}


def test_eval_writes_a_report_with_a_zero_stale_rate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real BM25 + Chroma + reranker behind the example dataset, and a report on disk."""
    config = integration_config(tmp_path)
    snapshot = write_snapshot(tmp_path)
    container = Container(config, environ={}, http_client=offline_client(PAGES))

    assert main(["ingest", "patch-notes", str(snapshot)], container=container) == 0
    for url in PAGES:
        assert main(["ingest", "poe2db", url], container=container) == 0
    capsys.readouterr()

    corpus_ids = {chunk.chunk_id for chunk in JsonChunkRepository(config.processed_dir).all()}
    expected_ids = {
        chunk_id
        for line in EXAMPLE_DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for chunk_id in json.loads(line)["expected_chunk_ids"]
    }
    assert expected_ids <= corpus_ids, "the example dataset drifted from the committed fixtures"

    runs = tmp_path / "runs"
    assert (
        main(
            ["eval", "--dataset", str(EXAMPLE_DATASET), "--output-dir", str(runs)],
            container=container,
        )
        == 0
    )
    printed = capsys.readouterr()
    assert "error: " not in printed.err

    reports = sorted(runs.glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))

    assert report["run"]["git_commit"] is None or isinstance(report["run"]["git_commit"], str)
    assert report["dataset"]["cases"] == 4
    assert report["dataset"]["answerable"] == 3
    assert report["dataset"]["unanswerable"] == 1
    assert (
        report["dataset"]["sha256"]
        == hashlib.sha256(EXAMPLE_DATASET.read_bytes()).hexdigest()
    )
    assert report["config"]["retrieval"]["final_top_k"] == 5

    aggregate = report["metrics"]["aggregate"]
    assert aggregate["answerable_cases"] == 3
    assert aggregate["evaluated_cases"] == 4
    assert aggregate["stale_retrieval_rate"] == 0.0
    assert aggregate["hit_at_k"]["5"] > 0.0, "the example dataset found nothing in the top 5"
    assert set(aggregate["hit_at_k"]) == {"1", "3", "5"}
    assert sorted(report["metrics"]["by_tag"]) == ["gems", "patch-notes"]

    assert all(case["stale_chunk_ids"] == [] for case in report["cases"])
    assert all(case["patch"] == PATCH for case in report["cases"])
    scored = [case for case in report["cases"] if case["answerable"]]
    assert len(scored) == 3
    unanswerable = [case for case in report["cases"] if not case["answerable"]]
    assert len(unanswerable) == 1
    assert unanswerable[0]["first_hit_rank"] is None

    # The observed metrics are part of the deliverable, not only of the assertion.
    print(
        "observed metrics: "
        + json.dumps({"aggregate": aggregate, "by_tag": report["metrics"]["by_tag"]})
    )
