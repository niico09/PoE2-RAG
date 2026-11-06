"""Polite HTTP fetcher for the closed set of hosts this project may fetch.

The adapter implements `SourceFetcher` with the four respectful-access rules from ADR-0002:
an identifiable `User-Agent` with contact, a per-host rate limit, an in-memory `robots.txt` cache,
and an on-disk page cache.  Every failure that originates here is a `HttpFetchError` so callers can
tell network/cache failures from malformed documents.

Which hosts and which paths are allowed is a `HttpSourcePolicy`: the fetcher applies a rule it was
handed and never decides one for itself. Adding a second host is therefore a declaration in the
composition root, not a branch here, and the scheme stays fixed to https for every policy.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx2

from poe2_rag.config import ConfigurationError
from poe2_rag.ingestion.application import RawDocument
from poe2_rag.shared.domain import is_blank

SOURCE_TYPE = "poe2db"
_ALLOWED_SCHEME = "https"
_SIDE_CAR_SUFFIX = ".meta.json"
_TEMP_SUFFIX = ".tmp"
_CACHE_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class HttpFetchError(RuntimeError):
    """Base class for every failure the polite HTTP fetcher owns."""

    __slots__ = ()


class UnsupportedLocatorError(HttpFetchError):
    """Raised when a locator does not match the policy the fetcher was built with."""

    __slots__ = ()


class RobotsDisallowedError(HttpFetchError):
    """Raised when robots.txt forbids the requested URL or rejects the client."""

    __slots__ = ()


class RobotsUnavailableError(HttpFetchError):
    """Raised when robots.txt cannot be fetched or parsed."""

    __slots__ = ()


class HttpStatusError(HttpFetchError):
    """Raised for a non-2xx HTTP response."""

    __slots__ = ("status_code", "url")

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code} for {url}")
        self.status_code = status_code
        self.url = url


class HttpTimeoutError(HttpFetchError):
    """Raised when a request exceeds the configured timeout."""

    __slots__ = ()


class HttpRequestError(HttpFetchError):
    """Raised for transport failures other than timeouts."""

    __slots__ = ()


class HttpCacheError(HttpFetchError):
    """Raised when the on-disk cache is unreadable or inconsistent."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class PathRule:
    """The one path shape a policy accepts: a fixed prefix, then a slug matching `slug`.

    `slug_message` is the sentence the rule contributes to `UnsupportedLocatorError`, so each host
    names its own rejection ("slug must be non-empty with no slash", "thread id must be digits")
    without the shared validator learning either vocabulary.
    """

    prefix: str
    slug: re.Pattern[str]
    slug_message: str


@dataclass(frozen=True, slots=True)
class HttpSourcePolicy:
    """One closed rule set: which host, which path, which source type, which cache segment.

    `source_type` is the value the parser for this host accepts, so the kind is spelled once and a
    test pins the two together. `cache_directory` is one path segment appended to the constructor's
    `cache_dir`, and the noun every cache message uses derives from it: one string per concern, so
    `patch_notes` reads `patch-notes` in a sentence and never grows a second name.
    """

    host: str
    path: PathRule
    source_type: str
    cache_directory: str

    def __post_init__(self) -> None:
        if is_blank(self.host):
            raise ValueError("host must be a non-blank string")
        if is_blank(self.source_type):
            raise ValueError("source_type must be a non-blank string")
        segment = self.cache_directory
        if is_blank(segment) or segment in {".", ".."} or "/" in segment or "\\" in segment:
            raise ValueError(f"cache_directory must be one plain path segment, got {segment!r}")


POE2DB_POLICY = HttpSourcePolicy(
    host="poe2db.tw",
    path=PathRule(
        prefix="/us/",
        slug=re.compile(r"[^/]+"),
        slug_message="slug must be non-empty with no slash",
    ),
    source_type=SOURCE_TYPE,
    cache_directory="poe2db",
)
"""The fetcher's default: the only host it accepted before policies existed, unchanged."""


class MissingHttpContactError(ConfigurationError):
    """Raised when the User-Agent contact is missing or blank."""

    __slots__ = ()


def _package_version() -> str:
    """Return the installed package version, or a safe fallback in development."""
    try:
        return version("poe2-rag")
    except PackageNotFoundError:
        return "0.0.0"


class PoliteHttpFetcher:
    """Fetch one policy's pages politely: robots, rate limit, cache, identifiable User-Agent."""

    __slots__ = (
        "_cache_dir",
        "_cache_noun",
        "_client",
        "_clock",
        "_contact",
        "_last_request",
        "_policy",
        "_refresh",
        "_request_interval",
        "_robots",
        "_sleep",
        "_timeout",
    )

    def __init__(
        self,
        *,
        policy: HttpSourcePolicy = POE2DB_POLICY,
        client: httpx2.Client,
        cache_dir: Path,
        contact: str | None,
        request_interval: float,
        timeout: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        refresh: bool = False,
    ) -> None:
        if contact is None or is_blank(contact):
            raise MissingHttpContactError(
                "POE2_RAG_HTTP_CONTACT is required for outbound HTTP fetching"
            )
        self._policy = policy
        self._cache_noun = policy.cache_directory.replace("_", "-")
        self._client = client
        self._cache_dir = cache_dir
        self._contact = contact.strip()
        self._request_interval = request_interval
        self._timeout = timeout
        self._clock = clock
        self._sleep = sleep
        self._refresh = refresh
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request: dict[str, float] = {}

    def fetch(self, locator: str) -> RawDocument:
        """Return the page at `locator`, using the cache unless `refresh` is set."""
        url = self._validate_locator(locator)
        if not self._refresh:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        self._ensure_allowed_by_robots(url)
        response = self._request(url)
        if response.status_code < 200 or response.status_code >= 300:
            raise HttpStatusError(response.status_code, url)

        content = response.text
        fetched_at = datetime.now(UTC)
        self._write_cache(url, content, fetched_at)
        return RawDocument(
            locator=url,
            content=content,
            fetched_at=fetched_at,
            source_type=self._policy.source_type,
        )

    def _validate_locator(self, locator: str) -> str:
        """Return the canonical URL if the locator matches the policy, else raise.

        The order is fixed and shared by every policy: scheme, host, port, userinfo, query, fragment,
        then the path rule (prefix, trailing slash, slug). The scheme, port, userinfo, query,
        fragment and trailing-slash sentences stay literal because they are host-independent; the
        host, prefix and slug come from the policy, which is the only place that knows a host.
        """
        policy = self._policy
        rule = policy.path
        if is_blank(locator):
            raise UnsupportedLocatorError(f"locator must be a non-blank URL, got {locator!r}")
        try:
            parsed = urlparse(locator)
        except ValueError as exc:
            raise UnsupportedLocatorError(f"not a valid URL: {locator!r}") from exc

        if parsed.scheme != _ALLOWED_SCHEME:
            raise UnsupportedLocatorError(f"only https URLs are allowed, got {locator!r}")
        if parsed.hostname != policy.host:
            raise UnsupportedLocatorError(f"only {policy.host} is allowed, got {locator!r}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise UnsupportedLocatorError(f"not a valid URL: {locator!r}") from exc
        if port is not None:
            raise UnsupportedLocatorError(f"no port is allowed, got {locator!r}")
        if parsed.username is not None or parsed.password is not None:
            raise UnsupportedLocatorError(f"no userinfo is allowed, got {locator!r}")
        if parsed.query:
            raise UnsupportedLocatorError(f"no query is allowed, got {locator!r}")
        if parsed.fragment:
            raise UnsupportedLocatorError(f"no fragment is allowed, got {locator!r}")

        path = parsed.path
        if not path.startswith(rule.prefix):
            raise UnsupportedLocatorError(
                f"path must start with {rule.prefix!r}, got {locator!r}"
            )
        if path.endswith("/"):
            raise UnsupportedLocatorError(f"no trailing slash is allowed, got {locator!r}")
        slug = path[len(rule.prefix) :]
        if rule.slug.fullmatch(slug) is None:
            raise UnsupportedLocatorError(f"{rule.slug_message}, got {locator!r}")

        return f"{_ALLOWED_SCHEME}://{policy.host}{path}"

    def _ensure_allowed_by_robots(self, url: str) -> None:
        """Raise if robots.txt disallows the given URL for our User-Agent."""
        host = urlparse(url).hostname or self._policy.host
        parser = self._robots_for(host)
        if not parser.can_fetch(self._user_agent(), url):
            raise RobotsDisallowedError(f"robots.txt disallows {url}")

    def _robots_for(self, host: str) -> RobotFileParser:
        """Return a parsed robots.txt for `host`, fetching it once per instance."""
        if host in self._robots:
            return self._robots[host]

        url = f"{_ALLOWED_SCHEME}://{host}/robots.txt"
        try:
            response = self._request(url)
        except HttpFetchError as exc:
            raise RobotsUnavailableError(f"cannot fetch robots.txt for {host}: {exc}") from exc

        parser = RobotFileParser()
        if 200 <= response.status_code < 300:
            parser.parse(response.text.splitlines())
        elif response.status_code in (401, 403):
            raise RobotsDisallowedError(f"robots.txt returned {response.status_code} for {host}")
        elif response.status_code in (404, 410):
            # A missing robots.txt means "allow all".
            parser.parse([])
        else:
            raise RobotsUnavailableError(
                f"robots.txt returned {response.status_code} for {host}"
            )

        self._robots[host] = parser
        return parser

    def _request(self, url: str) -> httpx2.Response:
        """Make a single GET request after honouring the per-host rate limit."""
        host = urlparse(url).hostname or self._policy.host
        self._throttle(host)
        try:
            response = self._client.get(
                url, headers={"User-Agent": self._user_agent()}, timeout=self._timeout
            )
        except httpx2.TimeoutException as exc:
            raise HttpTimeoutError(f"timeout fetching {url}: {exc}") from exc
        except httpx2.RequestError as exc:
            raise HttpRequestError(f"request failed for {url}: {exc}") from exc
        return response

    def _throttle(self, host: str) -> None:
        """Sleep if the previous request to `host` was too recent, then record this one.

        The clock is read again after sleeping: recording the pre-sleep moment would make the next
        request look a full interval old and let it leave immediately, back to back with this one.
        """
        now = self._clock()
        last = self._last_request.get(host)
        if last is not None:
            deficit = self._request_interval - (now - last)
            if deficit > 0:
                self._sleep(deficit)
                now = self._clock()
        self._last_request[host] = now

    def _user_agent(self) -> str:
        """Return the identifiable User-Agent header value."""
        return f"poe2-rag/{_package_version()} (+{self._contact})"

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        """Return the HTML path and its JSON sidecar for `url`.

        The name comes from the URL, so every character outside the safe set becomes a dash: a slug
        with a path separator must not turn the cache write into a write outside the cache directory.
        """
        parsed = urlparse(url)
        safe_path = parsed.path.strip("/").replace("/", "-")
        raw_name = f"{parsed.hostname}-{safe_path}"
        filename = f"{_CACHE_NAME_UNSAFE.sub('-', raw_name)}.html"
        html_path = self._cache_dir / self._policy.cache_directory / filename
        meta_path = html_path.with_name(html_path.name + _SIDE_CAR_SUFFIX)
        return html_path, meta_path

    def _read_cache(self, url: str) -> RawDocument | None:
        """Return a cached document if both files are present and valid."""
        html_path, meta_path = self._cache_paths(url)
        html_exists = html_path.exists()
        meta_exists = meta_path.exists()
        if not html_exists and not meta_exists:
            return None
        if not html_exists or not meta_exists:
            raise self._cache_error(url)

        try:
            # Bytes, not `read_text`: universal newlines would turn CRLF into LF and the stored
            # digest would never match again, breaking every cache hit for a CRLF page.
            content = html_path.read_bytes().decode("utf-8")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpCacheError(
                f"{self._cache_noun} cache is unreadable for {url}; use --refresh to rebuild it"
            ) from exc

        if not isinstance(meta, dict):
            raise self._cache_error(url)
        if meta.get("url") != url:
            raise self._cache_error(url)
        if meta.get("status") != 200:
            raise self._cache_error(url)
        fetched_at_value = meta.get("fetched_at")
        if not isinstance(fetched_at_value, str):
            raise self._cache_error(url)
        try:
            fetched_at = datetime.fromisoformat(fetched_at_value)
        except ValueError as exc:
            raise self._cache_error(url) from exc
        if fetched_at.tzinfo is None:
            raise self._cache_error(url)
        digest = meta.get("sha256")
        if not isinstance(digest, str) or digest != _content_digest(content):
            # A failed sidecar replace can leave the previous sidecar next to new HTML; the digest
            # keeps that pair from being served with a stale capture date.
            raise self._cache_error(url)

        return RawDocument(
            locator=url,
            content=content,
            fetched_at=fetched_at,
            source_type=self._policy.source_type,
        )

    def _cache_error(self, url: str) -> HttpCacheError:
        return HttpCacheError(
            f"{self._cache_noun} cache sidecar is malformed for {url}; use --refresh to rebuild it"
        )

    def _write_cache(self, url: str, content: str, fetched_at: datetime) -> None:
        """Write HTML and its sidecar atomically."""
        html_path, meta_path = self._cache_paths(url)
        html_path.parent.mkdir(parents=True, exist_ok=True)

        self._atomic_write(html_path, content.encode("utf-8"))
        meta = {
            "url": url,
            "fetched_at": fetched_at.isoformat(),
            "status": 200,
            "sha256": _content_digest(content),
        }
        self._atomic_write(meta_path, json.dumps(meta, separators=(",", ":")).encode("utf-8"))

    def _atomic_write(self, target: Path, data: bytes) -> None:
        """Write `data` to `target` via a temp file in the same directory."""
        temp = target.with_name(target.name + _TEMP_SUFFIX)
        try:
            with temp.open("wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, target)
            self._fsync_dir(target.parent)
        except OSError as exc:
            self._remove_if_present(temp)
            raise HttpCacheError(
                f"cannot write {self._cache_noun} cache for {target}; use --refresh to retry"
            ) from exc

    def _fsync_dir(self, directory: Path) -> None:
        """Ensure the directory entry for a just-replaced file is durable."""
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _remove_if_present(self, path: Path) -> None:
        with contextlib.suppress(OSError):
            path.unlink()


def _content_digest(content: str) -> str:
    """Return the digest of the exact UTF-8 bytes the cache stores for `content`."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
