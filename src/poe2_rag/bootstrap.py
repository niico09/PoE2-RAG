"""Composition root: one frozen config in, every adapter and use case out (§7).

Nothing here is built eagerly. Constructing a `Container` resolves no path, opens no index, loads no
model and creates no SDK client; each piece is built the first time a use case asks for it and is then
shared by both halves of the system. The write half (`ingest`) and the read half (`ask`) meet exactly
here: `ReindexCorpus` rebuilds the two indexes through the same adapters `AnswerQuestion` reads them
through, so an index is written and read with one identity (§5.3, §6).

The credential is not configuration: `Container` only checks that `ANTHROPIC_API_KEY` is present before
the generation stage is wired, and the SDK reads the value from the environment on its own. No field,
message or log line in this module ever carries it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from poe2_rag.answering.adapters.outbound.dense.bge_embedder import (
    BgeEmbedder,
    EmbedderError,
)
from poe2_rag.answering.adapters.outbound.dense.chroma_index import (
    ChromaVectorIndex,
    VectorIndexError,
    VectorIndexReadError,
    VectorIndexWriteError,
)
from poe2_rag.answering.adapters.outbound.lexical.bm25_index import (
    Bm25Index,
    LexicalIndexError,
    LexicalIndexLoadError,
    LexicalIndexWriteError,
)
from poe2_rag.answering.adapters.outbound.llm.claude_language_model import (
    ClaudeLanguageModel,
    LanguageModelError,
    LanguageModelRuntimeError,
)
from poe2_rag.answering.adapters.outbound.rerank.cross_encoder_reranker import (
    CrossEncoderReranker,
    RerankerError,
    RerankScoringError,
)
from poe2_rag.answering.application import (
    AnswerQuestion,
    AnswerQuestionError,
    LanguageModel,
    RetrievalSettings,
    RetrieveContext,
)
from poe2_rag.answering.domain.errors import InvalidQueryError
from poe2_rag.config import (
    API_KEY_VARIABLE,
    AppConfig,
    ConfigurationError,
    MissingApiKeyError,
    load_config,
)
from poe2_rag.evaluation.adapters.outbound.git_commit import current_commit
from poe2_rag.evaluation.adapters.outbound.json_report_writer import JsonReportWriter
from poe2_rag.evaluation.adapters.outbound.jsonl_dataset_reader import (
    JsonlDatasetReader,
)
from poe2_rag.evaluation.adapters.outbound.system_clock import SystemClock
from poe2_rag.evaluation.application import (
    ConfigSnapshot,
    EvaluationError,
    RunEvaluation,
)
from poe2_rag.ingestion.adapters.outbound.http import HttpFetchError, PoliteHttpFetcher
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    ChunkRepositoryLoadError,
    ChunkRepositoryWriteError,
    JsonChunkRepository,
)
from poe2_rag.ingestion.adapters.outbound.local_file_fetcher import (
    LocalFileFetcher,
    LocalSnapshotError,
)
from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import (
    PatchNotesParseError,
    PatchNotesParser,
)
from poe2_rag.ingestion.adapters.outbound.poe2db import (
    Poe2dbGemPageParser,
    Poe2dbParseError,
)
from poe2_rag.ingestion.application import (
    IngestionError,
    IngestSource,
    RawDocument,
    ReindexCorpus,
    SourceFetcher,
)
from poe2_rag.shared.adapters.outbound.toml_patch_catalog import (
    PatchCatalogLoadError,
    TomlPatchCatalog,
)
from poe2_rag.shared.domain import PatchNotReleasedError, is_blank

if TYPE_CHECKING:
    import httpx2
    from anthropic import Anthropic

CHROMA_DIRECTORY_NAME = "chroma"
"""The dense index lives beside `bm25.json`, under `<index_dir>/chroma` (§7)."""

DEFAULT_RETRIEVAL_SETTINGS = RetrievalSettings(
    lexical_top_n=50,
    dense_top_n=50,
    rerank_top_m=20,
    final_top_k=5,
)
"""Per-stage budgets for the MVP: 50 lexical and 50 dense candidates, 20 fused and reranked, 5 delivered.

These are a policy choice, not a consequence of the wiring: they decide how much of the corpus the
prompt can see, and the CLI feature was not allowed to invent a budget *variable*. They live here only
because the composition root is the one place that must choose concrete values to build `AnswerQuestion`,
and `tests/unit/test_bootstrap.py` pins each number so a change is a decision rather than an edit.
`RetrievalSettings` independently validates the `final_top_k <= rerank_top_m` invariant.
"""

EXPECTED_ERRORS: tuple[type[Exception], ...] = (
    # configuration: unusable values, an unreadable catalog file, the missing credential
    ConfigurationError,
    PatchCatalogLoadError,
    # ingestion: the write path and the files it reads
    IngestionError,
    PatchNotesParseError,
    LocalSnapshotError,
    HttpFetchError,
    Poe2dbParseError,
    # A cached capture older than the first known release: the catalog cannot date it, and the
    # operator has to add the patch or discard the cache, so it is reported, not a traceback.
    PatchNotReleasedError,
    ChunkRepositoryLoadError,
    ChunkRepositoryWriteError,
    # answering: retrieval, the index rebuilds `ingest` performs through it, and the model that scored
    # the candidates. `InvalidQueryError` is caught on purpose even though it also covers type
    # invariants (`catalog must be a PatchCatalog`): its user-facing cases — a blank question or a
    # well-formed version the catalog does not know — are the most likely input errors, and the domain
    # offers no finer split yet. Every other domain error stays out and still propagates.
    AnswerQuestionError,
    InvalidQueryError,
    LexicalIndexError,
    LexicalIndexLoadError,
    LexicalIndexWriteError,
    VectorIndexError,
    VectorIndexReadError,
    VectorIndexWriteError,
    EmbedderError,
    RerankerError,
    RerankScoringError,
    # generation: the adapter's own failures, refusal and truncation included
    LanguageModelError,
    LanguageModelRuntimeError,
    # evaluation: an unreadable or drifted dataset, an unusable request, an unwritable report.
    # `ReportWriteError` is a subclass, so the family covers it without a second entry.
    EvaluationError,
)
"""Every failure the wired adapters declare as operational, in one place the CLI can ask for.

It lives in the composition root, not in the CLI, because only this module knows which adapters exist:
a new adapter adds its errors here, and the inbound boundary keeps receiving a container instead of a
list of hierarchies (ADR-0006, §7). A model that cannot load is deliberately *not* here: `BgeEmbedder`
lets the loader's `OSError` escape, and catching `OSError` at the boundary would report a permission or
disk fault as if it were a model problem. Closing that needs the loader wrapped in a dedicated adapter
error first.
"""

ClientFactory = Callable[[], "Anthropic"]
"""Builds the SDK client on demand, so its import and its credential stay out of every other path."""


def build_container(environ: Mapping[str, str] | None = None) -> Container:
    """Build the container from `environ` (the process environment when it is omitted)."""
    source = os.environ if environ is None else environ
    return Container(load_config(source), environ=source)


class Container:
    """Every adapter of one run, built on first use and then shared.

    The properties are the composition root's only public surface: `ingest_source` with
    `reindex_corpus` for the write half, `answer_question` for the read half, and the shared adapters
    in between so a test can assert that both halves really use one instance per port.
    """

    __slots__ = (
        "_answer_question",
        "_chunk_repository",
        "_client_factory",
        "_config",
        "_embedder",
        "_evaluation",
        "_has_api_key",
        "_http_client_instance",
        "_ingest_source",
        "_injected_http_client",
        "_language_model",
        "_lexical_index",
        "_patch_catalog",
        "_poe2db_ingest",
        "_poe2db_refresh_ingest",
        "_reindex_corpus",
        "_reranker",
        "_retrieve_context",
        "_vector_index",
    )

    def __init__(
        self,
        config: AppConfig,
        *,
        environ: Mapping[str, str] | None = None,
        client_factory: ClientFactory | None = None,
        language_model: LanguageModel | None = None,
        http_client: httpx2.Client | None = None,
    ) -> None:
        self._config = config
        # The environment is reduced to one flag while the container is built: the mapping itself is
        # not retained, so the credential cannot travel in an attribute that a repr, a log line or a
        # traceback frame would print. Only presence is ever consulted; the value is never read.
        self._has_api_key = not is_blank((os.environ if environ is None else environ).get(API_KEY_VARIABLE))
        self._client_factory = (
            _default_anthropic_client if client_factory is None else client_factory
        )
        self._language_model = language_model
        self._patch_catalog: TomlPatchCatalog | None = None
        self._chunk_repository: JsonChunkRepository | None = None
        self._embedder: BgeEmbedder | None = None
        self._lexical_index: Bm25Index | None = None
        self._vector_index: ChromaVectorIndex | None = None
        self._reranker: CrossEncoderReranker | None = None
        self._ingest_source: IngestSource | None = None
        self._poe2db_ingest: IngestSource | None = None
        self._poe2db_refresh_ingest: IngestSource | None = None
        self._injected_http_client = http_client
        self._http_client_instance: httpx2.Client | None = None
        self._reindex_corpus: ReindexCorpus | None = None
        self._retrieve_context: RetrieveContext | None = None
        self._evaluation: RunEvaluation | None = None
        self._answer_question: AnswerQuestion | None = None

    @property
    def config(self) -> AppConfig:
        """The configuration every adapter below was built from."""
        return self._config

    @property
    def expected_errors(self) -> tuple[type[Exception], ...]:
        """The failures the wired adapters declare as operational: what the CLI reports as one line."""
        return EXPECTED_ERRORS

    @property
    def patch_catalog(self) -> TomlPatchCatalog:
        """The patches `config/patches.toml` declares, and which one is current."""
        if self._patch_catalog is None:
            self._patch_catalog = TomlPatchCatalog(self._config.patch_catalog_path)
        return self._patch_catalog

    @property
    def chunk_repository(self) -> JsonChunkRepository:
        """The processed corpus on disk."""
        if self._chunk_repository is None:
            self._chunk_repository = JsonChunkRepository(self._config.processed_dir)
        return self._chunk_repository

    @property
    def embedder(self) -> BgeEmbedder:
        """One embedder for both sides: queries with the instruction, documents without it."""
        if self._embedder is None:
            self._embedder = BgeEmbedder(self._config.embedder_model_id)
        return self._embedder

    @property
    def lexical_index(self) -> Bm25Index:
        """The BM25 index, written by `reindex_corpus` and searched by `answer_question`."""
        if self._lexical_index is None:
            self._lexical_index = Bm25Index(self._config.index_dir)
        return self._lexical_index

    @property
    def vector_index(self) -> ChromaVectorIndex:
        """The Chroma index, written by `reindex_corpus` and searched by `answer_question`."""
        if self._vector_index is None:
            self._vector_index = ChromaVectorIndex(
                self._config.index_dir / CHROMA_DIRECTORY_NAME,
                embedder=self.embedder,
            )
        return self._vector_index

    @property
    def reranker(self) -> CrossEncoderReranker:
        """The cross-encoder that reorders the fused candidates."""
        if self._reranker is None:
            self._reranker = CrossEncoderReranker(self._config.reranker_model_id)
        return self._reranker

    @property
    def language_model(self) -> LanguageModel:
        """The generation adapter, built only when it is asked for and only with a credential."""
        if self._language_model is None:
            self._require_api_key()
            self._language_model = ClaudeLanguageModel(
                self._client_factory(),
                self._config.llm_model,
                max_tokens=self._config.llm_max_tokens,
                effort=self._config.llm_effort,
            )
        return self._language_model

    @property
    def ingest_source(self) -> IngestSource:
        """Fetch a local snapshot, parse it and upsert the chunks it produces."""
        if self._ingest_source is None:
            self._ingest_source = IngestSource(
                fetcher=_RawDirectoryFetcher(self._config.raw_dir, LocalFileFetcher()),
                parser=PatchNotesParser(),
                repository=self.chunk_repository,
                catalog=self.patch_catalog,
            )
        return self._ingest_source

    def poe2db_ingest_source(self, *, refresh: bool) -> IngestSource:
        """Fetch one poe2db page over HTTP, parse it and upsert the chunks it produces.

        One runner per cache policy: `refresh` is a property of the fetcher, so a cached run and a
        forced run cannot share one instance without one of them changing the other's behaviour.
        Both runners share the corpus and the catalog, exactly like the patch-notes pair.
        """
        existing = self._poe2db_refresh_ingest if refresh else self._poe2db_ingest
        if existing is not None:
            return existing
        runner = IngestSource(
            fetcher=PoliteHttpFetcher(
                client=self._http_client(),
                cache_dir=self._config.raw_dir,
                contact=self._config.http_contact,
                request_interval=self._config.http_request_interval,
                timeout=self._config.http_timeout,
                refresh=refresh,
            ),
            parser=Poe2dbGemPageParser(self.patch_catalog),
            repository=self.chunk_repository,
            catalog=self.patch_catalog,
        )
        if refresh:
            self._poe2db_refresh_ingest = runner
        else:
            self._poe2db_ingest = runner
        return runner

    def _http_client(self) -> httpx2.Client:
        """Build the HTTP client on first use, or return the one a test injected."""
        if self._injected_http_client is not None:
            return self._injected_http_client
        if self._http_client_instance is None:
            import httpx2

            self._http_client_instance = httpx2.Client(timeout=self._config.http_timeout)
        return self._http_client_instance

    @property
    def reindex_corpus(self) -> ReindexCorpus:
        """Rebuild both indexes from the processed corpus, embedding before either write (§5.3)."""
        if self._reindex_corpus is None:
            self._reindex_corpus = ReindexCorpus(
                self.chunk_repository,
                self.lexical_index,
                document_embedder=self.embedder,
                vector_writer=self.vector_index,
            )
        return self._reindex_corpus

    @property
    def retrieve_context(self) -> RetrieveContext:
        """The deterministic half of the read path, sharing every port with `answer_question`.

        `RunEvaluation` depends on this use case and never on the language model (ADR-0005): the
        retrieval is what a fixed dataset can measure without spending a token.
        """
        if self._retrieve_context is None:
            self._retrieve_context = RetrieveContext(
                embedder=self.embedder,
                lexical_index=self.lexical_index,
                vector_index=self.vector_index,
                reranker=self.reranker,
                catalog=self.patch_catalog,
                settings=DEFAULT_RETRIEVAL_SETTINGS,
            )
        return self._retrieve_context

    @property
    def evaluation(self) -> RunEvaluation:
        """Measure retrieval against the fixed dataset: one report, no model, no credential (D4).

        It reads the corpus, so `eval` needs the indexes built by `ingest`, and it shares the retrieval
        use case with `answer_question` instead of rebuilding the pipeline. The commit id is read here,
        in the composition root, and passed in: the use case never runs git. Missing git is not an
        error, so a report outside a repository simply carries no commit.
        """
        if self._evaluation is None:
            self._evaluation = RunEvaluation(
                retriever=self.retrieve_context,
                corpus=self.chunk_repository,
                reader=JsonlDatasetReader(),
                writer=JsonReportWriter(),
                clock=SystemClock(),
                config_snapshot=ConfigSnapshot(
                    embedder_model=self._config.embedder_model_id,
                    reranker_model=self._config.reranker_model_id,
                    retrieval=DEFAULT_RETRIEVAL_SETTINGS,
                ),
                default_dataset_path=self._config.eval_dataset_path,
                default_output_dir=self._config.eval_runs_dir,
                git_commit=current_commit(self._config.project_root),
            )
        return self._evaluation

    @property
    def answer_question(self) -> AnswerQuestion:
        """Retrieve, fuse, rerank, generate and validate: the whole read path in one use case."""
        if self._answer_question is None:
            self._answer_question = AnswerQuestion(
                retrieve=self.retrieve_context,
                language_model=self.language_model,
            )
        return self._answer_question

    def _require_api_key(self) -> None:
        """Fail before retrieval when generation has no credential, and never echo the value.

        Asking for the answer is what needs the key, so the presence flag is *checked* here and not at
        construction: `ingest` has to work on a machine that never calls the API. Raising before the
        first port call keeps a missing key from paying for an embedding, two index scans and a rerank.
        """
        if not self._has_api_key:
            raise MissingApiKeyError(
                f"{API_KEY_VARIABLE} is not set. Export it, or pass it with "
                f"`uv run --env-file .env ...` (see .env.example)."
            )


class _RawDirectoryFetcher:
    """Read a snapshot from the configured raw directory, so `POE2_RAG_RAW_DIR` is not a dead knob.

    `LocalFileFetcher` reads the locator it is handed, relative to the working directory. Where snapshots
    live is configuration, so it is resolved here in the composition root rather than in the adapter:
    `ingest patch-notes patch-notes-0.1.1.html` finds the file under the configured raw directory from
    any working directory, an absolute locator keeps working exactly as given, and the report echoes the
    path that was actually read.
    """

    __slots__ = ("_fetcher", "_root")

    def __init__(self, root: Path, fetcher: SourceFetcher) -> None:
        self._root = root
        self._fetcher = fetcher

    def fetch(self, locator: str) -> RawDocument:
        """Fetch `locator`, resolving a relative path against the raw directory."""
        path = Path(locator)
        return self._fetcher.fetch(str(path if path.is_absolute() else self._root / path))


def _default_anthropic_client() -> Anthropic:
    """Create the SDK client at the moment the generation stage needs it.

    The import sits inside the function so `bootstrap` itself never imports the SDK: the composition
    root wires the generation adapter, and only that adapter's module owns `anthropic` (§7, ADR-0005).
    """
    import anthropic

    return anthropic.Anthropic()
