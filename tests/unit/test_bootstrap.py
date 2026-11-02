"""The composition root: one frozen config in, the three use cases out (S2).

The suite proves the two promises that make the container safe to build on every command: nothing
heavy runs until a use case asks for it (no torch model, no loaded scorer, no SDK client), and the
generation half is reachable only with a key, while ingest never needs one. The fakes here are the
smallest shapes the wired adapters validate, not stand-ins for the pipeline.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from poe2_rag.answering.adapters.outbound.dense import bge_embedder
from poe2_rag.answering.adapters.outbound.rerank import cross_encoder_reranker
from poe2_rag.answering.application import AnswerQuestion, LanguageModel
from poe2_rag.answering.domain.errors import InvalidQueryError
from poe2_rag.bootstrap import DEFAULT_RETRIEVAL_SETTINGS, EXPECTED_ERRORS, Container
from poe2_rag.config import AppConfig, MissingApiKeyError
from poe2_rag.ingestion.adapters.outbound.http import (
    HttpFetchError,
    MissingHttpContactError,
    PoliteHttpFetcher,
)
from poe2_rag.ingestion.adapters.outbound.poe2db import (
    Poe2dbGemPageParser,
    Poe2dbParseError,
)
from poe2_rag.ingestion.application import (
    EmptyCorpusError,
    IngestSource,
    LexicalIndexWriter,
    ReindexCorpus,
    VectorIndexWriter,
)

CLI_FIXTURES = Path(__file__).parents[1] / "fixtures" / "cli"
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
    """`ReindexCorpus` writes and `AnswerQuestion` reads through the container's own instances.

    The claim is about the collaborators the two use cases were built with, so the case reads those
    slots. Asserting `container.embedder is container.embedder` would only prove that a property
    memoizes: a `ReindexCorpus` built with its own `BgeEmbedder(...)` would still pass.
    """
    container = build(config)

    reindex = container.reindex_corpus
    answer = container.answer_question

    assert reindex._document_embedder is container.embedder
    assert reindex._lexical_writer is container.lexical_index
    assert reindex._vector_writer is container.vector_index
    assert answer._embedder is container.embedder
    assert answer._lexical_index is container.lexical_index
    assert answer._vector_index is container.vector_index
    assert answer._language_model is container.language_model
    assert reindex is container.reindex_corpus and answer is container.answer_question


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


def test_the_retrieval_budget_pins_the_mvp_numbers() -> None:
    """The budgets are a documented policy choice, so every number is pinned here.

    A monotonicity assertion would be tautological: `RetrievalSettings.__post_init__` already refuses
    `final_top_k > rerank_top_m`, so a violated value could not even be imported.
    """
    settings = DEFAULT_RETRIEVAL_SETTINGS

    assert (settings.lexical_top_n, settings.dense_top_n) == (50, 50)
    assert (settings.rerank_top_m, settings.final_top_k) == (20, 5)


def test_the_expected_errors_are_the_operational_families_only() -> None:
    """The boundary's contract is a selective list, never a base class that would swallow bugs."""
    assert RuntimeError not in EXPECTED_ERRORS
    assert ValueError not in EXPECTED_ERRORS
    assert OSError not in EXPECTED_ERRORS
    for family in (
        MissingApiKeyError,
        EmptyCorpusError,
        InvalidQueryError,
        HttpFetchError,
        Poe2dbParseError,
    ):
        assert issubclass(family, EXPECTED_ERRORS)


def test_a_relative_locator_resolves_against_the_raw_directory(
    config: AppConfig, no_model_load: None
) -> None:
    """`POE2_RAG_RAW_DIR` is a real knob, not a documented no-op.

    `LocalFileFetcher` reads what it is handed, relative to the working directory; the composition root
    resolves a relative locator against the configured raw directory, so a snapshot name works from any
    directory and the report echoes the path that was actually read.
    """
    config.raw_dir.mkdir(parents=True, exist_ok=True)
    snapshot = config.raw_dir / "patch-notes-0.1.1.html"
    shutil.copyfile(CLI_FIXTURES / "patch-notes-0.1.1.html", snapshot)
    snapshot.with_name(snapshot.name + ".meta.json").write_text(
        json.dumps({"captured_at": "2026-10-30T12:00:00+00:00"}), encoding="utf-8"
    )

    report = build(config).ingest_source.execute("patch-notes-0.1.1.html")

    # The report echoes the locator as the operator wrote it; the run only succeeds because the
    # snapshot was found under `raw_dir`, since no file of that name exists in the working directory.
    assert report.locator == "patch-notes-0.1.1.html"
    assert report.chunk_ids
    assert snapshot.is_file()
    assert not Path("patch-notes-0.1.1.html").exists()


def test_no_field_of_the_container_retains_the_api_key(
    config: AppConfig, no_model_load: None
) -> None:
    """The environment must not be kept, only the fact that the key was there.

    Keeping the mapping would put the credential in an attribute of the object every command holds,
    one `repr`, one log line or one traceback frame away from a terminal. The container therefore
    reduces the environment to a presence flag while it is being built.
    """
    container = build(config, environ={"ANTHROPIC_API_KEY": API_KEY})
    _ = container.answer_question

    for slot in Container.__slots__:
        assert API_KEY not in repr(getattr(container, slot)), slot


def _with_contact(config: AppConfig) -> AppConfig:
    """The poe2db pair refuses to build without a contact; tests give it a harmless one."""
    return replace(config, http_contact="ops@example.com")


def test_poe2db_ingest_is_built_lazily_from_its_own_fetcher_and_parser(
    config: AppConfig, no_model_load: None
) -> None:
    """One refresh-aware runner per cache policy, sharing the corpus and the catalog."""
    container = build(_with_contact(config))

    runner = container.poe2db_ingest_source(refresh=False)

    assert isinstance(runner, IngestSource)
    assert isinstance(runner._fetcher, PoliteHttpFetcher)
    assert isinstance(runner._parser, Poe2dbGemPageParser)
    assert runner._repository is container.chunk_repository
    assert runner._catalog is container.patch_catalog
    assert runner is container.poe2db_ingest_source(refresh=False)


def test_refresh_selects_a_distinct_runner(config: AppConfig, no_model_load: None) -> None:
    container = build(_with_contact(config))

    cached = container.poe2db_ingest_source(refresh=False)
    refreshed = container.poe2db_ingest_source(refresh=True)

    assert cached is not refreshed
    cached_fetcher = cast(PoliteHttpFetcher, cached._fetcher)
    refreshed_fetcher = cast(PoliteHttpFetcher, refreshed._fetcher)
    assert cached_fetcher._refresh is False
    assert refreshed_fetcher._refresh is True


def test_the_poe2db_fetcher_receives_the_configured_http_settings(
    config: AppConfig, no_model_load: None
) -> None:
    configured = replace(
        _with_contact(config), http_request_interval=2.5, http_timeout=15.0
    )

    runner = build(configured).poe2db_ingest_source(refresh=False)
    fetcher = cast(PoliteHttpFetcher, runner._fetcher)

    assert fetcher._request_interval == 2.5
    assert fetcher._timeout == 15.0
    assert fetcher._cache_dir == configured.raw_dir
    assert "ops@example.com" in fetcher._user_agent()


def test_a_missing_contact_fails_when_the_poe2db_runner_is_asked_for(
    config: AppConfig, no_model_load: None
) -> None:
    """The read path and patch-notes keep working without a contact; fetching does not."""
    container = build(config)

    assert isinstance(container.ingest_source, IngestSource)
    with pytest.raises(MissingHttpContactError) as raised:
        container.poe2db_ingest_source(refresh=False)

    assert "POE2_RAG_HTTP_CONTACT" in str(raised.value)


def test_building_the_poe2db_runner_makes_no_request(
    config: AppConfig, no_model_load: None
) -> None:
    """Lazily means lazy: wiring the fetcher must not touch the network."""
    requests: list[object] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        raise AssertionError("the runner made a request while being built")

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    container = Container(_with_contact(config), environ={}, http_client=client)

    runner = container.poe2db_ingest_source(refresh=False)

    assert isinstance(runner, IngestSource)
    assert requests == []
