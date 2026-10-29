"""Provenance of a chunk, used to build citations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlsplit

from poe2_rag.shared.domain._text import is_blank
from poe2_rag.shared.domain.errors import InvalidSourceRefError

_ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where a piece of content came from and when it was captured."""

    type: str
    url: str
    captured_at: date

    def __post_init__(self) -> None:
        if is_blank(self.type):
            raise InvalidSourceRefError("type must be a non-blank string")
        if isinstance(self.url, str):
            object.__setattr__(self, "url", self.url.strip())
        if not _is_http_url(self.url):
            raise InvalidSourceRefError(f"url must be an absolute http(s) URL, got {self.url!r}")
        if isinstance(self.captured_at, datetime) or not isinstance(self.captured_at, date):
            raise InvalidSourceRefError(f"captured_at must be a date, got {self.captured_at!r}")


def _is_http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    if parts.scheme not in _ALLOWED_SCHEMES:
        return False
    hostname = parts.hostname
    if not hostname:
        return False
    if any(char.isspace() for char in hostname):
        return False
    return ":" in hostname or all(hostname.split("."))
