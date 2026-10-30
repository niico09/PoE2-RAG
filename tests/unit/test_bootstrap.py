"""The composition root: one frozen config in, the three use cases out (S2).

The suite proves the two promises that make the container safe to build on every command: nothing
heavy runs until a use case asks for it (no torch model, no loaded scorer, no SDK client), and the
generation half is reachable only with a key, while ingest never needs one. The fakes here are the
smallest shapes the wired adapters validate, not stand-ins for the pipeline.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from poe2_rag.answering.adapters.outbound.dense import bge_embedder
from poe2_rag.answering.adapters.outbound.rerank import cross_encoder_reranker
from poe2_rag.answering.application import AnswerQuestion, LanguageModel
from poe2_rag.bootstrap import DEFAULT_RETRIEVAL_SETTINGS, Container
from poe2_rag.config import AppConfig, MissingApiKeyError
from poe2_rag.ingestion.application import (
    IngestSource,
    LexicalIndexWriter,
    ReindexCorpus,
    VectorIndexWriter,
)

HEAVY_MODULES = ("torch", "sentence_transformers")
API_KEY = "sk-ant-not-a-real-key"


class FakeAnthropicClient:
    """The only shape `ClaudeLanguageModel` validates: a client exposing a beta resource."""

    beta = object()


class FakeLanguageModel:
    """A `LanguageModel` port implementation; unusable, only identity is asserted."""

    def generate(self, question: str, context: Any) -> Any:  # pragma: no cover - never called
        raise AssertionError("the fake is injected, not exercised")


class CountingFactory:
    """A client factory that records how many clients were built."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        return FakeAnthropicClient()


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    """A configuration pointing every artifact at an isolated directory."""
    return AppConfig(
        project_root=tmp_path,
        raw_dir=tmp_path / "raw",
        processed_dir=tmp_path / "processed",
        index_dir=tmp_path / "index",
        patch_catalog_path=_write_catalog(tmp_path),
        embedder_model_id="BAAI/bge-base-en-v1.5",
        reranker_model_id="BAAI/bge-reranker-base",
        llm_model="claude-sonnet-5-5",
        llm_max_tokens=1024,
        llm_effort="medium",
    )


@pytest.fixture
def no_model_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything loads the embedding model or the cross-encoder."""

    def explode(*_: object, **__: object) -> Any:
        raise AssertionError("the container loaded a model while being built")

    monkeypatch.setattr(bge_embedder, "_load_model", explode)
    monkeypatch.setattr(cross_encoder_reranker, "_load_scorer", explode)


def _write_catalog(root: Path) -> Path:
    """Write a two-patch catalog, the smallest file `TomlPatchCatalog` accepts."""
    path = root / "patches.toml"
    path.write_text(
        'current = "0.1.1"\n\n'
        '[[patches]]\nversion = "0.1.0"\nreleased_on = 2024-12-06\n\n'
        '[[patches]]\nversion = "0.1.1"\nreleased_on = 2025-01-17\n',
        encoding="utf-8",
    )
    return path


def build(
    config: AppConfig,
    *,
    environ: dict[str, str] | None = None,
    client_factory: Callable[[], Any] | None = None,
    language_model: LanguageModel | None = None,
) -> Container:
    """Build a container with a fake SDK client unless the case is about the real factory."""
    return Container(
        config,
        environ={"ANTHROPIC_API_KEY": API_KEY} if environ is None else environ,
        client_factory=CountingFactory() if client_factory is None else client_factory,
        language_model=language_model,
    )


def test_the_container_builds_the_three_use_cases(
    config: AppConfig, no_model_load: None
) -> None:
    container = build(config)

    assert isinstance(container.ingest_source, IngestSource)
    assert isinstance(container.reindex_corpus, ReindexCorpus)
    assert isinstance(container.answer_question, AnswerQuestion)


def test_building_the_container_loads_no_model_and_imports_no_heavy_package(
    config: AppConfig, no_model_load: None
) -> None:
    """The loaders raise, so a passing case proves the models were never touched."""
    already_imported = {name for name in HEAVY_MODULES if name in sys.modules}

    container = build(config)
    for component in (
        container.ingest_source,
        container.reindex_corpus,
        container.answer_question,
    ):
        assert component is not None

    assert {name for name in HEAVY_MODULES if name in sys.modules} == already_imported


def test_the_two_halves_share_one_adapter_per_port(config: AppConfig, no_model_load: None) -> None:
    """`ReindexCorpus` writes and `AnswerQuestion` reads through the same instances."""
    container = build(config)

    assert container.embedder is container.embedder
    assert container.lexical_index is container.lexical_index
    assert container.vector_index is container.vector_index
    assert container.reindex_corpus is container.reindex_corpus
    assert container.answer_question is container.answer_question


def test_the_container_wires_the_configured_paths(config: AppConfig, no_model_load: None) -> None:
    container = build(config)

    assert container.config is config
    assert container.chunk_repository.path == config.processed_dir / "chunks.json"
    assert container.lexical_index.path == config.index_dir / "bm25.json"
    assert container.patch_catalog.current() == container.patch_catalog.known()[-1]


def test_ingest_needs_no_api_key(config: AppConfig, no_model_load: None) -> None:
    """Writing the corpus and the indexes never reaches the generation stage."""
    factory = CountingFactory()
    container = build(config, environ={}, client_factory=factory)

    assert container.reindex_corpus is not None
    assert isinstance(container.lexical_index, LexicalIndexWriter)
    assert isinstance(container.vector_index, VectorIndexWriter)
    assert factory.calls == 0


@pytest.mark.parametrize("environ", [{}, {"ANTHROPIC_API_KEY": "   "}])
def test_a_missing_key_fails_before_any_retrieval_work(
    config: AppConfig, no_model_load: None, environ: dict[str, str]
) -> None:
    factory = CountingFactory()
    container = build(config, environ=environ, client_factory=factory)

    with pytest.raises(MissingApiKeyError) as raised:
        _ = container.answer_question

    message = str(raised.value)
    assert "ANTHROPIC_API_KEY" in message
    assert API_KEY not in message
    assert factory.calls == 0


def test_the_key_comes_from_the_process_environment_by_default(
    config: AppConfig, no_model_load: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY)
    container = Container(config, client_factory=CountingFactory())

    assert isinstance(container.answer_question, AnswerQuestion)


def test_the_sdk_client_is_built_once_and_only_when_answering(
    config: AppConfig, no_model_load: None
) -> None:
    factory = CountingFactory()
    container = build(config, client_factory=factory)

    assert factory.calls == 0
    first = container.answer_question
    assert container.answer_question is first
    assert factory.calls == 1


def test_the_client_factory_defaults_to_the_anthropic_sdk(
    config: AppConfig, no_model_load: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bootstrap` must not import the SDK itself; the default factory is the one that does."""
    from poe2_rag import bootstrap

    monkeypatch.setattr(bootstrap, "_default_anthropic_client", FakeAnthropicClient)
    container = Container(config, environ={"ANTHROPIC_API_KEY": API_KEY})

    assert isinstance(container.answer_question, AnswerQuestion)


def test_an_injected_language_model_replaces_the_sdk_entirely(
    config: AppConfig, no_model_load: None
) -> None:
    """The integration suite reads the real retrieval with a scripted generation (S8)."""
    factory = CountingFactory()
    model = FakeLanguageModel()
    container = build(config, environ={}, client_factory=factory, language_model=model)

    assert container.language_model is model
    assert container.answer_question is container.answer_question
    assert factory.calls == 0


def test_the_default_retrieval_budget_is_monotone() -> None:
    """The budgets are wiring, not policy: `final_top_k` may never exceed `rerank_top_m`."""
    assert DEFAULT_RETRIEVAL_SETTINGS.final_top_k <= DEFAULT_RETRIEVAL_SETTINGS.rerank_top_m
