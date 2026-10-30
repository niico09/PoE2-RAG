"""Top-level CLI: parse, dispatch and map expected errors to exit codes (S5, S6).

This module owns no business logic. It wires the user's command-line arguments to the
``ingest`` and ``ask`` inbound adapters, and turns the expected domain/application errors
into one-line stderr messages with no traceback. Everything else propagates unchanged.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Protocol

from poe2_rag.answering.adapters.inbound import cli as ask_cli
from poe2_rag.answering.adapters.outbound.dense.bge_embedder import EmbedderError
from poe2_rag.answering.adapters.outbound.dense.chroma_index import (
    VectorIndexError,
    VectorIndexReadError,
    VectorIndexWriteError,
)
from poe2_rag.answering.adapters.outbound.lexical.bm25_index import (
    LexicalIndexError,
    LexicalIndexLoadError,
    LexicalIndexWriteError,
)
from poe2_rag.answering.adapters.outbound.llm.claude_language_model import (
    LanguageModelError,
    LanguageModelRuntimeError,
)
from poe2_rag.answering.adapters.outbound.rerank.cross_encoder_reranker import (
    RerankerError,
    RerankScoringError,
)
from poe2_rag.answering.application import AnswerQuestionError
from poe2_rag.answering.domain.errors import InvalidQueryError
from poe2_rag.config import ConfigurationError
from poe2_rag.ingestion.adapters.inbound import cli as ingest_cli
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    ChunkRepositoryLoadError,
    ChunkRepositoryWriteError,
)
from poe2_rag.ingestion.adapters.outbound.local_file_fetcher import LocalSnapshotError
from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import PatchNotesParseError
from poe2_rag.ingestion.application import IngestionError
from poe2_rag.shared.adapters.outbound.toml_patch_catalog import PatchCatalogLoadError
from poe2_rag.shared.domain.errors import InvalidPatchVersionError
from poe2_rag.shared.domain.patch_version import PatchVersion

EXPECTED_ERRORS: tuple[type[Exception], ...] = (
    # configuration: unusable values, an unreadable catalog file, the missing credential
    ConfigurationError,
    PatchCatalogLoadError,
    # ingestion: the write path and the files it reads
    IngestionError,
    PatchNotesParseError,
    LocalSnapshotError,
    ChunkRepositoryLoadError,
    ChunkRepositoryWriteError,
    # answering: retrieval, the indexes it reads and the writes `ingest` performs through them.
    # `InvalidQueryError` is the one domain error a user provokes with input — a blank question or a
    # well-formed version the catalog does not know — so it is reported like any other expected
    # failure; every other domain error is an invariant violation and still propagates.
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
)


class CliContainer(Protocol):
    """The three runners the CLI needs, satisfied by ``bootstrap.Container`` or by fakes."""

    @property
    def ingest_source(self) -> ingest_cli.IngestSourceRunner:
        """Runner that ingests one source document."""
        ...

    @property
    def reindex_corpus(self) -> ingest_cli.ReindexCorpusRunner:
        """Runner that rebuilds the indexes from the processed corpus."""
        ...

    @property
    def answer_question(self) -> ask_cli.AnswerQuestionRunner:
        """Runner that answers a question for an optional patch."""
        ...


def _default_container() -> CliContainer:
    """Build the production container lazily.

    Importing the composition root pulls ``chromadb`` and the Anthropic SDK, so this
    function is only called when a real command (not ``--help``) needs it.
    """
    from poe2_rag import bootstrap

    return bootstrap.build_container()


def _patch_version(raw: str) -> PatchVersion:
    """Convert an argparse value to a ``PatchVersion``, or fail as a usage error.

    A malformed version is the user mistyping the command, so it exits 2 through argparse. A
    well-formed version the catalog does not know is a different failure: it reaches
    ``AnswerQuestion`` and comes back through ``EXPECTED_ERRORS`` with exit 1.
    """
    try:
        return PatchVersion.parse(raw)
    except InvalidPatchVersionError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for ``poe2-rag``."""
    parser = argparse.ArgumentParser(
        prog="poe2-rag",
        description="Ask patch-aware questions about Path of Exile 2 and ingest its sources.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="ingest a source and rebuild the indexes")
    ingest.add_argument(
        "source",
        choices=ingest_cli.SOURCES,
        metavar="source",
        help="source type to ingest",
    )
    ingest.add_argument(
        "locator",
        metavar="path",
        help="path or URL of the source document",
    )

    ask = subparsers.add_parser("ask", help="ask a question about the corpus")
    ask.add_argument(
        "question",
        help="the question to answer",
    )
    ask.add_argument(
        "--patch",
        type=_patch_version,
        default=None,
        help="patch version to answer for (default: current patch)",
    )

    return parser


def _dispatch(args: argparse.Namespace, container: CliContainer) -> None:
    """Dispatch a parsed command to the inbound adapter that owns it."""
    if args.command == "ingest":
        ingest_cli.run(
            source=args.source,
            locator=args.locator,
            ingest=container.ingest_source,
            reindex=container.reindex_corpus,
        )
    elif args.command == "ask":
        ask_cli.run(
            question=args.question,
            patch=args.patch,
            ask=container.answer_question,
        )


def main(argv: Sequence[str] | None = None, *, container: CliContainer | None = None) -> int:
    """Parse ``argv`` and run the requested command.

    Expected errors print one line on stderr and return 1. argparse usage errors raise
    ``SystemExit`` with code 2, and ``--help`` raises ``SystemExit`` with code 0.
    Unexpected exceptions propagate unchanged.
    """
    args = _build_parser().parse_args(argv)

    try:
        # Building the container reads the documented POE2_RAG_* variables, so it belongs inside the
        # handler: a bad value is exactly the configuration failure this contract reports.
        _dispatch(args, container if container is not None else _default_container())
    except EXPECTED_ERRORS as exc:
        message = str(exc).replace("\n", " ").strip()
        print(f"error: {message}", file=sys.stderr)
        return 1

    return 0
