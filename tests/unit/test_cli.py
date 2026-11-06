"""Dispatch and error-contract tests for the top-level CLI (S5, S6).

Every test injects a fake container, so the suite runs offline and never imports the
composition root unless the test is specifically exercising the default factory.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from cli_fakes import FailingContainer, FakeContainer, FakeEvaluationRunner

from poe2_rag.answering.adapters.inbound.cli import AnswerQuestionRunner
from poe2_rag.answering.adapters.outbound.dense.chroma_index import (
    VectorIndexWriteError,
)
from poe2_rag.answering.adapters.outbound.lexical.bm25_index import (
    LexicalIndexWriteError,
)
from poe2_rag.answering.adapters.outbound.llm.claude_language_model import (
    LanguageModelRefusedError,
)
from poe2_rag.answering.domain.errors import InvalidQueryError
from poe2_rag.cli import main
from poe2_rag.config import ConfigurationError, MissingApiKeyError
from poe2_rag.evaluation.application import (
    DatasetDriftError,
    EvaluationRequest,
    InvalidEvaluationRequestError,
)
from poe2_rag.ingestion.adapters.inbound.cli import (
    IngestSourceRunner,
    ReindexCorpusRunner,
)
from poe2_rag.ingestion.application import EmptyCorpusError
from poe2_rag.shared.domain import PatchVersion

THREAD_URL = "https://www.pathofexile.com/forum/view-thread/4009785"


class EvalOnlyContainer:
    """A container whose only working runner is the evaluation one.

    `eval` must not reach the other three capabilities, so each of them explodes: a command that asks
    for one fails instead of passing by inertia. Providing them (instead of omitting them) is what
    keeps the fake assignable to `CliContainer` without a cast.
    """

    expected_errors = FakeContainer.expected_errors

    def __init__(self, evaluation: FakeEvaluationRunner | None = None) -> None:
        self.evaluation = evaluation or FakeEvaluationRunner()

    def ingest_source(  # pragma: no cover - must not be reached
        self, *, source: str, refresh: bool
    ) -> IngestSourceRunner:
        raise AssertionError("`eval` must not ask for the ingest runner")

    @property
    def reindex_corpus(self) -> ReindexCorpusRunner:  # pragma: no cover - must not be reached
        raise AssertionError("`eval` must not ask for the reindex runner")

    @property
    def answer_question(self) -> AnswerQuestionRunner:  # pragma: no cover - must not be reached
        raise AssertionError("`eval` must not ask for the generation runner")


def test_ingest_runs_source_and_reindex_with_locator() -> None:
    """``ingest <source> <path>`` returns 0 and calls both use cases once."""
    fake = FakeContainer()
    locator = "data/raw/x.html"

    result = main(["ingest", "patch-notes", locator], container=fake)

    assert result == 0
    assert fake.ingest.calls == [locator]
    assert fake.ingest_requests == [("patch-notes", False)]
    assert len(fake.reindex_corpus.calls) == 1


def test_ask_with_patch_passes_version() -> None:
    """``ask --patch <version>`` forwards a parsed ``PatchVersion`` to the runner."""
    fake = FakeContainer()

    result = main(["ask", "How much mana?", "--patch", "0.1.0"], container=fake)

    assert result == 0
    assert fake.answer_question.calls == [("How much mana?", PatchVersion(0, 1, 0))]


def test_ask_without_patch_passes_none() -> None:
    """Without ``--patch`` the CLI passes ``None`` and lets the use case default apply."""
    fake = FakeContainer()

    result = main(["ask", "How much mana?"], container=fake)

    assert result == 0
    assert fake.answer_question.calls == [("How much mana?", None)]


def test_a_malformed_patch_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """`--patch 0.1` is the user mistyping the command, so argparse exits 2."""
    with pytest.raises(SystemExit) as exc_info:
        main(["ask", "q", "--patch", "0.1"], container=FakeContainer())

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "argument --patch" in captured.err
    assert "0.1" in captured.err


def test_a_well_formed_patch_reaches_the_runner_without_being_resolved() -> None:
    """argparse checks the shape only; whether the catalog knows the version is the use case's call.

    The fake accepts `9.9.9`, which is the point: the boundary must not resolve patches. What the real
    path does with an unknown version is pinned separately, by
    `test_a_rejected_question_is_one_line_through_the_real_use_case`.
    """
    fake = FakeContainer()

    assert main(["ask", "q", "--patch", "9.9.9"], container=fake) == 0
    assert fake.answer_question.calls[-1] == ("q", PatchVersion(9, 9, 9))


def test_ingest_help_shows_the_closed_source_set(capsys: pytest.CaptureFixture[str]) -> None:
    """The closed choice of S3 has to be discoverable from the CLI, not only from the README."""
    with pytest.raises(SystemExit) as exc_info:
        main(["ingest", "--help"])

    assert exc_info.value.code == 0
    assert "{patch-notes,poe2db}" in capsys.readouterr().out


def test_importing_the_cli_pulls_in_no_adapter_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0006: the boundary receives a container, so importing it loads no adapter library.

    A subprocess is the honest way to assert this: the suite itself has long since imported chromadb and
    the SDK, so `sys.modules` here says nothing about what the module alone drags in.
    """
    heavy = (
        "'chromadb', 'anthropic', 'torch', 'bs4',"
        "'poe2_rag.ingestion.adapters.outbound.patch_notes_parser',"
        "'poe2_rag.ingestion.adapters.outbound.http.polite_http_fetcher',"
        "'poe2_rag.ingestion.adapters.outbound.local_file_fetcher'"
    )
    code = f"import sys, poe2_rag.cli;print(sorted(m for m in ({heavy}) if m in sys.modules))"

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_unknown_source_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown ``source`` choice is rejected by argparse with exit code 2."""
    fake = FakeContainer()

    with pytest.raises(SystemExit) as exc_info:
        main(["ingest", "poe2wiki", "x"], container=fake)

    assert exc_info.value.code == 2
    assert "poe2wiki" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "refresh"),
    [
        (["ingest", "poe2db", "https://poe2db.tw/us/Spark"], False),
        (["ingest", "poe2db", "https://poe2db.tw/us/Spark", "--refresh"], True),
    ],
    ids=["cached", "refresh"],
)
def test_poe2db_asks_the_container_for_its_runner_and_reindexes(
    argv: list[str], refresh: bool
) -> None:
    """`--refresh` selects a fetcher that ignores the cache; the source decides which runner runs."""
    container = FakeContainer()

    assert main(argv, container=container) == 0
    assert container.ingest_requests == [("poe2db", refresh)]
    assert container.ingest.calls == ["https://poe2db.tw/us/Spark"]
    assert len(container.reindex_corpus.calls) == 1


def test_refresh_is_a_usage_error_for_a_local_snapshot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--refresh` means "fetch again"; a local snapshot has no cache to bypass."""
    container = FakeContainer()

    with pytest.raises(SystemExit) as exc_info:
        main(["ingest", "patch-notes", "x.html", "--refresh"], container=container)

    assert exc_info.value.code == 2
    stderr = capsys.readouterr().err
    assert "--refresh" in stderr
    assert "patch-notes URLs" in stderr
    assert "usage: poe2-rag ingest" in stderr
    assert container.ingest_requests == []


def test_refresh_is_accepted_for_a_patch_notes_url() -> None:
    """A thread URL has a cache, so the flag reaches the container instead of argparse."""
    container = FakeContainer()

    assert main(["ingest", "patch-notes", THREAD_URL, "--refresh"], container=container) == 0
    assert container.ingest_requests == [("patch-notes", True)]
    assert container.ingest.calls == [THREAD_URL]


def test_patch_notes_without_refresh_uses_the_same_entry_point() -> None:
    container = FakeContainer()

    assert main(["ingest", "patch-notes", "x.html"], container=container) == 0
    assert container.ingest_requests == [("patch-notes", False)]
    assert container.ingest.calls == ["x.html"]


@pytest.mark.parametrize(
    ("argv", "locator"),
    [
        (["ingest", "poe2db", "data/raw/patch-notes-0.1.1.html"], "data/raw/patch-notes-0.1.1.html"),
        (["ingest", "patch-notes", "ftp://x"], "ftp://x"),
        (["ingest", "patch-notes", "ftp://x", "--refresh"], "ftp://x"),
    ],
    ids=["poe2db-path", "other-scheme", "other-scheme-refresh"],
)
def test_a_locator_outside_the_allow_list_is_one_line_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str], locator: str
) -> None:
    """The real container and the real fetcher: the policy rejects before any request.

    `ftp://x` reaches the HTTP branch with *and* without `--refresh`, which is why the guard cannot
    treat every non-`path` locator as a usage error: both cases must report the same fetcher error.
    """
    import cli_e2e

    from poe2_rag.bootstrap import Container

    container = Container(cli_e2e.integration_config(tmp_path), environ={})

    assert main(argv, container=container) == 1
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0] == f"error: only https URLs are allowed, got '{locator}'"
    assert captured.out == ""


def test_an_unknown_source_cannot_reach_a_runner(tmp_path: Path) -> None:
    """The choices argparse publishes and the container's builders are one closed set."""
    import cli_e2e

    from poe2_rag.bootstrap import Container

    container = Container(cli_e2e.integration_config(tmp_path), environ={})

    with pytest.raises(ValueError, match="unknown source: 'poe2wiki'"):
        container.ingest_source(source="poe2wiki", refresh=False)


def test_missing_api_key_prints_one_line_and_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``MissingApiKeyError`` is caught, printed as one ``error:`` line and returns 1."""
    container = FailingContainer(
        answer_question=MissingApiKeyError(
            "ANTHROPIC_API_KEY is not set. Export it, or pass it with "
            "`uv run --env-file .env ...` (see .env.example)."
        )
    )

    result = main(["ask", "q"], container=container)

    assert result == 1
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: ")
    assert "ANTHROPIC_API_KEY" in lines[0]
    assert "Traceback" not in captured.err


def test_llm_refusal_prints_one_line_and_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``LanguageModelRefusedError`` is caught and its message printed as one line."""
    container = FailingContainer(
        answer_question=LanguageModelRefusedError(
            "the model declined to answer", stop_details_category="policy"
        )
    )

    result = main(["ask", "q"], container=container)

    assert result == 1
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: ")
    assert "the model declined to answer" in lines[0]


def test_ingest_failure_prints_one_line_and_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An ingestion failure is caught at the boundary and printed as one line."""
    container = FailingContainer(
        ingest_source=EmptyCorpusError("cannot reindex an empty corpus: no chunk to index")
    )

    result = main(["ingest", "patch-notes", "data/raw/x.html"], container=container)

    assert result == 1
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: ")
    assert "cannot reindex an empty corpus" in lines[0]


def test_unexpected_exception_propagates() -> None:
    """Exceptions outside the expected set are not swallowed by the CLI boundary."""
    container = FailingContainer(answer_question=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        main(["ask", "q"], container=container)


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """``--help`` is handled by argparse and exits 0."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_default_container_is_used_only_when_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--help`` must not build the default container; ``ask`` must build it exactly once."""
    calls: list[object] = []

    def default() -> FakeContainer:
        calls.append(())
        return FakeContainer()

    import poe2_rag.cli as cli_module

    monkeypatch.setattr(cli_module, "_default_container", default)

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    assert len(calls) == 0

    result = main(["ask", "q"])
    assert result == 0
    assert len(calls) == 1


def test_injected_container_never_uses_default_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a container is injected the default factory is never invoked."""
    import poe2_rag.cli as cli_module

    def explode() -> FakeContainer:  # pragma: no cover - must not run
        raise AssertionError("default factory should not be called")

    monkeypatch.setattr(cli_module, "_default_container", explode)
    fake = FakeContainer()

    result = main(["ask", "q"], container=fake)

    assert result == 0
    assert fake.answer_question.calls == [("q", None)]


def test_a_configuration_error_from_the_default_factory_obeys_the_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad `POE2_RAG_*` value is a command failure, not a crash on the way into the command.

    The default container reads the environment, so it is built inside the same handler as the
    command: a value it rejects has to come back as one line and exit 1 like any other expected error.
    """
    import poe2_rag.cli as cli_module

    def broken() -> FakeContainer:
        raise ConfigurationError("POE2_RAG_LLM_MAX_TOKENS must be a positive integer, got 'zero'")

    monkeypatch.setattr(cli_module, "_default_container", broken)

    assert main(["ingest", "patch-notes", "x.html"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "POE2_RAG_LLM_MAX_TOKENS" in captured.err
    assert "Traceback" not in captured.err


def test_an_invalid_query_prints_one_line_and_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A question or a patch the use case rejects is a user error, not a crash.

    `InvalidQueryError` is the one domain error a user provokes with input (a blank question, or a
    well-formed version the catalog does not know), so it belongs to the same contract as any other
    expected failure: one line, exit 1, no traceback.
    """
    container = FailingContainer(
        answer_question=InvalidQueryError("version 9.9.9 is not a known patch")
    )

    assert main(["ask", "What changed?", "--patch", "9.9.9"], container=container) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "9.9.9 is not a known patch" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "failure",
    [
        LexicalIndexWriteError("disk full while replacing bm25.json"),
        VectorIndexWriteError("disk full while swapping the collection"),
    ],
    ids=["lexical", "dense"],
)
def test_an_index_write_failure_prints_one_line_and_exits_one(
    capsys: pytest.CaptureFixture[str], failure: Exception
) -> None:
    """`ingest` rebuilds both indexes, so a failed rebuild is an expected failure of the command."""
    container = FailingContainer(reindex_corpus=failure)

    assert main(["ingest", "patch-notes", "data/raw/x.html"], container=container) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert str(failure) in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "argv",
    [
        ["ask", "What changed?", "--patch", "9.9.9"],
        ["ask", "   "],
    ],
    ids=["unknown-patch", "blank-question"],
)
def test_a_rejected_question_is_one_line_through_the_real_use_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """The end-to-end path the verifier reproduced: real use case, real catalog, no model loaded.

    `Query.for_catalog` rejects the question before the embedder, either index or the reranker is
    called, so this case also proves the boundary reports it instead of leaking a traceback.
    """
    import cli_e2e

    from poe2_rag.answering.adapters.outbound.dense import bge_embedder
    from poe2_rag.answering.adapters.outbound.rerank import cross_encoder_reranker
    from poe2_rag.bootstrap import Container

    def explode(*_: object, **__: object) -> object:
        raise AssertionError("a rejected question must not load a model")

    monkeypatch.setattr(bge_embedder, "_load_model", explode)
    monkeypatch.setattr(cross_encoder_reranker, "_load_scorer", explode)

    container = Container(
        cli_e2e.build_config(tmp_path),
        environ={"ANTHROPIC_API_KEY": "sk-ant-not-a-real-key"},
        client_factory=_stub_client,
    )

    assert main(argv, container=container) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert captured.err.count("\n") == 1
    assert "Traceback" not in captured.err


class _StubClient:
    """The only shape `ClaudeLanguageModel` validates, so the container wires without the SDK."""

    beta = object()


def _stub_client() -> Any:
    """Build the stub through a factory, the way the composition root asks for a client."""
    return _StubClient()


def test_eval_dispatches_the_parsed_options() -> None:
    """`--dataset`, `--k` and `--output-dir` reach the use case as one request, in K order."""
    container = EvalOnlyContainer()

    result = main(
        ["eval", "--dataset", "eval/other.jsonl", "--k", "5,1", "--output-dir", "runs"],
        container=container,
    )

    assert result == 0
    assert container.evaluation.requests == [
        EvaluationRequest(
            dataset=Path("eval/other.jsonl"), ks=(1, 5), output_dir=Path("runs")
        )
    ]


def test_eval_without_options_leaves_the_defaults_to_the_wiring() -> None:
    """The command passes `None` through: the configured dataset and runs directory win."""
    container = EvalOnlyContainer()

    assert main(["eval"], container=container) == 0
    request = container.evaluation.requests[0]
    assert request.dataset is None
    assert request.output_dir is None
    assert request.ks == (1, 3, 5)


@pytest.mark.parametrize(
    "raw",
    ["0", "-1", "1,one", "1,1", "", "1_0", "+5", "1.0"],
    ids=["zero", "negative", "word", "repeated", "blank", "underscore", "sign", "float"],
)
def test_a_malformed_k_is_a_usage_error(raw: str, capsys: pytest.CaptureFixture[str]) -> None:
    """A K that cannot produce a metric column is the user mistyping the command: exit 2."""
    container = EvalOnlyContainer()

    with pytest.raises(SystemExit) as exc_info:
        main(["eval", "--k", raw], container=container)

    assert exc_info.value.code == 2
    assert "K" in capsys.readouterr().err
    assert container.evaluation.requests == []


def test_eval_help_lists_its_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["eval", "--help"])

    assert exc_info.value.code == 0
    printed = capsys.readouterr().out
    assert "usage: poe2-rag eval" in printed
    assert "--dataset" in printed
    assert "--k" in printed
    assert "--output-dir" in printed


def test_an_evaluation_failure_prints_one_line_and_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A drifted dataset is an expected failure of the command: one line, exit 1, no traceback."""
    container = EvalOnlyContainer(
        FakeEvaluationRunner(
            DatasetDriftError(
                "eval/dataset.jsonl names 1 chunk id(s) the corpus does not have: poe2db:x"
            )
        )
    )

    assert main(["eval"], container=container) == 1
    captured = capsys.readouterr()
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("error: ")
    assert "poe2db:x" in lines[0]
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_eval_never_asks_the_generation_half(capsys: pytest.CaptureFixture[str]) -> None:
    """S7: the command runs against retrieval only, so no credential and no client are needed."""
    container = EvalOnlyContainer()

    assert main(["eval"], container=container) == 0
    assert "Retrieval evaluation: 1 cases (1 answerable, 0 unanswerable)" in capsys.readouterr().out


def test_a_k_beyond_the_delivered_top_k_prints_one_line_and_exits_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only the use case knows `final_top_k`, so an oversized K is an expected failure, not usage."""
    container = EvalOnlyContainer(
        FakeEvaluationRunner(InvalidEvaluationRequestError("K 10 is beyond the 5 chunks"))
    )

    assert main(["eval", "--k", "10"], container=container) == 1
    captured = capsys.readouterr()
    assert len(captured.err.splitlines()) == 1
    assert captured.err.startswith("error: ")
    assert "beyond the 5 chunks" in captured.err
    assert captured.out == ""
