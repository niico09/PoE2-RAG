"""The `eval` command adapter: one request in, one compact table out (§5, S7).

The report it formats comes from the shared builder in `tests/cli_fakes.py`, the same one the
dispatch suite uses, so a change in the payload shape breaks one fixture and not two.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cli_fakes import (
    EVAL_REPORT_PATH,
    FakeEvaluationRunner,
    eval_outcome,
    make_evaluation_run,
)

from poe2_rag.evaluation.adapters.inbound.cli import DEFAULT_KS, run
from poe2_rag.evaluation.application import (
    EvaluationReport,
    EvaluationRequest,
    EvaluationRun,
)

SPARK = eval_outcome("spark", 1, ("gems",))
PIERCE = eval_outcome("pierce", None, ("gems",))
TWO_CASES = make_evaluation_run((SPARK, PIERCE), ks=(1, 3), dataset_cases=3)
UNTAGGED = make_evaluation_run((eval_outcome("spark", 1),), ks=(1,))


def test_prints_one_compact_table_with_the_aggregate_and_the_tags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The table is a view of the report: counts, Hit@K, MRR, the stale guard, each tag, the path."""
    fake = FakeEvaluationRunner(run=TWO_CASES)

    run(dataset=Path("eval/other.jsonl"), ks=(1, 3), output_dir=Path("runs"), evaluation=fake)

    assert capsys.readouterr().out.splitlines() == [
        "Retrieval evaluation: 3 cases (2 answerable, 1 unanswerable)",
        f"Dataset: eval/dataset.jsonl (sha256 {'a' * 64})",
        "Hit@1: 0.500  Hit@3: 0.500",
        "MRR: 0.500",
        "Stale-retrieval rate: 0.000",
        "By tag:",
        "  gems: 2 answerable, Hit@1: 0.500  Hit@3: 0.500, MRR 0.500, stale 0.000",
        f"Report: {EVAL_REPORT_PATH}",
    ]


def test_forwards_the_arguments_as_one_request() -> None:
    fake = FakeEvaluationRunner()

    run(dataset=Path("eval/other.jsonl"), ks=(5,), output_dir=Path("runs"), evaluation=fake)

    assert fake.requests == [
        EvaluationRequest(dataset=Path("eval/other.jsonl"), ks=(5,), output_dir=Path("runs"))
    ]


def test_a_command_without_options_leaves_the_defaults_to_the_wiring() -> None:
    """`None` is passed through: the container owns the configured dataset and runs directory."""
    fake = FakeEvaluationRunner()

    run(dataset=None, output_dir=None, evaluation=fake)

    request = fake.requests[0]
    assert request.dataset is None
    assert request.output_dir is None
    assert request.ks == DEFAULT_KS


def test_a_run_with_no_tags_omits_the_breakdown(capsys: pytest.CaptureFixture[str]) -> None:
    """An untagged dataset prints no empty section: the table only shows what the report holds."""
    report = UNTAGGED.report
    cached = EvaluationReport(
        run=report.run,
        dataset=report.dataset,
        config=report.config,
        metrics=report.metrics,
        by_tag={},
        cases=report.cases,
    )

    run(
        dataset=None,
        ks=(1,),
        output_dir=None,
        evaluation=FakeEvaluationRunner(run=EvaluationRun(cached, EVAL_REPORT_PATH)),
    )

    printed = capsys.readouterr().out
    assert "By tag:" not in printed
    assert printed.count("\n") == 6
