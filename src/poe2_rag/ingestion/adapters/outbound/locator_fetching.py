"""Dispatcher that selects the fetch branch by locator kind.

``LocatorFetcher`` sits on top of the two branches: the local branch is ``_RawDirectoryFetcher``
(resolving relative paths against ``POE2_RAG_RAW_DIR`` and keeping absolute paths as given), and
the URL branch is ``PoliteHttpFetcher`` for the closed forum policy. The raw-directory wrapper
never sees a URL, so it cannot accidentally prefix one.
"""

from __future__ import annotations

from collections.abc import Callable

from poe2_rag.ingestion.adapters.locator import LocatorKind, classify_locator
from poe2_rag.ingestion.application.ports import RawDocument, SourceFetcher


class LocatorFetcher:
    """Fetch a patch-notes source from disk or from the polite HTTP fetcher."""

    __slots__ = ("_http", "_http_factory", "_local")

    def __init__(
        self,
        *,
        local: SourceFetcher,
        http: Callable[[], SourceFetcher],
    ) -> None:
        self._local = local
        self._http_factory = http
        self._http: SourceFetcher | None = None

    def fetch(self, locator: str) -> RawDocument:
        """Return the document at ``locator`` from the branch its kind selects."""
        kind = classify_locator(locator)
        if kind is LocatorKind.PATH:
            return self._local.fetch(locator)
        if self._http is None:
            self._http = self._http_factory()
        return self._http.fetch(locator)
