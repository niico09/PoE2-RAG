"""Dispatch and error-contract tests for the top-level CLI (S5, S6).

Every test injects a fake container, so the suite runs offline and never imports the
composition root unless the test is specifically exercising the default factory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cli_fakes import FailingContainer, FakeContainer

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
from poe2_rag.ingestion.application import EmptyCorpusError
from poe2_rag.shared.domain import PatchVersion


def test_ingest_runs_source_and_reindex_with_locator() -> None:
    """``ingest <source> <path>`` returns 0 and calls both use cases once."""
    fake = FakeContainer()
    locator = "data/raw/x.html"

    result = main(["ingest", "patch-notes", locator], container=fake)

    assert result == 0
    assert fake.ingest_source.calls == [locator]
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


def test_invalid_patch_is_usage_error_and_unknown_version_reaches_fake(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A malformed patch string exits 2; a well-formed unknown patch reaches the runner."""
    fake = FakeContainer()

    with pytest.raises(SystemExit) as exc_info:
        main(["ask", "q", "--patch", "0.1"], container=fake)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "argument --patch" in captured.err
    assert "0.1" in captured.err

    result = main(["ask", "q", "--patch", "9.9.9"], container=fake)
    assert result == 0
    assert fake.answer_question.calls[-1] == ("q", PatchVersion(9, 9, 9))


def test_unknown_source_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown ``source`` choice is rejected by argparse with exit code 2."""
    fake = FakeContainer()

    with pytest.raises(SystemExit) as exc_info:
        main(["ingest", "poe2db", "x"], container=fake)

    assert exc_info.value.code == 2
    assert "poe2db" in capsys.readouterr().err


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
