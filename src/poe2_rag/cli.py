"""Top-level CLI: parse, dispatch, and report expected failures as one line (§5, ADR-0006).

This module owns no business logic and knows no adapter. It parses arguments, asks the composition root
for the container, and hands the command to the inbound adapter that owns it.

Which failures are *expected* belongs to the composition root as well: only it knows which adapters it
wired, so it publishes the tuple this boundary reports (`Container.expected_errors`). Keeping the tuple
there is what lets ADR-0006 hold in practice — the CLI receives its dependencies already built, a new
adapter does not force an edit here, and importing the CLI does not drag in `chromadb`, the SDK or torch.
The one failure this module does own is that of building the container at all, which happens before a
container exists to ask.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Protocol

from poe2_rag.answering.adapters.inbound import cli as ask_cli
from poe2_rag.config import ConfigurationError
from poe2_rag.ingestion.adapters.inbound import cli as ingest_cli
from poe2_rag.shared.domain.errors import InvalidPatchVersionError
from poe2_rag.shared.domain.patch_version import PatchVersion

BUILD_ERRORS: tuple[type[Exception], ...] = (ConfigurationError,)
"""The only failures that can precede the container: reading the `POE2_RAG_*` environment."""


class CliContainer(Protocol):
    """What the CLI needs from the composition root: three runners and the failures to report.

    `bootstrap.Container` satisfies it structurally, and so does a fake: a command can be tested
    without wiring an adapter, a model or a credential.
    """

    @property
    def expected_errors(self) -> tuple[type[Exception], ...]:
        """The failures the wired adapters declare as operational, reported as one line."""
        ...

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
    """Build the production container on demand.

    The import lives here so that importing this module — which `--help` does — stays free of the
    composition root and everything it pulls in. The container reduces the environment to what the
    adapters need; this function only asks for it.
    """
    from poe2_rag import bootstrap

    return bootstrap.build_container()


def _patch_version(raw: str) -> PatchVersion:
    """Convert an argparse value to a ``PatchVersion``, or fail as a usage error.

    A malformed version is the user mistyping the command, so it exits 2 through argparse. A
    well-formed version the catalog does not know is a different failure: it reaches
    ``AnswerQuestion`` and comes back through the container's expected errors with exit 1.
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
    # No `metavar` here: the choices are the point, so argparse renders `{patch-notes}` and the list
    # grows into `{patch-notes,poe2db,...}` by itself when another source adapter is added.
    ingest.add_argument(
        "source",
        choices=ingest_cli.SOURCES,
        help="source type to ingest",
    )
    ingest.add_argument(
        "locator",
        metavar="path",
        help="path of the source snapshot, relative paths resolve against POE2_RAG_RAW_DIR",
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


def _report(error: Exception) -> int:
    """Write `error` as the single stderr line the contract promises, and return the exit code.

    Collapsing whitespace keeps a multi-line message a line: the contract is "one line on stderr", and
    a message that wrapped would read like a traceback.
    """
    print(f"error: {' '.join(str(error).split())}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None, *, container: CliContainer | None = None) -> int:
    """Parse ``argv`` and run the requested command, returning the exit code.

    Expected failures print one line on stderr and return 1: those of building the container, and those
    the container declares in `expected_errors`. argparse usage errors raise ``SystemExit`` with code 2,
    ``--help`` raises it with code 0, and anything else propagates untouched — an unexpected exception is
    a bug, and a traceback is the honest thing to show for it.
    """
    args = _build_parser().parse_args(argv)

    try:
        runtime = container if container is not None else _default_container()
    except BUILD_ERRORS as exc:
        return _report(exc)

    try:
        _dispatch(args, runtime)
    except tuple(runtime.expected_errors) as exc:
        return _report(exc)

    return 0
