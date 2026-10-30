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
from typing import TYPE_CHECKING

from poe2_rag.answering.adapters.outbound.dense.bge_embedder import BgeEmbedder
from poe2_rag.answering.adapters.outbound.dense.chroma_index import ChromaVectorIndex
from poe2_rag.answering.adapters.outbound.lexical.bm25_index import Bm25Index
from poe2_rag.answering.adapters.outbound.llm.claude_language_model import (
    ClaudeLanguageModel,
)
from poe2_rag.answering.adapters.outbound.rerank.cross_encoder_reranker import (
    CrossEncoderReranker,
)
from poe2_rag.answering.application import (
    AnswerQuestion,
    LanguageModel,
    RetrievalSettings,
)
from poe2_rag.config import API_KEY_VARIABLE, AppConfig, MissingApiKeyError, load_config
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)
from poe2_rag.ingestion.adapters.outbound.local_file_fetcher import LocalFileFetcher
from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import PatchNotesParser
from poe2_rag.ingestion.application import IngestSource, ReindexCorpus
from poe2_rag.shared.adapters.outbound.toml_patch_catalog import TomlPatchCatalog
from poe2_rag.shared.domain import is_blank

if TYPE_CHECKING:
    from anthropic import Anthropic

CHROMA_DIRECTORY_NAME = "chroma"
"""The dense index lives beside `bm25.json`, under `<index_dir>/chroma` (§7)."""

DEFAULT_RETRIEVAL_SETTINGS = RetrievalSettings(
    lexical_top_n=50,
    dense_top_n=50,
    rerank_top_m=20,
    final_top_k=5,
)
"""Per-stage budgets for the MVP: wide enough for both indexes, small enough for a cheap prompt.

No configuration variable exposes them yet, because the request that wired the CLI did not name one and
a budget is policy rather than wiring. `RetrievalSettings` still validates the invariant.
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
        "_has_api_key",
        "_ingest_source",
        "_language_model",
        "_lexical_index",
        "_patch_catalog",
        "_reindex_corpus",
        "_reranker",
        "_vector_index",
    )

    def __init__(
        self,
        config: AppConfig,
        *,
        environ: Mapping[str, str] | None = None,
        client_factory: ClientFactory | None = None,
        language_model: LanguageModel | None = None,
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
        self._reindex_corpus: ReindexCorpus | None = None
        self._answer_question: AnswerQuestion | None = None

    @property
    def config(self) -> AppConfig:
        """The configuration every adapter below was built from."""
        return self._config

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
                fetcher=LocalFileFetcher(),
                parser=PatchNotesParser(),
                repository=self.chunk_repository,
                catalog=self.patch_catalog,
            )
        return self._ingest_source

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
    def answer_question(self) -> AnswerQuestion:
        """Retrieve, fuse, rerank, generate and validate: the whole read path in one use case."""
        if self._answer_question is None:
            self._answer_question = AnswerQuestion(
                embedder=self.embedder,
                lexical_index=self.lexical_index,
                vector_index=self.vector_index,
                reranker=self.reranker,
                language_model=self.language_model,
                catalog=self.patch_catalog,
                settings=DEFAULT_RETRIEVAL_SETTINGS,
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


def _default_anthropic_client() -> Anthropic:
    """Create the SDK client at the moment the generation stage needs it.

    The import sits inside the function so `bootstrap` itself never imports the SDK: the composition
    root wires the generation adapter, and only that adapter's module owns `anthropic` (§7, ADR-0005).
    """
    import anthropic

    return anthropic.Anthropic()
