"""Three-way locator classification.

The classifier is deliberately three-valued rather than a boolean ``is_http_locator``:

* ``HTTP`` means an ``http`` or ``https`` scheme and reaches the polite HTTP fetcher.
* ``OTHER_SCHEME`` means some other ``<scheme>://`` shape; it still reaches the HTTP fetcher so
  the policy validator rejects it with the same ``UnsupportedLocatorError`` both with and without
  ``--refresh``.
* ``PATH`` means everything else (relative path, absolute path, blank) and stays on the local
  branch.

A boolean predicate would need a second predicate for the CLI ``--refresh`` guard, and the two
predicates could diverge; one table feeds both the dispatcher and the guard.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse


class LocatorKind(Enum):
    """Kind of source locator passed to ``ingest patch-notes``.

    Values are strings so the classifier can be logged or serialized without a second mapping.
    """

    HTTP = "HTTP"
    OTHER_SCHEME = "OTHER_SCHEME"
    PATH = "PATH"


def classify_locator(locator: str) -> LocatorKind:
    """Return whether ``locator`` is an http(s) URL, another scheme URL, or a path."""
    parsed = urlparse(locator)
    if parsed.scheme in {"http", "https"}:
        return LocatorKind.HTTP
    if parsed.scheme:
        return LocatorKind.OTHER_SCHEME
    return LocatorKind.PATH
