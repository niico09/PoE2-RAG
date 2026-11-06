"""Dispatch a patch-notes locator to the local branch or to the polite HTTP fetcher (S1, S4, S6)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import httpx2
import pytest

from poe2_rag.bootstrap import FORUM_POLICY, _RawDirectoryFetcher
from poe2_rag.ingestion.adapters.locator import LocatorKind
from poe2_rag.ingestion.adapters.outbound.http.polite_http_fetcher import (
    PoliteHttpFetcher,
)
from poe2_rag.ingestion.adapters.outbound.json_chunk_repository import (
    JsonChunkRepository,
)
from poe2_rag.ingestion.adapters.outbound.local_file_fetcher import (
    METADATA_SUFFIX,
    LocalFileFetcher,
)
from poe2_rag.ingestion.adapters.outbound.locator_fetching import LocatorFetcher
from poe2_rag.ingestion.adapters.outbound.patch_notes_parser import (
    PatchNotesParser,
)
from poe2_rag.ingestion.application import IngestSource, RawDocument, SourceFetcher
from poe2_rag.shared.domain import (
    InMemoryPatchCatalog,
    PatchRelease,
    PatchVersion,
)

FIXTURES = Path(__file__).parents[4] / "fixtures" / "patch_notes"
THREAD_ID = "7654321"
THREAD_URL = f"https://www.pathofexile.com/forum/view-thread/{THREAD_ID}"
ROBOTS_URL = "https://www.pathofexile.com/robots.txt"
ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /"
CONTACT = "ops@example.com"
INTERVAL = 1.0
TIMEOUT = 30.0

DISPATCH_CASES = [
    pytest.param(
        "https://www.pathofexile.com/forum/view-thread/1",
        LocatorKind.HTTP,
        id="https-url",
    ),
    pytest.param(
        "http://example.invalid/x",
        LocatorKind.HTTP,
        id="http-url",
    ),
    pytest.param(
        "ftp://x",
        LocatorKind.OTHER_SCHEME,
        id="other-scheme",
    ),
    pytest.param(
        "https:/x",
        LocatorKind.HTTP,
        id="scheme-without-netloc-is-still-http",
    ),
    pytest.param(
        "data/raw/patch-notes-0.1.1.html",
        LocatorKind.PATH,
        id="relative-path",
    ),
    pytest.param(
        "/opt/snapshots/x.html",
        LocatorKind.PATH,
        id="absolute-path",
    ),
]
"""One locator per `(kind, branch)` decision the dispatcher makes.

`test_locator.py` owns the syntax table that classifies every locator shape; this one only decides
which branch each kind reaches, so the two lists answer different questions instead of duplicating
one.
"""


class _RecordingFetcher:
    """SourceFetcher fake that records every ``fetch`` call."""

    __slots__ = ("_document", "calls")

    def __init__(self, document: RawDocument | None = None) -> None:
        self._document = document
        self.calls: list[str] = []

    def fetch(self, locator: str) -> RawDocument:
        self.calls.append(locator)
        if self._document is not None:
            return self._document
        return RawDocument(
            locator=locator,
            content="",
            fetched_at=datetime.now(UTC),
            source_type="patch_notes",
        )


def _make_http_factory(
    fetcher: _RecordingFetcher | None = None,
) -> tuple[Callable[[], _RecordingFetcher], list[_RecordingFetcher]]:
    """Return a factory and a list that receives every fetcher it built."""
    built: list[_RecordingFetcher] = []

    def factory() -> _RecordingFetcher:
        instance = fetcher if fetcher is not None else _RecordingFetcher()
        built.append(instance)
        return instance

    return factory, built


@pytest.mark.parametrize(("locator", "kind"), DISPATCH_CASES)
def test_dispatches_to_the_branch_matching_the_locator_kind(
    locator: str, kind: LocatorKind
) -> None:
    local = _RecordingFetcher()
    http_factory, built = _make_http_factory()
    dispatcher = LocatorFetcher(local=local, http=http_factory)

    raw = dispatcher.fetch(locator)

    if kind is LocatorKind.PATH:
        assert raw.locator == locator
        assert local.calls == [locator]
        assert built == []
    else:
        assert local.calls == []
        assert len(built) == 1
        assert built[0].calls == [locator]
        assert raw.locator == locator


def test_is_a_source_fetcher() -> None:
    assert isinstance(
        LocatorFetcher(local=_RecordingFetcher(), http=lambda: _RecordingFetcher()),
        SourceFetcher,
    )


def test_http_factory_is_built_once_and_memoized() -> None:
    local = _RecordingFetcher()
    http_factory, built = _make_http_factory()
    dispatcher = LocatorFetcher(local=local, http=http_factory)

    first = dispatcher.fetch("https://www.pathofexile.com/forum/view-thread/1")
    second = dispatcher.fetch("https://www.pathofexile.com/forum/view-thread/2")

    assert len(built) == 1
    assert built[0].calls == [
        "https://www.pathofexile.com/forum/view-thread/1",
        "https://www.pathofexile.com/forum/view-thread/2",
    ]
    assert local.calls == []
    assert first is not second
    assert first.locator != second.locator


def test_raw_directory_wrapper_resolves_paths_but_never_prefixes_a_url(
    tmp_path: Path,
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    snapshot = raw_dir / "patch-notes-0.1.0.html"
    snapshot.write_text("<html><h1>0.1.0 Patch Notes</h1></html>", encoding="utf-8")
    snapshot.with_name(snapshot.name + METADATA_SUFFIX).write_text(
        '{"captured_at": "2026-09-21T12:00:00+00:00"}', encoding="utf-8"
    )

    absolute_path = tmp_path / "absolute" / "patch-notes-0.1.0.html"
    absolute_path.parent.mkdir()
    absolute_path.write_text("<html><h1>absolute</h1></html>", encoding="utf-8")
    absolute_path.with_name(absolute_path.name + METADATA_SUFFIX).write_text(
        '{"captured_at": "2026-09-21T12:00:00+00:00"}', encoding="utf-8"
    )

    routes = {
        ROBOTS_URL: httpx2.Response(200, text=ROBOTS_ALLOW_ALL),
        THREAD_URL: httpx2.Response(200, text="<html><h1>url</h1></html>"),
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return routes[str(request.url)]

    def http_factory() -> SourceFetcher:
        return PoliteHttpFetcher(
            policy=FORUM_POLICY,
            client=httpx2.Client(transport=httpx2.MockTransport(handler)),
            cache_dir=tmp_path / "cache",
            contact=CONTACT,
            request_interval=INTERVAL,
            timeout=TIMEOUT,
        )

    dispatcher = LocatorFetcher(
        local=_RawDirectoryFetcher(raw_dir, LocalFileFetcher()),
        http=http_factory,
    )

    relative_raw = dispatcher.fetch("patch-notes-0.1.0.html")
    assert relative_raw.locator == str(raw_dir / "patch-notes-0.1.0.html")
    assert "0.1.0 Patch Notes" in relative_raw.content

    absolute_raw = dispatcher.fetch(str(absolute_path))
    assert absolute_raw.locator == str(absolute_path)
    assert "absolute" in absolute_raw.content

    url_raw = dispatcher.fetch(THREAD_URL)
    assert url_raw.locator == THREAD_URL
    assert "url" in url_raw.content


def _catalog_for(version: PatchVersion) -> InMemoryPatchCatalog:
    return InMemoryPatchCatalog(
        [PatchRelease(version, date(2026, 9, 21))],
        version,
    )


def _run_pipeline(
    fetcher: SourceFetcher,
    locator: str,
    repository_dir: Path,
    catalog: InMemoryPatchCatalog,
) -> JsonChunkRepository:
    repository = JsonChunkRepository(repository_dir)
    source = IngestSource(
        fetcher=fetcher,
        parser=PatchNotesParser(),
        repository=repository,
        catalog=catalog,
    )
    source.execute(locator)
    return repository


def test_url_and_path_runs_produce_identical_chunks_for_the_same_snapshot(
    tmp_path: Path,
) -> None:
    """S4 parity: the only inputs the transport can move are source.type and source.url."""
    fixture = FIXTURES / "full_020_thread.html"
    version = PatchVersion.parse("0.2.0")
    catalog = _catalog_for(version)

    # Local run over a snapshot with a capture sidecar.
    local_root = tmp_path / "local_raw"
    local_root.mkdir()
    local_snapshot = local_root / "full_020_thread.html"
    shutil.copyfile(fixture, local_snapshot)
    local_snapshot.with_name(local_snapshot.name + METADATA_SUFFIX).write_text(
        '{"captured_at": "2026-09-21T12:00:00+00:00"}', encoding="utf-8"
    )
    local_repository = tmp_path / "local_processed"
    local_repository_obj = _run_pipeline(
        fetcher=LocalFileFetcher(),
        locator=str(local_snapshot),
        repository_dir=local_repository,
        catalog=catalog,
    )
    local_chunks = tuple(local_repository_obj.all())

    # HTTP run over the same fixture served by a fake transport.
    http_repository = tmp_path / "http_processed"
    http_cache = tmp_path / "http_cache"

    routes = {
        ROBOTS_URL: httpx2.Response(200, text=ROBOTS_ALLOW_ALL),
        THREAD_URL: httpx2.Response(200, text=fixture.read_text(encoding="utf-8")),
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return routes[str(request.url)]

    def http_factory() -> SourceFetcher:
        return PoliteHttpFetcher(
            policy=FORUM_POLICY,
            client=httpx2.Client(transport=httpx2.MockTransport(handler)),
            cache_dir=http_cache,
            contact=CONTACT,
            request_interval=INTERVAL,
            timeout=TIMEOUT,
        )

    http_repository_obj = _run_pipeline(
        fetcher=LocatorFetcher(
            local=_RawDirectoryFetcher(local_root, LocalFileFetcher()),
            http=http_factory,
        ),
        locator=THREAD_URL,
        repository_dir=http_repository,
        catalog=catalog,
    )
    http_chunks = tuple(http_repository_obj.all())

    assert tuple(chunk.chunk_id for chunk in local_chunks) == tuple(
        chunk.chunk_id for chunk in http_chunks
    )
    for local_chunk, http_chunk in zip(local_chunks, http_chunks, strict=True):
        assert local_chunk.source.type == http_chunk.source.type
        assert local_chunk.source.url == http_chunk.source.url
        assert local_chunk.validity.valid_from == http_chunk.validity.valid_from == version


def test_captured_at_is_the_fetched_date_for_a_fresh_fetch_and_a_cache_hit(
    tmp_path: Path,
) -> None:
    """S1/S4: `captured_at` comes from the fetch, never from the clock of a later read."""
    fixture = FIXTURES / "full_020_thread.html"
    version = PatchVersion.parse("0.2.0")
    catalog = _catalog_for(version)
    cache_dir = tmp_path / "cache"

    routes = {
        ROBOTS_URL: httpx2.Response(200, text=ROBOTS_ALLOW_ALL),
        THREAD_URL: httpx2.Response(200, text=fixture.read_text(encoding="utf-8")),
    }

    def live_handler(request: httpx2.Request) -> httpx2.Response:
        return routes[str(request.url)]

    def raising_handler(_request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("a cache hit must not touch the network")

    def http_fetcher(handler: Callable[[httpx2.Request], httpx2.Response]) -> SourceFetcher:
        return PoliteHttpFetcher(
            policy=FORUM_POLICY,
            client=httpx2.Client(transport=httpx2.MockTransport(handler)),
            cache_dir=cache_dir,
            contact=CONTACT,
            request_interval=INTERVAL,
            timeout=TIMEOUT,
        )

    def dispatcher(handler: Callable[[httpx2.Request], httpx2.Response]) -> LocatorFetcher:
        return LocatorFetcher(
            local=_RawDirectoryFetcher(tmp_path / "raw", LocalFileFetcher()),
            http=lambda: http_fetcher(handler),
        )

    fresh_repository = _run_pipeline(
        fetcher=dispatcher(live_handler),
        locator=THREAD_URL,
        repository_dir=tmp_path / "processed",
        catalog=catalog,
    )
    sidecar = (
        cache_dir / "patch_notes" / "www.pathofexile.com-forum-view-thread-7654321.html.meta.json"
    )
    fetched_at = datetime.fromisoformat(
        json.loads(sidecar.read_text(encoding="utf-8"))["fetched_at"]
    )
    assert {chunk.source.captured_at for chunk in fresh_repository.all()} == {fetched_at.date()}

    # A second run reads the cache: the capture date is the original fetch, not the read moment.
    cached_repository = _run_pipeline(
        fetcher=dispatcher(raising_handler),
        locator=THREAD_URL,
        repository_dir=tmp_path / "processed-cached",
        catalog=catalog,
    )
    assert {chunk.source.captured_at for chunk in cached_repository.all()} == {fetched_at.date()}
