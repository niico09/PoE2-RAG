"""Adapter-specific behaviour of the JSON evaluation report writer."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from poe2_rag.answering.application import RetrievalSettings
from poe2_rag.evaluation.adapters.outbound.json_report_writer import (
    JsonReportWriter,
    ReportWriteError,
)
from poe2_rag.evaluation.application.errors import InvalidReportError
from poe2_rag.evaluation.application.report import (
    CaseRecord,
    ConfigSnapshot,
    DatasetInfo,
    EvaluationReport,
    RunMetadata,
)
from poe2_rag.evaluation.domain import CaseOutcome, Metrics
from poe2_rag.shared.domain import PatchVersion

REPORT_AT = datetime(2026, 11, 1, 9, 30, 0, tzinfo=UTC)


class _RunDouble:
    """The two fields the writer reads from a run."""

    def __init__(self, run_at: datetime, git_commit: str) -> None:
        self.run_at = run_at
        self.git_commit = git_commit


class _DatasetDouble:
    """The only dataset field the writer reads."""

    def __init__(self, sha256: str) -> None:
        self.sha256 = sha256


class _ReportDouble:
    """A report shape the writer accepts, so its own guard can be tested in isolation."""

    def __init__(self, run: _RunDouble, dataset: _DatasetDouble) -> None:
        self.run = run
        self.dataset = dataset

    def as_dict(self) -> dict[str, object]:
        return {}


def _make_report(run_at: datetime, git_commit: str | None) -> EvaluationReport:
    """Build a minimal report for writer tests."""
    return EvaluationReport(
        run=RunMetadata(run_at=run_at, git_commit=git_commit),
        dataset=DatasetInfo(
            path=Path("data/eval.jsonl"),
            sha256="0" * 64,
            cases=1,
            answerable=1,
        ),
        config=ConfigSnapshot(
            embedder_model="embedder",
            reranker_model="reranker",
            retrieval=RetrievalSettings(
                lexical_top_n=10,
                dense_top_n=10,
                rerank_top_m=5,
                final_top_k=5,
            ),
        ),
        metrics=Metrics(
            answerable_cases=1,
            evaluated_cases=1,
            hit_at_k=((1, 1.0), (5, 1.0)),
            mrr=1.0,
            stale_retrieval_rate=0.0,
        ),
        by_tag={
            "tag": Metrics(
                answerable_cases=1,
                evaluated_cases=1,
                hit_at_k=((1, 1.0),),
                mrr=1.0,
                stale_retrieval_rate=0.0,
            )
        },
        cases=(
            CaseRecord(
                outcome=CaseOutcome(
                    case_id="case-1",
                    answerable=True,
                    tags=("tag",),
                    delivered_chunk_ids=("chunk-1",),
                    first_hit_rank=1,
                    stale_chunk_ids=(),
                ),
                target_version=PatchVersion.parse("0.1.0"),
            ),
        ),
    )


def _expected_name(run_at: datetime, git_commit: str | None, digest: str = "0" * 64) -> str:
    """Return the deterministic file name the writer must produce."""
    stamp = run_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = git_commit if git_commit is not None else "nogit"
    return f"{stamp}-{suffix}-{digest[:8]}.json"


class TestWrite:
    def test_writes_file_and_content(self, tmp_path: Path) -> None:
        run_at = datetime(2026, 11, 1, 9, 30, 0, tzinfo=UTC)
        report = _make_report(run_at, "6fb9b03")
        writer = JsonReportWriter()

        path = writer.write(report, tmp_path)

        assert path.parent == tmp_path
        assert path.name == _expected_name(run_at, "6fb9b03")
        assert json.loads(path.read_text(encoding="utf-8")) == report.as_dict()

    def test_creates_directory(self, tmp_path: Path) -> None:
        run_at = datetime(2026, 11, 1, 9, 30, 0, tzinfo=UTC)
        report = _make_report(run_at, "6fb9b03")
        directory = tmp_path / "nested" / "reports"

        path = JsonReportWriter().write(report, directory)

        assert path.parent == directory
        assert path.is_file()

    def test_filename_without_commit_uses_nogit(self, tmp_path: Path) -> None:
        run_at = datetime(2026, 11, 1, 9, 30, 0, tzinfo=UTC)
        report = _make_report(run_at, None)

        path = JsonReportWriter().write(report, tmp_path)

        assert path.name == _expected_name(run_at, None)

    def test_filename_converts_run_at_to_utc(self, tmp_path: Path) -> None:
        run_at = datetime(2026, 11, 1, 11, 30, 0, tzinfo=timezone(offset=timedelta(hours=2)))
        report = _make_report(run_at, None)

        path = JsonReportWriter().write(report, tmp_path)

        assert path.name == "20261101T093000Z-nogit-00000000.json"

    def test_overwriting_same_report_is_idempotent(self, tmp_path: Path) -> None:
        run_at = datetime(2026, 11, 1, 9, 30, 0, tzinfo=UTC)
        report = _make_report(run_at, "6fb9b03")
        writer = JsonReportWriter()

        first = writer.write(report, tmp_path)
        second = writer.write(report, tmp_path)

        assert first == second
        assert list(tmp_path.glob(".eval-*.tmp")) == []

    def test_failed_replace_raises_and_cleans_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_at = datetime(2026, 11, 1, 9, 30, 0, tzinfo=UTC)
        report = _make_report(run_at, "6fb9b03")
        expected_path = tmp_path / _expected_name(run_at, "6fb9b03")

        def fail_replace(source: object, target: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", fail_replace)

        with pytest.raises(ReportWriteError) as exc_info:
            JsonReportWriter().write(report, tmp_path)

        assert str(expected_path) in str(exc_info.value)
        assert list(tmp_path.glob(".eval-*.tmp")) == []

    def test_failed_mkstemp_raises_and_leaves_no_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_at = datetime(2026, 11, 1, 9, 30, 0, tzinfo=UTC)
        report = _make_report(run_at, "6fb9b03")
        expected_path = tmp_path / _expected_name(run_at, "6fb9b03")

        def fail_mkstemp(dir: str | None = None, prefix: str | None = None, suffix: str | None = None) -> Any:
            raise OSError("no space")

        monkeypatch.setattr("tempfile.mkstemp", fail_mkstemp)

        with pytest.raises(ReportWriteError) as exc_info:
            JsonReportWriter().write(report, tmp_path)

        assert str(expected_path) in str(exc_info.value)
        assert list(tmp_path.glob(".eval-*.tmp")) == []

    def test_a_commit_id_cannot_escape_the_runs_directory(self, tmp_path: Path) -> None:
        """Cheap guard: a report whose commit is not a single path segment writes nothing.

        `RunMetadata` already refuses such a value, so this builds a duck-typed report to keep the
        adapter's own guard covered instead of trusting the layer above.
        """
        report = _ReportDouble(
            run=_RunDouble(datetime(2026, 11, 1, 9, 30, tzinfo=UTC), "../escape"),
            dataset=_DatasetDouble("0" * 64),
        )

        with pytest.raises(ReportWriteError, match="commit id"):
            JsonReportWriter().write(report, tmp_path / "runs")  # type: ignore[arg-type]

        assert list(tmp_path.iterdir()) == []

    def test_a_commit_that_cannot_name_a_file_is_rejected_before_it_is_written(
        self, tmp_path: Path
    ) -> None:
        """The application refuses the value, so a run never dies after retrieving every case."""
        with pytest.raises(InvalidReportError, match="git_commit"):
            RunMetadata(REPORT_AT, "../escape")

        assert list(tmp_path.iterdir()) == []

    def test_two_datasets_in_the_same_second_do_not_overwrite_each_other(
        self, tmp_path: Path
    ) -> None:
        """The digest prefix keeps two runs apart; only a re-run of the same dataset overwrites."""
        first = _make_report(REPORT_AT, "6fb9b03")
        second = EvaluationReport(
            run=first.run,
            dataset=DatasetInfo(Path("other.jsonl"), "b" * 64, cases=1, answerable=1),
            config=first.config,
            metrics=first.metrics,
            by_tag=first.by_tag,
            cases=first.cases,
        )

        first_path = JsonReportWriter().write(first, tmp_path)
        second_path = JsonReportWriter().write(second, tmp_path)

        assert first_path != second_path
        assert len(sorted(tmp_path.glob("*.json"))) == 2
