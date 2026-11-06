"""Offline unit tests for the polite HTTP fetcher and its on-disk cache (S5, S7, S8, S11)."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest

from poe2_rag.bootstrap import FORUM_POLICY
from poe2_rag.config import ConfigurationError
from poe2_rag.ingestion.adapters.outbound.http.polite_http_fetcher import (
    POE2DB_POLICY,
    HttpCacheError,
    HttpFetchError,
    HttpRequestError,
    HttpSourcePolicy,
    HttpStatusError,
    HttpTimeoutError,
    MissingHttpContactError,
    PathRule,
    PoliteHttpFetcher,
    RobotsDisallowedError,
    RobotsUnavailableError,
    UnsupportedLocatorError,
)
from poe2_rag.ingestion.application import RawDocument, SourceFetcher

Client = httpx2.Client
ConnectError = httpx2.ConnectError
MockTransport = httpx2.MockTransport
ReadTimeout = httpx2.ReadTimeout
Request = httpx2.Request
Response = httpx2.Response

CONTACT = "ops@example.com"
BASE_URL = "https://poe2db.tw/us/Spark"
OTHER_URL = "https://poe2db.tw/us/IceNova"
ROBOTS_URL = "https://poe2db.tw/robots.txt"

ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /"
ROBOTS_DISALLOW_SPARK = "User-agent: *\nDisallow: /us/Spark"


class RecordingHandler:
    """MockTransport handler that records requests and returns a configured response."""

    __slots__ = ("requests", "routes")

    def __init__(self, routes: dict[str, Response | Exception]) -> None:
        self.routes = routes
        self.requests: list[Request] = []

    def __call__(self, request: Request) -> Response:
        self.requests.append(request)
        url = str(request.url)
        for pattern, response in self.routes.items():
            if url == pattern:
                if isinstance(response, Exception):
                    raise response
                return response
        raise RuntimeError(f"Unexpected request to {url}")


def make_client(handler: RecordingHandler) -> Client:
    return Client(transport=MockTransport(handler))


@pytest.fixture
def unused_client() -> Client:
    """A client that is never actually used by constructor-validation tests."""
    return Client(
        transport=MockTransport(lambda _request: Response(200))
    )


def _noop_sleep(_seconds: float) -> None:
    pass


def make_fetcher(
    client: Client,
    cache_dir: Path,
    *,
    policy: HttpSourcePolicy = POE2DB_POLICY,
    contact: str | None = CONTACT,
    interval: float = 1.0,
    timeout: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = _noop_sleep,
    refresh: bool = False,
) -> PoliteHttpFetcher:
    return PoliteHttpFetcher(
        policy=policy,
        client=client,
        cache_dir=cache_dir,
        contact=contact,
        request_interval=interval,
        timeout=timeout,
        clock=clock,
        sleep=sleep,
        refresh=refresh,
    )


def cache_html_path(cache_dir: Path) -> Path:
    return cache_dir / "poe2db" / "poe2db.tw-us-Spark.html"


def cache_meta_path(cache_dir: Path) -> Path:
    path = cache_html_path(cache_dir)
    return path.with_name(path.name + ".meta.json")


class TestPortAndValidation:
    def test_is_a_source_fetcher(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html></html>"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            assert isinstance(fetcher, SourceFetcher)
            raw = fetcher.fetch(BASE_URL)
            assert isinstance(raw, RawDocument)
            assert raw.source_type == "poe2db"

    def test_missing_contact_raises_configuration_error(
        self, tmp_path: Path, unused_client: Client
    ) -> None:
        with pytest.raises(MissingHttpContactError) as exc_info:
            PoliteHttpFetcher(
                client=unused_client,
                cache_dir=tmp_path,
                contact=None,
                request_interval=1.0,
                timeout=30.0,
            )
        assert "POE2_RAG_HTTP_CONTACT" in str(exc_info.value)
        assert issubclass(MissingHttpContactError, ConfigurationError)

    @pytest.mark.parametrize("contact", ["", "   "])
    def test_blank_contact_raises_configuration_error(
        self, tmp_path: Path, contact: str, unused_client: Client
    ) -> None:
        with pytest.raises(MissingHttpContactError) as exc_info:
            PoliteHttpFetcher(
                client=unused_client,
                cache_dir=tmp_path,
                contact=contact,
                request_interval=1.0,
                timeout=30.0,
            )
        assert "POE2_RAG_HTTP_CONTACT" in str(exc_info.value)

    @pytest.mark.parametrize(
        "url",
        [
            "http://poe2db.tw/us/Spark",
            "https://poe2db.tw/us/",
            "https://poe2db.tw/us/Spark/",
            "https://poe2db.tw/us/Spark?foo=bar",
            "https://poe2db.tw/us/Spark#sec",
            "https://poe2db.tw:8443/us/Spark",
            "https://poe2db.tw:notaport/us/Spark",
            "https://user@poe2db.tw/us/Spark",
            "https://poe2db.tw/gb/Spark",
            "https://other.host/us/Spark",
            "https://poe2db.tw/us/Spark/extra",
            "not-a-url",
            "",
        ],
    )
    def test_rejected_locator_makes_no_requests(self, tmp_path: Path, url: str) -> None:
        handler = RecordingHandler({})
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(UnsupportedLocatorError):
                fetcher.fetch(url)
        assert len(handler.requests) == 0

    def test_a_trailing_slash_is_reported_as_a_trailing_slash(self, tmp_path: Path) -> None:
        handler = RecordingHandler({})
        with make_client(handler) as client:
            with pytest.raises(UnsupportedLocatorError) as exc_info:
                make_fetcher(client, tmp_path).fetch("https://poe2db.tw/us/Spark/")

        assert "trailing slash" in str(exc_info.value)
        assert handler.requests == []


class TestUserAgent:
    def test_user_agent_header_is_sent_on_every_request(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html>Spark</html>"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path, contact=CONTACT)
            fetcher.fetch(BASE_URL)

        assert len(handler.requests) == 2
        for request in handler.requests:
            ua = request.headers.get("User-Agent")
            assert ua is not None
            assert ua.startswith("poe2-rag/")
            assert f"(+{CONTACT})" in ua


class TestRobots:
    def test_robots_disallowed_url_raises_before_fetching_page(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {ROBOTS_URL: Response(200, text=ROBOTS_DISALLOW_SPARK)}
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(RobotsDisallowedError):
                fetcher.fetch(BASE_URL)

        assert len(handler.requests) == 1
        assert str(handler.requests[0].url) == ROBOTS_URL

    def test_robots_fetched_once_per_host_across_two_slugs(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html>Spark</html>"),
                OTHER_URL: Response(200, text="<html>IceNova</html>"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            fetcher.fetch(BASE_URL)
            fetcher.fetch(OTHER_URL)

        robots_requests = [r for r in handler.requests if str(r.url) == ROBOTS_URL]
        page_requests = [r for r in handler.requests if str(r.url) in (BASE_URL, OTHER_URL)]
        assert len(robots_requests) == 1
        assert len(page_requests) == 2

    def test_robots_404_allows_all(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(404, text="not found"),
                BASE_URL: Response(200, text="<html>Spark</html>"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            raw = fetcher.fetch(BASE_URL)

        assert raw.content == "<html>Spark</html>"

    @pytest.mark.parametrize("status", [401, 403])
    def test_robots_401_403_is_disallowed(self, tmp_path: Path, status: int) -> None:
        handler = RecordingHandler({ROBOTS_URL: Response(status, text="forbidden")})
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(RobotsDisallowedError):
                fetcher.fetch(BASE_URL)
        assert len(handler.requests) == 1

    def test_robots_5xx_is_unavailable(self, tmp_path: Path) -> None:
        handler = RecordingHandler({ROBOTS_URL: Response(503, text="down")})
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(RobotsUnavailableError):
                fetcher.fetch(BASE_URL)
        assert len(handler.requests) == 1

    def test_robots_transport_error_is_unavailable(self, tmp_path: Path) -> None:
        handler = RecordingHandler({ROBOTS_URL: ConnectError("refused")})
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(RobotsUnavailableError):
                fetcher.fetch(BASE_URL)
        assert len(handler.requests) == 1


class TestRateLimit:
    def test_sleeps_when_clock_has_not_advanced(self, tmp_path: Path) -> None:
        sleeps: list[float] = []
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="a"),
                OTHER_URL: Response(200, text="b"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(
                client, tmp_path, interval=1.0, clock=lambda: 0.0, sleep=sleeps.append
            )
            fetcher.fetch(BASE_URL)
            fetcher.fetch(OTHER_URL)

        # First page after robots and second page after first page both need a full interval.
        assert sleeps == [1.0, 1.0]

    def test_no_sleep_when_clock_already_advanced(self, tmp_path: Path) -> None:
        sleeps: list[float] = []
        now = [0.0]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            now[0] += seconds

        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="a"),
                OTHER_URL: Response(200, text="b"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(
                client, tmp_path, interval=1.0, clock=lambda: now[0], sleep=sleep
            )
            fetcher.fetch(BASE_URL)
            now[0] += 2.0
            fetcher.fetch(OTHER_URL)

        assert sleeps == [1.0]

    def test_the_interval_is_measured_between_the_actual_requests(self, tmp_path: Path) -> None:
        """A recorded pre-sleep timestamp would let the next page go out right after the first."""
        now = [0.0]
        request_times: list[tuple[str, float]] = []
        routes = {
            ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
            BASE_URL: Response(200, text="Spark"),
            OTHER_URL: Response(200, text="Ice Nova"),
        }

        def handler(request: Request) -> Response:
            url = str(request.url)
            request_times.append((url, now[0]))
            return routes[url]

        def sleep(seconds: float) -> None:
            now[0] += seconds

        with Client(transport=MockTransport(handler)) as client:
            fetcher = make_fetcher(
                client, tmp_path, interval=1.0, clock=lambda: now[0], sleep=sleep
            )
            fetcher.fetch(BASE_URL)
            fetcher.fetch(OTHER_URL)

        assert request_times == [(ROBOTS_URL, 0.0), (BASE_URL, 1.0), (OTHER_URL, 2.0)]


class TestCache:
    def test_cache_miss_writes_html_and_meta_sidecar(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html>Spark</html>"),
            }
        )
        before = datetime.now(UTC)
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            raw = fetcher.fetch(BASE_URL)
        after = datetime.now(UTC)

        html_path = cache_html_path(tmp_path)
        meta_path = cache_meta_path(tmp_path)
        assert html_path.read_text(encoding="utf-8") == "<html>Spark</html>"

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["url"] == BASE_URL
        assert meta["status"] == 200
        assert meta["sha256"] == hashlib.sha256(b"<html>Spark</html>").hexdigest()
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
        assert fetched_at.tzinfo is not None
        assert before <= fetched_at <= after

        assert raw.content == "<html>Spark</html>"
        assert raw.fetched_at == fetched_at

    def test_cache_hit_preserves_crlf_newlines(self, tmp_path: Path) -> None:
        """`read_text` would normalize CRLF and break the digest of every such cached page."""
        content = "<html>\r\n<body>Spark\r\n</body>\r\n</html>"
        first_handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text=content),
            }
        )
        with make_client(first_handler) as client:
            original = make_fetcher(client, tmp_path).fetch(BASE_URL)
        assert original.content == content

        second_handler = RecordingHandler({})
        with make_client(second_handler) as client:
            cached = make_fetcher(client, tmp_path).fetch(BASE_URL)

        assert cached.content == content
        assert cached.fetched_at == original.fetched_at
        assert second_handler.requests == []

    def test_cache_hit_by_second_fetcher_returns_original_fetched_at_with_zero_requests(
        self, tmp_path: Path
    ) -> None:
        first_handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html>Spark</html>"),
            }
        )
        with make_client(first_handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            original = fetcher.fetch(BASE_URL)

        second_handler = RecordingHandler({})
        with make_client(second_handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            cached = fetcher.fetch(BASE_URL)

        assert cached.content == original.content
        assert cached.fetched_at == original.fetched_at
        assert len(second_handler.requests) == 0

    def test_a_slug_with_separators_stays_inside_the_cache_directory(self, tmp_path: Path) -> None:
        """The filename is derived from a URL, so it must never become a path with separators."""
        hostile = "https://poe2db.tw/us/..\\..\\evil"
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                hostile: Response(200, text="<html>hostile</html>"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            fetcher.fetch(hostile)

        cache_directory = tmp_path / "poe2db"
        written = [path for path in cache_directory.iterdir() if path.suffix == ".html"]
        assert len(written) == 1
        assert written[0].parent == cache_directory
        assert "\\" not in written[0].name
        assert written[0].read_text(encoding="utf-8") == "<html>hostile</html>"

    def test_refresh_skips_cache_and_refetches(self, tmp_path: Path) -> None:
        first_handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html>Spark</html>"),
            }
        )
        with make_client(first_handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            original = fetcher.fetch(BASE_URL)

        second_handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html>Spark updated</html>"),
            }
        )
        with make_client(second_handler) as client:
            fetcher = make_fetcher(client, tmp_path, refresh=True)
            refreshed = fetcher.fetch(BASE_URL)

        assert refreshed.content == "<html>Spark updated</html>"
        assert refreshed.fetched_at > original.fetched_at
        assert any(str(r.url) == BASE_URL for r in second_handler.requests)

    @pytest.mark.parametrize(
        "meta_content",
        [None, "{bad", '{"url":"other"}', '{"status":200}', '{"url":"' + BASE_URL + '"}'],
    )
    def test_corrupt_or_missing_sidecar_raises_an_http_cache_error(
        self, tmp_path: Path, meta_content: str | None
    ) -> None:
        html_path = cache_html_path(tmp_path)
        meta_path = cache_meta_path(tmp_path)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text("<html>Spark</html>", encoding="utf-8")
        if meta_content is not None:
            meta_path.write_text(meta_content, encoding="utf-8")

        handler = RecordingHandler({})
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(HttpCacheError) as exc_info:
                fetcher.fetch(BASE_URL)

        assert "--refresh" in str(exc_info.value)
        assert len(handler.requests) == 0

    def test_a_sidecar_whose_digest_does_not_match_its_html_raises(self, tmp_path: Path) -> None:
        """A failed sidecar replace must not pair new HTML with an older capture date."""
        html_path = cache_html_path(tmp_path)
        meta_path = cache_meta_path(tmp_path)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text("<html>Spark updated</html>", encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "url": BASE_URL,
                    "fetched_at": "2026-11-01T12:00:00+00:00",
                    "status": 200,
                    "sha256": hashlib.sha256(b"<html>Spark</html>").hexdigest(),
                }
            ),
            encoding="utf-8",
        )

        handler = RecordingHandler({})
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(HttpCacheError) as exc_info:
                fetcher.fetch(BASE_URL)

        assert "--refresh" in str(exc_info.value)
        assert len(handler.requests) == 0

    def test_a_returned_locator_is_the_canonical_url(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html>Spark</html>"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            raw = fetcher.fetch("https://POE2DB.TW/us/Spark")

        assert raw.locator == BASE_URL

    def test_atomic_write_leaves_no_temp_files_when_replace_fails(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html>Spark</html>"),
            }
        )
        with make_client(handler) as client, patch.object(
            os, "replace", side_effect=OSError("disk full")
        ):
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(HttpCacheError):
                fetcher.fetch(BASE_URL)

        cache_dir = tmp_path / "poe2db"
        assert not any(".tmp" in p.name for p in cache_dir.iterdir())


class TestFetchErrors:
    def test_non_2xx_page_raises_http_status_error(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(503, text="down"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(HttpStatusError) as exc_info:
                fetcher.fetch(BASE_URL)

        assert exc_info.value.status_code == 503
        assert str(exc_info.value.status_code) in str(exc_info.value)
        assert BASE_URL in exc_info.value.url

    def test_timeout_raises_http_timeout_error(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: ReadTimeout("timed out"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(HttpTimeoutError):
                fetcher.fetch(BASE_URL)

    def test_other_transport_error_raises_http_request_error(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: ConnectError("connection refused"),
            }
        )
        with make_client(handler) as client:
            fetcher = make_fetcher(client, tmp_path)
            with pytest.raises(HttpRequestError):
                fetcher.fetch(BASE_URL)


FORUM_THREAD_URL = "https://www.pathofexile.com/forum/view-thread/4009785"
FORUM_ROBOTS_URL = "https://www.pathofexile.com/robots.txt"

POLICY_CASES = [
    pytest.param(
        POE2DB_POLICY,
        BASE_URL,
        "https://www.pathofexile.com/us/Spark",
        "https://poe2db.tw/gb/Spark",
        "https://poe2db.tw/us/Spark/extra",
        id="poe2db",
    ),
    pytest.param(
        FORUM_POLICY,
        FORUM_THREAD_URL,
        "https://poe2db.tw/forum/view-thread/4009785",
        "https://www.pathofexile.com/forum/post-reply/4009785",
        "https://www.pathofexile.com/forum/view-thread/not-a-number",
        id="forum",
    ),
]
"""One row per policy: the policy, a locator it accepts, and locators it must reject.

Every user-visible locator sentence is asserted through the policy that declares it, so the shared
validator cannot change one host's message while the other host's half of the suite stays green.
"""


class TestFetchPolicy:
    """The closed policy declares the rule; the fetcher only applies it (S2)."""

    def test_the_policy_declares_exactly_the_four_fields(self) -> None:
        assert [field.name for field in fields(HttpSourcePolicy)] == [
            "host",
            "path",
            "source_type",
            "cache_directory",
        ]
        assert [field.name for field in fields(PathRule)] == ["prefix", "slug", "slug_message"]

    @pytest.mark.parametrize(
        "segment",
        ["", "   ", "a/b", "a\\b", ".", ".."],
        ids=["blank", "spaces", "slash", "backslash", "dot", "dotdot"],
    )
    def test_a_cache_directory_must_be_one_plain_segment(self, segment: str) -> None:
        """A policy must not be able to write outside the cache root it is given."""
        with pytest.raises(ValueError):
            HttpSourcePolicy(
                host=POE2DB_POLICY.host,
                path=POE2DB_POLICY.path,
                source_type=POE2DB_POLICY.source_type,
                cache_directory=segment,
            )

    @pytest.mark.parametrize(("policy", "url", "foreign_host", "_prefix", "_slug"), POLICY_CASES)
    def test_a_foreign_host_names_the_policy_host(
        self,
        tmp_path: Path,
        policy: HttpSourcePolicy,
        url: str,
        foreign_host: str,
        _prefix: str,
        _slug: str,
    ) -> None:
        handler = RecordingHandler({})
        with make_client(handler) as client, pytest.raises(UnsupportedLocatorError) as exc_info:
            make_fetcher(client, tmp_path, policy=policy).fetch(foreign_host)

        assert f"only {policy.host} is allowed" in str(exc_info.value)
        assert handler.requests == []

    @pytest.mark.parametrize(("policy", "url", "_host", "foreign_path", "_slug"), POLICY_CASES)
    def test_a_foreign_path_names_the_policy_prefix(
        self,
        tmp_path: Path,
        policy: HttpSourcePolicy,
        url: str,
        _host: str,
        foreign_path: str,
        _slug: str,
    ) -> None:
        handler = RecordingHandler({})
        with make_client(handler) as client, pytest.raises(UnsupportedLocatorError) as exc_info:
            make_fetcher(client, tmp_path, policy=policy).fetch(foreign_path)

        assert f"path must start with {policy.path.prefix!r}" in str(exc_info.value)
        assert handler.requests == []

    @pytest.mark.parametrize(("policy", "url", "_host", "_prefix", "_slug"), POLICY_CASES)
    def test_a_trailing_slash_is_rejected_for_every_policy(
        self,
        tmp_path: Path,
        policy: HttpSourcePolicy,
        url: str,
        _host: str,
        _prefix: str,
        _slug: str,
    ) -> None:
        handler = RecordingHandler({})
        with make_client(handler) as client, pytest.raises(UnsupportedLocatorError) as exc_info:
            make_fetcher(client, tmp_path, policy=policy).fetch(url + "/")

        assert "no trailing slash is allowed" in str(exc_info.value)
        assert handler.requests == []

    @pytest.mark.parametrize(("policy", "url", "_host", "_prefix", "bad_slug"), POLICY_CASES)
    def test_a_slug_the_rule_rejects_names_the_rule_message(
        self,
        tmp_path: Path,
        policy: HttpSourcePolicy,
        url: str,
        _host: str,
        _prefix: str,
        bad_slug: str,
    ) -> None:
        handler = RecordingHandler({})
        with make_client(handler) as client, pytest.raises(UnsupportedLocatorError) as exc_info:
            make_fetcher(client, tmp_path, policy=policy).fetch(bad_slug)

        assert policy.path.slug_message in str(exc_info.value)
        assert handler.requests == []

    @pytest.mark.parametrize(("policy", "url", "_host", "_prefix", "_slug"), POLICY_CASES)
    def test_an_accepted_locator_comes_back_canonical(
        self,
        tmp_path: Path,
        policy: HttpSourcePolicy,
        url: str,
        _host: str,
        _prefix: str,
        _slug: str,
    ) -> None:
        handler = RecordingHandler(
            {
                f"https://{policy.host}/robots.txt": Response(200, text=ROBOTS_ALLOW_ALL),
                url: Response(200, text="<html>thread</html>"),
            }
        )
        upper_host = url.replace(f"//{policy.host}", f"//{policy.host.upper()}")
        with make_client(handler) as client:
            raw = make_fetcher(client, tmp_path, policy=policy).fetch(upper_host)

        assert raw.locator == url
        assert raw.source_type == policy.source_type


ACCEPTED_FORUM_URLS = [
    "https://www.pathofexile.com/forum/view-thread/1",
    "https://www.pathofexile.com/forum/view-thread/42",
    "https://www.pathofexile.com/forum/view-thread/123456",
    "https://www.pathofexile.com/forum/view-thread/4009785",
]
"""Thread ids of every length the forum serves today, so the rule is digits and not a width."""

REJECTED_FORUM_URLS = [
    "http://www.pathofexile.com/forum/view-thread/1",
    "https://pathofexile.com/forum/view-thread/1",
    "https://www.pathofexile.com:8443/forum/view-thread/1",
    "https://user@www.pathofexile.com/forum/view-thread/1",
    "https://www.pathofexile.com/forum/view-thread/1?page=2",
    "https://www.pathofexile.com/forum/view-thread/1#post-2",
    "https://www.pathofexile.com/forum/view-thread/1/",
    "https://www.pathofexile.com/forum/view-thread/",
    "https://www.pathofexile.com/forum/view-thread/not-a-number",
    "https://www.pathofexile.com/forum/view-thread/1/extra",
    "https://www.pathofexile.com/forum/post-reply/1",
    "https://poe2db.tw/us/Spark",
]
"""Every way out of `/forum/view-thread/<digits>`, including the two neighbouring forum routes."""


class TestForumPolicy:
    """The forum rule is the second closed policy: only thread URLs under the staff forum (S2)."""

    @pytest.mark.parametrize("url", ACCEPTED_FORUM_URLS)
    def test_accepted_thread_urls_are_fetched_and_kept_as_given(
        self, tmp_path: Path, url: str
    ) -> None:
        handler = RecordingHandler(
            {
                FORUM_ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                url: Response(200, text="<html>thread</html>"),
            }
        )
        with make_client(handler) as client:
            raw = make_fetcher(client, tmp_path, policy=FORUM_POLICY).fetch(url)

        assert raw.locator == url
        assert raw.content == "<html>thread</html>"
        assert raw.source_type == FORUM_POLICY.source_type

    @pytest.mark.parametrize("url", REJECTED_FORUM_URLS)
    def test_rejected_locators_make_no_requests(self, tmp_path: Path, url: str) -> None:
        handler = RecordingHandler({})
        with make_client(handler) as client, pytest.raises(UnsupportedLocatorError):
            make_fetcher(client, tmp_path, policy=FORUM_POLICY).fetch(url)

        assert handler.requests == []

    def test_the_thread_is_cached_under_its_own_segment(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                FORUM_ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                FORUM_THREAD_URL: Response(200, text="<html>thread</html>"),
            }
        )
        with make_client(handler) as client:
            make_fetcher(client, tmp_path, policy=FORUM_POLICY).fetch(FORUM_THREAD_URL)

        written = tmp_path / "patch_notes" / "www.pathofexile.com-forum-view-thread-4009785.html"
        assert written.read_text(encoding="utf-8") == "<html>thread</html>"
        assert written.with_name(written.name + ".meta.json").is_file()
        assert not (tmp_path / "poe2db").exists()

    def test_a_forum_cache_error_names_its_own_source_and_never_poe2db(
        self, tmp_path: Path
    ) -> None:
        html_path = tmp_path / "patch_notes" / "www.pathofexile.com-forum-view-thread-4009785.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text("<html>thread</html>", encoding="utf-8")
        html_path.with_name(html_path.name + ".meta.json").write_text(
            '{"url":"other"}', encoding="utf-8"
        )

        handler = RecordingHandler({})
        with make_client(handler) as client, pytest.raises(HttpCacheError) as exc_info:
            make_fetcher(client, tmp_path, policy=FORUM_POLICY).fetch(FORUM_THREAD_URL)

        message = str(exc_info.value)
        assert message.startswith("patch-notes cache sidecar is malformed for ")
        assert FORUM_THREAD_URL in message
        assert "poe2db" not in message
        assert handler.requests == []

    def test_a_failed_forum_cache_write_names_patch_notes(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                FORUM_ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                FORUM_THREAD_URL: Response(200, text="<html>thread</html>"),
            }
        )
        with (
            make_client(handler) as client,
            patch.object(os, "replace", side_effect=OSError("disk full")),
            pytest.raises(HttpCacheError) as exc_info,
        ):
            make_fetcher(client, tmp_path, policy=FORUM_POLICY).fetch(FORUM_THREAD_URL)

        assert "cannot write patch-notes cache" in str(exc_info.value)


class TestCacheMessages:
    """The cache sentences are the operator's only remediation hint on a one-line exit 1."""

    def test_an_unreadable_cache_names_poe2db(self, tmp_path: Path) -> None:
        html_path = cache_html_path(tmp_path)
        meta_path = cache_meta_path(tmp_path)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text("<html>Spark</html>", encoding="utf-8")
        meta_path.write_text("{bad", encoding="utf-8")

        handler = RecordingHandler({})
        with make_client(handler) as client, pytest.raises(HttpCacheError) as exc_info:
            make_fetcher(client, tmp_path).fetch(BASE_URL)

        message = str(exc_info.value)
        assert message.startswith("poe2db cache is unreadable for ")
        assert BASE_URL in message
        assert "--refresh" in message

    def test_a_malformed_sidecar_names_poe2db(self, tmp_path: Path) -> None:
        html_path = cache_html_path(tmp_path)
        meta_path = cache_meta_path(tmp_path)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text("<html>Spark</html>", encoding="utf-8")
        meta_path.write_text(json.dumps({"url": "other"}), encoding="utf-8")

        handler = RecordingHandler({})
        with make_client(handler) as client, pytest.raises(HttpCacheError) as exc_info:
            make_fetcher(client, tmp_path).fetch(BASE_URL)

        message = str(exc_info.value)
        assert message.startswith("poe2db cache sidecar is malformed for ")
        assert "--refresh" in message

    def test_a_failed_write_names_poe2db(self, tmp_path: Path) -> None:
        handler = RecordingHandler(
            {
                ROBOTS_URL: Response(200, text=ROBOTS_ALLOW_ALL),
                BASE_URL: Response(200, text="<html>Spark</html>"),
            }
        )
        with (
            make_client(handler) as client,
            patch.object(os, "replace", side_effect=OSError("disk full")),
            pytest.raises(HttpCacheError) as exc_info,
        ):
            make_fetcher(client, tmp_path).fetch(BASE_URL)

        assert "cannot write poe2db cache" in str(exc_info.value)


class TestErrorHierarchy:
    @pytest.mark.parametrize(
        "cls",
        [
            UnsupportedLocatorError,
            RobotsDisallowedError,
            RobotsUnavailableError,
            HttpStatusError,
            HttpTimeoutError,
            HttpRequestError,
            HttpCacheError,
        ],
    )
    def test_all_http_fetch_errors_derive_from_runtime_error(self, cls: type[Exception]) -> None:
        assert issubclass(cls, HttpFetchError)
        assert issubclass(cls, RuntimeError)
