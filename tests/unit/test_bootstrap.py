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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from poe2_rag.answering.adapters.outbound.dense import bge_embedder
from poe2_rag.answering.adapters.outbound.llm.claude_language_model import (
    ClaudeLanguageModel,
)
from poe2_rag.answering.adapters.outbound.llm.opencode_language_model import (
    OpenCodeLanguageModel,
)
from poe2_rag.answering.adapters.outbound.rerank import cross_encoder_reranker
from poe2_rag.answering.application import AnswerQuestion, LanguageModel
from poe2_rag.answering.domain.errors import InvalidQueryError
from poe2_rag.bootstrap import (
    DEFAULT_RETRIEVAL_SETTINGS,
    EXPECTED_ERRORS,
    FORUM_POLICY,
    Container,
)
from poe2_rag.config import (
    LLM_PROVIDERS,
    AppConfig,
    MissingApiKeyError,
)
from poe2_rag.evaluation.application import EvaluationError
from poe2_rag.ingestion.adapters.inbound.cli import ingest as ingest_cli
from poe2_rag.ingestion.adapters.outbound.http import (
    HttpFetchError,
    MissingHttpContactError,
    PoliteHttpFetcher,
)
from poe2_rag.ingestion.adapters.outbound.locator_fetching import LocatorFetcher
from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import (
    PatchNotesParser,
    UnsupportedSourceTypeError,
)
from poe2_rag.ingestion.adapters.outbound.poe2db import (
    Poe2dbGemPageParser,
    Poe2dbParseError,
)
from poe2_rag.ingestion.application import (
    EmptyCorpusError,
    IngestSource,
    LexicalIndexWriter,
    RawDocument,
    ReindexCorpus,
    VectorIndexWriter,
)
from poe2_rag.shared.domain import PatchNotReleasedError

CLI_FIXTURES = Path(__file__).parents[1] / "fixtures" / "cli"
SNAPSHOT_NAME = "patch-notes-0.1.1.html"
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

    assert isinstance(container.ingest_source(source="patch-notes", refresh=False), IngestSource)
    assert isinstance(container.reindex_corpus, ReindexCorpus)
    assert isinstance(container.answer_question, AnswerQuestion)


def test_building_the_container_loads_no_model_and_imports_no_heavy_package(
    config: AppConfig, no_model_load: None
) -> None:
    """The loaders raise, so a passing case proves the models were never touched."""
    already_imported = {name for name in HEAVY_MODULES if name in sys.modules}

    container = build(config)
    for component in (
        container.ingest_source(source="patch-notes", refresh=False),
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
    # `AnswerQuestion` delegates retrieval, so one `RetrieveContext` serves `ask` and `eval`.
    assert answer._retrieve is container.retrieve_context
    assert container.retrieve_context._embedder is container.embedder
    assert container.retrieve_context._lexical_index is container.lexical_index
    assert container.retrieve_context._vector_index is container.vector_index
    assert container.retrieve_context._reranker is container.reranker
    assert container.retrieve_context._catalog is container.patch_catalog
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
    container = build(
        replace(config, llm_provider="claude"),
        environ=environ,
        client_factory=factory,
    )

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
    container = Container(replace(config, llm_provider="claude"), client_factory=CountingFactory())

    assert isinstance(container.answer_question, AnswerQuestion)


def test_the_sdk_client_is_built_once_and_only_when_answering(
    config: AppConfig, no_model_load: None
) -> None:
    factory = CountingFactory()
    container = build(replace(config, llm_provider="claude"), client_factory=factory)

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
    container = Container(
        replace(config, llm_provider="claude"),
        environ={"ANTHROPIC_API_KEY": API_KEY},
    )

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
        PatchNotReleasedError,
        EvaluationError,
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

    runner = build(config).ingest_source(source="patch-notes", refresh=False)
    report = runner.execute("patch-notes-0.1.1.html")

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
    container = build(
        replace(config, llm_provider="claude"),
        environ={"ANTHROPIC_API_KEY": API_KEY},
    )
    _ = container.answer_question

    for slot in Container.__slots__:
        assert API_KEY not in repr(getattr(container, slot)), slot


def _with_contact(config: AppConfig) -> AppConfig:
    """Both HTTP branches refuse to fetch without a contact; tests give it a harmless one."""
    return replace(config, http_contact="ops@example.com")


def test_poe2db_ingest_is_built_lazily_from_its_own_fetcher_and_parser(
    config: AppConfig, no_model_load: None
) -> None:
    """One runner per cache policy, reached through the single source-aware entry point."""
    container = build(_with_contact(config))

    runner = cast(IngestSource, container.ingest_source(source="poe2db", refresh=False))

    assert isinstance(runner._fetcher, PoliteHttpFetcher)
    assert isinstance(runner._parser, Poe2dbGemPageParser)
    assert runner._repository is container.chunk_repository
    assert runner._catalog is container.patch_catalog
    assert runner is container.ingest_source(source="poe2db", refresh=False)


def test_refresh_selects_a_distinct_runner(config: AppConfig, no_model_load: None) -> None:
    container = build(_with_contact(config))

    cached = cast(IngestSource, container.ingest_source(source="poe2db", refresh=False))
    refreshed = cast(IngestSource, container.ingest_source(source="poe2db", refresh=True))

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

    runner = cast(IngestSource, build(configured).ingest_source(source="poe2db", refresh=False))
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

    assert isinstance(container.ingest_source(source="patch-notes", refresh=False), IngestSource)
    with pytest.raises(MissingHttpContactError) as raised:
        container.ingest_source(source="poe2db", refresh=False)

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

    runner = cast(IngestSource, container.ingest_source(source="poe2db", refresh=False))

    assert isinstance(runner._fetcher, PoliteHttpFetcher)
    assert requests == []


def test_the_evaluation_runner_shares_the_retrieval_half_and_the_corpus(
    config: AppConfig, no_model_load: None
) -> None:
    """`RunEvaluation` measures the same retrieval `ask` uses, over the same corpus (S1, D4)."""
    container = build(config)

    evaluation = container.evaluation

    assert evaluation._retriever is container.retrieve_context
    assert evaluation._corpus is container.chunk_repository
    assert evaluation._default_dataset_path == config.eval_dataset_path
    assert evaluation._default_output_dir == config.eval_runs_dir
    assert evaluation is container.evaluation


def test_evaluation_needs_no_api_key_and_builds_no_client(
    config: AppConfig, no_model_load: None
) -> None:
    """S7: `eval` measures retrieval only, so it never asks for the generation half."""
    factory = CountingFactory()
    container = build(config, environ={}, client_factory=factory)

    assert container.evaluation is not None
    assert factory.calls == 0


def test_evaluation_reports_no_commit_when_the_project_root_is_not_a_repository(
    config: AppConfig, no_model_load: None
) -> None:
    """The commit is read in the composition root and is optional: a report without one is valid."""
    container = build(config)

    assert container.evaluation._git_commit is None


def test_the_forum_policy_is_the_closed_thread_rule() -> None:
    """The second host this project may fetch is a rule, not a hostname check (S2)."""
    assert FORUM_POLICY.host == "www.pathofexile.com"
    assert FORUM_POLICY.path.prefix == "/forum/view-thread/"
    assert FORUM_POLICY.path.slug.fullmatch("4009785")
    assert not FORUM_POLICY.path.slug.fullmatch("not-a-number")
    assert FORUM_POLICY.cache_directory == "patch_notes"


def test_the_forum_policy_source_type_is_the_one_the_parser_accepts() -> None:
    """One literal for the kind: the policy cannot declare a source the forum parser refuses."""
    raw = RawDocument(
        locator="https://www.pathofexile.com/forum/view-thread/4009785",
        content=(CLI_FIXTURES / "patch-notes-0.1.1.html").read_text(encoding="utf-8"),
        fetched_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        source_type=FORUM_POLICY.source_type,
    )

    assert PatchNotesParser().parse(raw)
    with pytest.raises(UnsupportedSourceTypeError):
        PatchNotesParser().parse(replace(raw, source_type="other_source"))


@pytest.mark.parametrize("refresh", [False, True], ids=["cached", "refresh"])
@pytest.mark.parametrize("source", ingest_cli.SOURCES)
def test_every_published_source_resolves_to_a_runner(
    config: AppConfig, no_model_load: None, source: str, refresh: bool
) -> None:
    """The CLI's closed `choices` and the container's builders are one set, kept in step here."""
    container = build(_with_contact(config))

    runner = cast(IngestSource, container.ingest_source(source=source, refresh=refresh))

    assert isinstance(runner, IngestSource)
    assert runner._repository is container.chunk_repository
    assert runner._catalog is container.patch_catalog


def test_an_unknown_source_fails_closed(config: AppConfig, no_model_load: None) -> None:
    """No name outside the table can reach a runner, and the failure names the input."""
    container = build(_with_contact(config))

    with pytest.raises(ValueError, match="unknown source: 'poe2wiki'"):
        container.ingest_source(source="poe2wiki", refresh=False)


def test_the_opencode_provider_needs_no_api_key(
    config: AppConfig, no_model_load: None
) -> None:
    """The default provider is local; it must receive model and timeout from configuration."""
    configured = replace(
        config,
        llm_provider="opencode",
        llm_model="opencode-go/probe",
        llm_timeout=7.5,
    )
    container = build(configured, environ={})

    model = container.language_model
    assert isinstance(model, OpenCodeLanguageModel)
    assert model._model == "opencode-go/probe"
    assert model._timeout == 7.5


def test_the_claude_provider_builds_the_claude_adapter(
    config: AppConfig, no_model_load: None
) -> None:
    container = build(
        replace(config, llm_provider="claude"),
        environ={"ANTHROPIC_API_KEY": API_KEY},
    )

    assert isinstance(container.language_model, ClaudeLanguageModel)


def test_the_provider_builders_match_the_configured_providers() -> None:
    """The bootstrap mapping and the config set must be the same keys; otherwise one drifts."""
    from poe2_rag import bootstrap

    assert set(bootstrap._LLM_BUILDERS) == LLM_PROVIDERS


def test_the_patch_notes_runner_dispatches_by_locator(
    config: AppConfig, no_model_load: None
) -> None:
    """A local snapshot and a forum URL share one runner, one corpus and one catalog."""
    container = build(_with_contact(config))

    runner = cast(IngestSource, container.ingest_source(source="patch-notes", refresh=False))

    assert isinstance(runner._fetcher, LocatorFetcher)
    assert isinstance(runner._parser, PatchNotesParser)
    assert runner is container.ingest_source(source="patch-notes", refresh=False)
    assert runner is not container.ingest_source(source="patch-notes", refresh=True)


def test_a_local_snapshot_never_needs_the_http_contact(
    config: AppConfig, no_model_load: None
) -> None:
    """S1: a local path behaves exactly as before, including on a machine with no contact."""
    config.raw_dir.mkdir(parents=True, exist_ok=True)
    snapshot = config.raw_dir / SNAPSHOT_NAME
    shutil.copyfile(CLI_FIXTURES / SNAPSHOT_NAME, snapshot)
    snapshot.with_name(snapshot.name + ".meta.json").write_text(
        json.dumps({"captured_at": "2026-10-30T12:00:00+00:00"}), encoding="utf-8"
    )
    runner = build(config).ingest_source(source="patch-notes", refresh=False)

    assert runner.execute(SNAPSHOT_NAME).chunk_ids

    with pytest.raises(MissingHttpContactError) as raised:
        runner.execute("https://www.pathofexile.com/forum/view-thread/4009785")

    assert "POE2_RAG_HTTP_CONTACT" in str(raised.value)
